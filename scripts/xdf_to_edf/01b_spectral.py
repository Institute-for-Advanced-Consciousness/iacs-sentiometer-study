"""Step 1b — spectral diagnostic on the clean mid-recording window.

Produces the PSD plot, PSD CSV, and line-noise report that ride along
with the EDF+ handoff to Paller. The reasoning for picking t=300 s as
the analysis anchor is that the first 120 s contain the
peripheral-electrode saturation documented in Step 1 — we want a
window past that, ideally on a quiet resting segment. We do NOT
re-inspect the first 120 s here (Step 1 already covered it).

Outputs written under ``outputs/diagnostics/``:

* ``psd_by_modality.png`` — per-channel PSD (1-100 Hz) for Cz, Fp1,
  the two EOG channels (ExGa 1/2), and the two chin EMG channels
  (ExGa 3/4).
* ``psd_data.csv`` — the same PSDs as wide CSV (one row per frequency
  bin, one column per channel).
* ``line_noise_report.txt`` — per-channel 60 / 120 / 180 Hz peak-to-
  baseline SNR in dB, plus the EXG high-frequency broadband ratio
  (30-100 Hz vs 1-30 Hz) and an impedance-metadata check.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import matplotlib
import numpy as np
import pyxdf
from scipy import signal

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from _common import (
    REPO_ROOT,
    SAMPLE_DIR,
    diag_dir_for,
    find_xdf as _find_xdf_common,
    subject_from_xdf,
)

FS_EXPECTED = 500.0
SEG_START_S = 300.0
SEG_DURATION_S = 60.0
WELCH_WIN_S = 4.0
WELCH_OVERLAP = 0.5

# CGX ExGa → AASM name mapping (matches the spec passed from the user).
CGX_EXG_DISPLAY = {
    "ExGa 1": "E1-M2 (EOG-L)",
    "ExGa 2": "E2-M1 (EOG-R)",
    "ExGa 3": "ChinZ-Chin1 (EMG-primary)",
    "ExGa 4": "Chin2-Chin1prime (EMG-backup)",
}

# Channel selection for the PSD figure.
EEG_TARGETS = ("Cz", "Fp1")
CGX_TARGETS = ("ExGa 1", "ExGa 2", "ExGa 3", "ExGa 4")


def _get(d: object, *keys: str, default: object = "") -> object:
    cur = d
    for k in keys:
        if isinstance(cur, list) and cur and isinstance(cur[0], dict):
            cur = cur[0]
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k, default)
    if isinstance(cur, list) and cur and isinstance(cur[0], str):
        return cur[0]
    return cur


def _channel_labels(stream: dict) -> list[str]:
    info = stream.get("info") or {}
    desc = info.get("desc") or []
    desc0 = desc[0] if isinstance(desc, list) and desc else {}
    if not isinstance(desc0, dict):
        return []
    chs = desc0.get("channels") or []
    chs0 = chs[0] if isinstance(chs, list) and chs else {}
    if not isinstance(chs0, dict):
        return []
    entries = chs0.get("channel") or []
    if not isinstance(entries, list):
        entries = [entries]
    labels: list[str] = []
    for ch in entries:
        if not isinstance(ch, dict):
            continue
        lab = ch.get("label") or [""]
        labels.append(lab[0] if isinstance(lab, list) else str(lab))
    return labels


def _find_xdf() -> Path:
    return _find_xdf_common()


def _slice_segment(stream: dict, start_s: float, dur_s: float) -> tuple[np.ndarray, np.ndarray]:
    """Return (timestamps_relative, data) for the window
    [t0+start_s, t0+start_s+dur_s] where t0 is this stream's first sample."""
    ts_raw = stream.get("time_stamps")
    ts = np.asarray(ts_raw if ts_raw is not None else [], dtype=float)
    if ts.size == 0:
        return np.array([]), np.array([])
    t0 = float(ts[0])
    lo = t0 + start_s
    hi = t0 + start_s + dur_s
    mask = (ts >= lo) & (ts < hi)
    data_raw = stream.get("time_series")
    data = np.asarray(data_raw if data_raw is not None else [], dtype=float)
    return ts[mask] - t0, data[mask, :] if data.ndim == 2 else data[mask]


def _welch(x: np.ndarray, fs: float) -> tuple[np.ndarray, np.ndarray]:
    nperseg = int(fs * WELCH_WIN_S)
    noverlap = int(nperseg * WELCH_OVERLAP)
    f, pxx = signal.welch(
        x, fs=fs, nperseg=nperseg, noverlap=noverlap, scaling="density"
    )
    return f, pxx


def _line_peak_snr_db(f: np.ndarray, pxx: np.ndarray, target_hz: float,
                     half_bw: float = 1.0, baseline_bw: float = 4.0) -> float:
    """dB ratio of peak power in [target-half_bw, target+half_bw] to the
    mean power in flanking baseline bands of width baseline_bw."""
    peak_band = (f >= target_hz - half_bw) & (f <= target_hz + half_bw)
    low_band = (f >= target_hz - baseline_bw - half_bw) & (f < target_hz - half_bw)
    high_band = (f > target_hz + half_bw) & (f <= target_hz + baseline_bw + half_bw)
    if peak_band.sum() == 0 or (low_band.sum() + high_band.sum()) == 0:
        return float("nan")
    peak = float(np.max(pxx[peak_band]))
    base = float(np.mean(pxx[low_band | high_band]))
    if base <= 0 or peak <= 0:
        return float("nan")
    return 10.0 * np.log10(peak / base)


def _band_power(f: np.ndarray, pxx: np.ndarray, lo: float, hi: float) -> float:
    mask = (f >= lo) & (f < hi)
    if mask.sum() < 2:
        return float("nan")
    return float(np.trapz(pxx[mask], f[mask]))


def _impedance_snapshot(streams: list[dict]) -> list[str]:
    """Return human-readable lines summarising impedance info we could find
    in the XDF — either per-channel values inside stream info XML, or the
    dedicated CGX Impedance stream."""
    lines: list[str] = []
    for s in streams:
        name = _get(s, "info", "name")
        stype = _get(s, "info", "type")
        if str(stype).lower() != "impeadance":  # CGX typo
            continue
        data_raw = s.get("time_series")
        data = np.asarray(data_raw if data_raw is not None else [], dtype=float)
        labels = _channel_labels(s)
        if data.size == 0 or not labels:
            continue
        lines.append(f"CGX Impedance stream: {name}")
        lines.append("  mean impedance per channel across full recording:")
        means = np.nanmean(data, axis=0) if data.ndim == 2 else np.array([])
        for lab, m in zip(labels, means):
            lines.append(f"    {lab:<22} {m:.3f} kΩ")
        lines.append("")
    # Check BrainVision EEG info XML for pre-recording impedance entries.
    for s in streams:
        if _get(s, "info", "name") == "BrainAmpSeries-Dev_1":
            info_dump = str(s.get("info", {}))
            if "impedance" in info_dump.lower():
                lines.append(
                    "BrainAmpSeries info XML contains 'impedance' substring — "
                    "check stream info XML (not extracted here)."
                )
            else:
                lines.append(
                    "BrainAmpSeries info XML: no impedance metadata found. "
                    "Pre-recording actiCAP impedance values were not logged "
                    "into the XDF for this session."
                )
    return lines


def main() -> int:
    xdf_path = _find_xdf()
    subject = subject_from_xdf(xdf_path)
    diag_dir = diag_dir_for(subject)
    print(f"Subject: {subject}")
    print(f"Loading {xdf_path} …")
    streams, _ = pyxdf.load_xdf(str(xdf_path))

    eeg = next(
        (s for s in streams if _get(s, "info", "name") == "BrainAmpSeries-Dev_1"),
        None,
    )
    cgx = next(
        (s for s in streams
         if _get(s, "info", "name") == "CGX AIM Phys. Mon. AIM-0106"),
        None,
    )
    if eeg is None or cgx is None:
        raise SystemExit("Missing expected EEG or CGX stream in XDF.")

    eeg_labels = _channel_labels(eeg)
    cgx_labels = _channel_labels(cgx)

    eeg_rel, eeg_seg = _slice_segment(eeg, SEG_START_S, SEG_DURATION_S)
    cgx_rel, cgx_seg = _slice_segment(cgx, SEG_START_S, SEG_DURATION_S)
    if eeg_seg.size == 0 or cgx_seg.size == 0:
        raise SystemExit(
            f"Chosen window [t0+{SEG_START_S}, +{SEG_START_S+SEG_DURATION_S}] s "
            "has no samples in one of the streams."
        )
    print(f"  EEG segment: {eeg_seg.shape[0]} samples "
          f"(effective fs ≈ {eeg_seg.shape[0]/SEG_DURATION_S:.2f} Hz)")
    print(f"  CGX segment: {cgx_seg.shape[0]} samples "
          f"(effective fs ≈ {cgx_seg.shape[0]/SEG_DURATION_S:.2f} Hz)")

    # ----- Welch PSD per target channel ------------------------------------
    psd_table: dict[str, tuple[np.ndarray, np.ndarray, str]] = {}
    for lbl in EEG_TARGETS:
        if lbl not in eeg_labels:
            continue
        j = eeg_labels.index(lbl)
        f, p = _welch(eeg_seg[:, j], FS_EXPECTED)
        psd_table[lbl] = (f, p, "EEG (BrainVision)")
    for lbl in CGX_TARGETS:
        if lbl not in cgx_labels:
            continue
        j = cgx_labels.index(lbl)
        f, p = _welch(cgx_seg[:, j], FS_EXPECTED)
        display = CGX_EXG_DISPLAY.get(lbl, lbl)
        psd_table[display] = (f, p, "CGX ExG (AASM rename)")

    # ----- Plot -------------------------------------------------------------
    fig, axes = plt.subplots(
        len(psd_table), 1,
        figsize=(10, 2.8 * len(psd_table)),  # taller rows for readability
        sharex=True,
    )
    if len(psd_table) == 1:
        axes = [axes]
    for ax, (display, (f, p, kind)) in zip(axes, psd_table.items()):
        ax.semilogy(f, p, linewidth=0.9)
        ax.set_xlim(0.5, 100)
        ax.set_ylabel("PSD\n(µV²/Hz)")
        ax.set_title(f"{display}   [{kind}]", fontsize=9)
        ax.axvspan(58, 62, color="red", alpha=0.08)
        ax.axvspan(118, 122, color="red", alpha=0.06)
        ax.grid(alpha=0.3, which="both")
    axes[-1].set_xlabel("Frequency (Hz)")
    fig.suptitle(
        f"PSD — Welch, 4 s windows, 50 % overlap, {SEG_DURATION_S:.0f} s "
        f"segment starting at t = {SEG_START_S:.0f} s"
    )
    fig.tight_layout()
    png_path = diag_dir /"psd_by_modality.png"
    fig.savefig(png_path, dpi=140)
    plt.close(fig)
    print(f"wrote {png_path}")

    # ----- CSV --------------------------------------------------------------
    csv_path = diag_dir /"psd_data.csv"
    # All PSDs should share frequency axis since FS + nperseg are the same.
    freq_axis = next(iter(psd_table.values()))[0]
    with open(csv_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["frequency_hz"] + list(psd_table.keys()))
        for i, f in enumerate(freq_axis):
            row = [f"{f:.4f}"]
            for display in psd_table:
                row.append(f"{psd_table[display][1][i]:.6e}")
            w.writerow(row)
    print(f"wrote {csv_path}")

    # ----- Line noise per channel (60 / 120 / 180 Hz) ----------------------
    report_lines: list[str] = [
        "Line-noise + broadband report",
        "=" * 60,
        f"XDF            : {xdf_path}",
        f"Segment window : [t0+{SEG_START_S:.0f}, +{SEG_START_S+SEG_DURATION_S:.0f}] s",
        f"Sample rate    : {FS_EXPECTED} Hz",
        f"Welch          : {WELCH_WIN_S:.1f} s / {WELCH_OVERLAP*100:.0f}% overlap",
        "",
        f"Per-channel peak-to-baseline SNR (dB) at 60, 120, 180 Hz.",
        "Baseline = mean PSD over ±4 Hz flanks, excluding ±1 Hz peak band.",
        f"Channels with any-line SNR > 10 dB are flagged.",
        "",
    ]

    def _summarize_psds(name: str, data_seg: np.ndarray, labels: list[str]) -> None:
        report_lines.append(f"--- {name} ---")
        report_lines.append(
            f"{'channel':<22} {'60Hz dB':>9} {'120Hz dB':>9} {'180Hz dB':>9}  flag"
        )
        all_snrs: list[list[float]] = []
        for j, lab in enumerate(labels):
            if j >= data_seg.shape[1]:
                continue
            f_ch, p_ch = _welch(data_seg[:, j], FS_EXPECTED)
            snrs = [
                _line_peak_snr_db(f_ch, p_ch, 60),
                _line_peak_snr_db(f_ch, p_ch, 120),
                _line_peak_snr_db(f_ch, p_ch, 180),
            ]
            all_snrs.append(snrs)
            good = [s for s in snrs if not np.isnan(s)]
            flag = "**" if good and max(good) > 10 else "  "
            report_lines.append(
                f"{lab:<22} {snrs[0]:>9.2f} {snrs[1]:>9.2f} {snrs[2]:>9.2f}  {flag}"
            )
        # Aggregate summary
        snr_arr = np.asarray(all_snrs)
        if snr_arr.size:
            med_60 = float(np.nanmedian(snr_arr[:, 0]))
            report_lines.append(
                f"  median 60 Hz SNR across {snr_arr.shape[0]} channels: "
                f"{med_60:.2f} dB"
            )

    _summarize_psds("BrainVision EEG (64 channels)", eeg_seg, eeg_labels)
    report_lines.append("")
    _summarize_psds("CGX AIM-2 (13 channels)", cgx_seg, cgx_labels)

    # ----- Broadband ratio on EXG channels (30-100 vs 1-30 Hz) -------------
    report_lines.append("")
    report_lines.append(
        "EXG high-frequency broadband power ratio  (30-100 Hz / 1-30 Hz)"
    )
    report_lines.append(
        "Expected HIGH on chin EMG (EMG band dominates); "
        "expected LOW on EOG (slow eye movements dominate)."
    )
    report_lines.append(
        f"{'channel':<30} {'ratio':>10}  interpretation"
    )
    for lbl in CGX_TARGETS:
        if lbl not in cgx_labels:
            continue
        j = cgx_labels.index(lbl)
        f_ch, p_ch = _welch(cgx_seg[:, j], FS_EXPECTED)
        p_hi = _band_power(f_ch, p_ch, 30, 100)
        p_lo = _band_power(f_ch, p_ch, 1, 30)
        ratio = p_hi / p_lo if p_lo > 0 else float("nan")
        display = CGX_EXG_DISPLAY.get(lbl, lbl)
        expected_hi = "EMG" in display
        note = (
            "OK (EMG)" if expected_hi and ratio > 0.3
            else "LOW — EMG activity quiet / participant relaxed"
            if expected_hi else
            "OK (EOG)" if not expected_hi and ratio < 0.3
            else "HIGH — possible contamination / line noise"
        )
        report_lines.append(
            f"{display:<30} {ratio:>10.3f}  {note}"
        )

    # EEG average vs EXG average 60 Hz comparison (grounding hint)
    report_lines.append("")
    report_lines.append("EEG vs CGX average 60 Hz peak SNR (grounding comparison):")
    eeg_60 = []
    for j in range(eeg_seg.shape[1]):
        f_ch, p_ch = _welch(eeg_seg[:, j], FS_EXPECTED)
        v = _line_peak_snr_db(f_ch, p_ch, 60)
        if not np.isnan(v):
            eeg_60.append(v)
    cgx_60 = []
    for j in range(cgx_seg.shape[1]):
        f_ch, p_ch = _welch(cgx_seg[:, j], FS_EXPECTED)
        v = _line_peak_snr_db(f_ch, p_ch, 60)
        if not np.isnan(v):
            cgx_60.append(v)
    if eeg_60 and cgx_60:
        eeg_m = float(np.median(eeg_60))
        cgx_m = float(np.median(cgx_60))
        report_lines.append(
            f"  EEG median 60 Hz SNR : {eeg_m:.2f} dB  "
            f"({len(eeg_60)} channels)"
        )
        report_lines.append(
            f"  CGX median 60 Hz SNR : {cgx_m:.2f} dB  "
            f"({len(cgx_60)} channels)"
        )
        if cgx_m > eeg_m + 5:
            report_lines.append(
                "  -> CGX is materially noisier at line frequency than "
                "EEG. Possible AIM-2 grounding / reference issue worth "
                "checking on the bench."
            )
        elif eeg_m > cgx_m + 5:
            report_lines.append(
                "  -> EEG is materially noisier at line frequency. "
                "Check cap ground (AFz) and amplifier shielding."
            )
        else:
            report_lines.append(
                "  -> Comparable 60 Hz pickup on both amplifiers — "
                "likely environmental, not grounding-specific."
            )

    # ----- Impedance snapshot -----
    report_lines.append("")
    report_lines.append("Impedance metadata found in XDF:")
    imp_lines = _impedance_snapshot(streams)
    if imp_lines:
        report_lines.extend("  " + ln for ln in imp_lines)
    else:
        report_lines.append(
            "  (no impedance data reachable from stream info / dedicated "
            "impedance stream)."
        )

    txt_path = diag_dir /"line_noise_report.txt"
    txt_path.write_text("\n".join(report_lines) + "\n")
    print(f"wrote {txt_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
