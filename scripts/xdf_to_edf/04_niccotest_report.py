"""Step 4 (NiccoTest-specific) — extended report.

Produces ``outputs/NiccoTest/P013_NiccoTest_extended_report.pdf`` plus
diagnostic PNGs under ``outputs/NiccoTest/diagnostics/``.

Sections:

1. **Cross-subject signal quality** — NiccoTest vs Sam vs Yaya. Per-
   channel 60 Hz line-noise SNR and broadband RMS.
2. **Sentiometer ON vs OFF — full-spectrum.** Per-channel heatmap of
   log10(PSD_ON / PSD_OFF) across 0.5–60 Hz, plus a canonical-band
   summary table (delta / theta / alpha / beta / low-gamma).
3. **Sentiometer ON vs OFF — sample EEG.** Six representative channels
   (Fp1/Fp2/C3/C4/O1/O2), three 10 s epochs in each window, so the
   reader can see whether the ON epochs carry visible rhythm/noise
   that the OFF epochs don't.
4. **EOG derivation** — Fp1−TP9 and Fp2−TP10 (cap-derived offline;
   the ExGa 1/2 face leads for the usual AASM E1-M2/E2-M1 were not
   placed).
5. **Chin / EOG electrodes — task vs rest.** ExGa 3/4 chin EMG
   bipolars (placed per protocol) plus the offline EOG pair.
6. **Sentiometer PD1–PD5 — per task vs matched rest.** 30 s per
   window; each photodiode in its own row.

**Sentiometer placement.** In this study the Sentiometer is mounted
on the **arm** — not the forehead. The optical signal therefore
reflects peripheral (skin / vasculature) physiology, not scalp optics.
Any coupling into EEG must be indirect (shared ground, cable RFI,
common-mode pickup), which makes the on/off spatial pattern in
Section 2 an interesting systems observation regardless of whether
the Sentiometer signal itself carries neural information.

Nicco did not sleep in this recording. "Rest" / "Sentiometer OFF"
windows are drawn from the laying-down / post-task period before and
after the device was removed at ~7 min from end of recording. The
final 60 s (device-removal artifact) is excluded.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import matplotlib
import numpy as np
import pyxdf
from scipy import signal

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from reportlab.lib import colors  # noqa: E402
from reportlab.lib.pagesizes import letter  # noqa: E402
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet  # noqa: E402
from reportlab.lib.units import inch  # noqa: E402
from reportlab.platypus import (  # noqa: E402
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from _common import SAMPLE_DIR, diag_dir_for, output_dir_for
from _report_helpers import (
    BANDS,
    EPOCH_S,
    SAMPLE_CHANNELS,
    build_band_delta_rows,
    compute_psd_matrix,
    plot_log_ratio_heatmap,
    plot_sample_epochs,
    welch_psd,
)

FS = 500.0

# Subject → XDF file mapping. Hard-coded so the cross-subject section is
# reproducible regardless of which file is newest in sampledata/.
SUBJECT_XDFS = {
    "NiccoTest": SAMPLE_DIR / "sub-NiccoTest_ses-S001_task-Default_run-001_eeg.xdf",
    "Sam":       SAMPLE_DIR / "sub-Sam-Sleep_ses-S001_task-Default_run-001_eeg.xdf",
    "Yaya":      SAMPLE_DIR / "sub-Yaya_ses-S001_task-Default_run-001_eeg.xdf",
}

# Friendly names for the six P013 tasks, used in plot labels and
# captions throughout the extended report.
TASK_NAMES = {
    "task00": "Questionnaire",
    "task01": "Oddball (P300)",
    "task02": "RGB illuminance",
    "task03": "Backward masking",
    "task04": "Mind-state",
    "task05": "SSVEP",
}


def _task_label(code: str) -> str:
    """Pretty label for a task code — `Task 01: Oddball (P300)`."""
    name = TASK_NAMES.get(code, "")
    n = code[4:6] if len(code) >= 6 else code
    return f"Task {n}: {name}" if name else f"Task {n}"

# Cross-subject analysis window.
XSUBJ_SEG_START_S = 300.0
XSUBJ_SEG_DURATION_S = 60.0

# Sentiometer ON/OFF windows — anchored to the Sentiometer stream-end
# at t≈5630 s, last 60 s of recording excluded as removal artifact.
NICCO_ON_START = 5390.0
NICCO_ON_DUR = 240.0
NICCO_OFF_START = 5690.0
NICCO_OFF_DUR = 240.0


# ----- XDF helpers ---------------------------------------------------------

def _first(lst):
    return lst[0] if isinstance(lst, (list, tuple)) and lst else lst


def _channel_labels(stream):
    info = stream.get("info") or {}
    desc = info.get("desc") or []
    desc0 = desc[0] if desc else {}
    if not isinstance(desc0, dict):
        return []
    chs = desc0.get("channels") or []
    chs0 = chs[0] if chs else {}
    if not isinstance(chs0, dict):
        return []
    entries = chs0.get("channel") or []
    if not isinstance(entries, list):
        entries = [entries]
    out = []
    for c in entries:
        if not isinstance(c, dict):
            continue
        lab = c.get("label", [""])
        out.append(lab[0] if isinstance(lab, list) else str(lab))
    return out


def _slice_window(stream, start_s, dur_s):
    ts = np.asarray(stream.get("time_stamps", []), dtype=float)
    if ts.size == 0:
        return np.empty((0, 0))
    t0 = float(ts[0])
    mask = (ts >= t0 + start_s) & (ts < t0 + start_s + dur_s)
    data = np.asarray(stream.get("time_series", []), dtype=float)
    if data.ndim == 1:
        return data[mask]
    return data[mask, :]


def _load_bv(xdf_path: Path):
    streams, _ = pyxdf.load_xdf(str(xdf_path))
    eeg = next(
        (s for s in streams if s["info"]["name"][0] == "BrainAmpSeries-Dev_1"),
        None,
    )
    if eeg is None:
        raise SystemExit(f"No BrainAmpSeries-Dev_1 stream in {xdf_path}")
    return streams, eeg, _channel_labels(eeg)


def _marker_times_rel(streams, bv_stream) -> list[float]:
    bv_ts = np.asarray(bv_stream.get("time_stamps", []), dtype=float)
    if bv_ts.size == 0:
        return []
    t0 = float(bv_ts[0])
    out: list[float] = []
    for s in streams:
        info = s.get("info") or {}
        if (info.get("type", [""])[0] or "").lower() != "markers":
            continue
        ts_m = np.asarray(s.get("time_stamps", []), dtype=float)
        if ts_m.size == 0:
            continue
        out.extend((ts_m - t0).tolist())
    return out


# ----- cross-subject metrics -----------------------------------------------

@dataclass
class SubjectMetrics:
    subject: str
    n_channels: int
    per_ch_60hz_db: dict[str, float]
    per_ch_rms_uv: dict[str, float]
    median_60hz_db: float
    median_rms_uv: float
    n_flagged_60hz: int
    n_saturated: int


def _line_snr(f, pxx, target=60.0, half_bw=1.0, baseline_bw=4.0):
    peak = (f >= target - half_bw) & (f <= target + half_bw)
    lo = (f >= target - baseline_bw - half_bw) & (f < target - half_bw)
    hi = (f > target + half_bw) & (f <= target + baseline_bw + half_bw)
    if peak.sum() == 0 or (lo.sum() + hi.sum()) == 0:
        return float("nan")
    P = float(np.max(pxx[peak])); B = float(np.mean(pxx[lo | hi]))
    if P <= 0 or B <= 0:
        return float("nan")
    return 10.0 * np.log10(P / B)


def _compute_subject_metrics(subj: str, xdf_path: Path) -> SubjectMetrics:
    print(f"  computing metrics for {subj} …")
    _, eeg, labels = _load_bv(xdf_path)
    d = _slice_window(eeg, XSUBJ_SEG_START_S, XSUBJ_SEG_DURATION_S)
    good = [j for j, l in enumerate(labels)
            if l.lower() not in ("triggerstream", "trigger")
            and j < d.shape[1]]
    labs = [labels[j] for j in good]
    d = d[:, good]
    per_60, per_rms = {}, {}
    sat = 0
    for j, lab in enumerate(labs):
        x = d[:, j]
        f, p = welch_psd(x, FS)
        per_60[lab] = _line_snr(f, p, 60.0)
        per_rms[lab] = float(np.sqrt(np.mean(x ** 2)))
        if per_rms[lab] > 2000.0:
            sat += 1
    snrs = [v for v in per_60.values() if np.isfinite(v)]
    rmss = [v for v in per_rms.values() if np.isfinite(v)]
    return SubjectMetrics(
        subject=subj, n_channels=len(labs),
        per_ch_60hz_db=per_60, per_ch_rms_uv=per_rms,
        median_60hz_db=float(np.median(snrs)) if snrs else float("nan"),
        median_rms_uv=float(np.median(rmss)) if rmss else float("nan"),
        n_flagged_60hz=sum(1 for v in snrs if v > 10.0),
        n_saturated=sat,
    )


def _plot_xsubj_60hz(metrics: dict[str, SubjectMetrics], path: Path) -> None:
    fig, ax = plt.subplots(figsize=(9, 4.2))
    rng = np.random.default_rng(0)
    cmap = {"NiccoTest": "#1f77b4", "Sam": "#2ca02c", "Yaya": "#d62728"}
    for i, (subj, m) in enumerate(metrics.items()):
        vals = np.array([v for v in m.per_ch_60hz_db.values()
                         if np.isfinite(v)])
        x = np.full_like(vals, i, dtype=float) + rng.normal(0, 0.06, vals.size)
        ax.scatter(x, vals, s=18, alpha=0.6, color=cmap.get(subj, "#888"))
        ax.hlines(np.median(vals), i - 0.3, i + 0.3,
                  colors="black", linewidth=2, zorder=3)
        ax.text(
            i, float(np.max(vals)) + 3,
            f"med={np.median(vals):.1f} dB\n>10dB: {m.n_flagged_60hz}/{m.n_channels}",
            ha="center", fontsize=8,
        )
    ax.set_xticks(range(len(metrics)))
    ax.set_xticklabels(list(metrics.keys()))
    ax.axhline(10.0, color="grey", ls="--", lw=0.8, label="10 dB flag threshold")
    ax.set_ylabel("60 Hz peak-to-baseline SNR (dB)")
    ax.set_title(
        "Cross-subject 60 Hz line-noise pickup — "
        f"EEG channels, segment [t₀+{int(XSUBJ_SEG_START_S)}, "
        f"+{int(XSUBJ_SEG_START_S+XSUBJ_SEG_DURATION_S)}] s"
    )
    ax.grid(alpha=0.3)
    ax.legend(loc="lower right", fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=140); plt.close(fig)


def _plot_xsubj_rms(metrics: dict[str, SubjectMetrics], path: Path) -> None:
    fig, ax = plt.subplots(figsize=(9, 4.2))
    rng = np.random.default_rng(1)
    cmap = {"NiccoTest": "#1f77b4", "Sam": "#2ca02c", "Yaya": "#d62728"}
    for i, (subj, m) in enumerate(metrics.items()):
        vals = np.array([v for v in m.per_ch_rms_uv.values()
                         if np.isfinite(v)])
        x = np.full_like(vals, i, dtype=float) + rng.normal(0, 0.06, vals.size)
        ax.scatter(x, vals, s=18, alpha=0.6, color=cmap.get(subj, "#888"))
        ax.hlines(np.median(vals), i - 0.3, i + 0.3,
                  colors="black", linewidth=2, zorder=3)
        ax.text(
            i, float(np.max(vals)) * 1.05,
            f"med={np.median(vals):.1f} µV\nsat>2mV: {m.n_saturated}/{m.n_channels}",
            ha="center", fontsize=8,
        )
    ax.set_xticks(range(len(metrics)))
    ax.set_xticklabels(list(metrics.keys()))
    ax.set_yscale("log")
    ax.set_ylabel("Per-channel RMS (µV, log)")
    ax.set_title(
        "Cross-subject broadband RMS — "
        f"EEG channels, segment [t₀+{int(XSUBJ_SEG_START_S)}, "
        f"+{int(XSUBJ_SEG_START_S+XSUBJ_SEG_DURATION_S)}] s"
    )
    ax.grid(alpha=0.3, which="both")
    fig.tight_layout()
    fig.savefig(path, dpi=140); plt.close(fig)


# ----- Nicco Sentiometer ON/OFF -------------------------------------------

@dataclass
class NiccoOnOff:
    labels: list[str]
    freq: np.ndarray
    psd_on: np.ndarray
    psd_off: np.ndarray
    data_on: np.ndarray
    data_off: np.ndarray
    eog_l_on: np.ndarray
    eog_l_off: np.ndarray
    eog_r_on: np.ndarray
    eog_r_off: np.ndarray


def _compute_on_off(xdf_path: Path) -> NiccoOnOff:
    print("  computing Nicco on/off contrast …")
    _, eeg, labels = _load_bv(xdf_path)
    good = [j for j, l in enumerate(labels)
            if l.lower() not in ("triggerstream", "trigger")]
    labs = [labels[j] for j in good]
    on = _slice_window(eeg, NICCO_ON_START, NICCO_ON_DUR)[:, good]
    off = _slice_window(eeg, NICCO_OFF_START, NICCO_OFF_DUR)[:, good]
    freqs, psd_on = compute_psd_matrix(on, FS)
    _, psd_off = compute_psd_matrix(off, FS)

    # Offline EOG.
    i_fp1 = labs.index("Fp1"); i_fp2 = labs.index("Fp2")
    i_tp9 = labs.index("TP9"); i_tp10 = labs.index("TP10")

    return NiccoOnOff(
        labels=labs, freq=freqs,
        psd_on=psd_on, psd_off=psd_off,
        data_on=on, data_off=off,
        eog_l_on=on[:, i_fp1] - on[:, i_tp9],
        eog_l_off=off[:, i_fp1] - off[:, i_tp9],
        eog_r_on=on[:, i_fp2] - on[:, i_tp10],
        eog_r_off=off[:, i_fp2] - off[:, i_tp10],
    )


def _plot_eog_traces(oo: NiccoOnOff, path: Path) -> None:
    n = int(10 * FS)
    t = np.arange(n) / FS
    fig, axes = plt.subplots(2, 2, figsize=(10, 4.8), sharex=True, sharey=True)
    pairs = [
        ("Sentiometer ON",  oo.eog_l_on,  oo.eog_r_on,  "#1f77b4"),
        ("Sentiometer OFF", oo.eog_l_off, oo.eog_r_off, "#d62728"),
    ]
    for col, (title, left, right, c) in enumerate(pairs):
        i0 = (left.size - n) // 2
        axes[0, col].plot(t, left[i0:i0+n], lw=0.7, color=c)
        axes[0, col].set_title(f"{title}: Fp1−TP9 (EOG-L)")
        axes[1, col].plot(t, right[i0:i0+n], lw=0.7, color=c)
        axes[1, col].set_title(f"{title}: Fp2−TP10 (EOG-R)")
    for ax in axes.flat:
        ax.grid(alpha=0.3); ax.set_ylabel("µV")
    axes[1, 0].set_xlabel("time (s)"); axes[1, 1].set_xlabel("time (s)")
    fig.suptitle(
        "Offline EOG derivations (cap-derived, no CGX ExGa bipolar)  "
        "— 10 s mid-window excerpt"
    )
    fig.tight_layout()
    fig.savefig(path, dpi=140); plt.close(fig)


def _plot_eog_psd(oo: NiccoOnOff, path: Path) -> None:
    pairs = [
        ("Fp1 − TP9 (EOG-L)", oo.eog_l_on, oo.eog_l_off),
        ("Fp2 − TP10 (EOG-R)", oo.eog_r_on, oo.eog_r_off),
    ]
    fig, axes = plt.subplots(len(pairs), 1, figsize=(9, 4.0), sharex=True)
    if len(pairs) == 1:
        axes = [axes]
    for ax, (name, s_on, s_off) in zip(axes, pairs):
        f_on, p_on = welch_psd(s_on, FS)
        f_off, p_off = welch_psd(s_off, FS)
        ax.semilogy(f_on, p_on, lw=1.0, color="#1f77b4", label="ON")
        ax.semilogy(f_off, p_off, lw=1.0, color="#d62728", label="OFF")
        for name2, lo, hi in BANDS:
            ax.axvspan(lo, hi, color="grey", alpha=0.05)
        ax.set_xlim(0.5, 60); ax.set_ylabel(f"{name}\nPSD (µV²/Hz)")
        ax.grid(alpha=0.3, which="both")
    axes[0].legend(loc="upper right", fontsize=8)
    axes[-1].set_xlabel("Frequency (Hz)")
    fig.suptitle("EOG derivations — PSD comparison, Sentiometer ON vs OFF")
    fig.tight_layout()
    fig.savefig(path, dpi=140); plt.close(fig)


# ----- PDF --------------------------------------------------------------

def _build_pdf(
    subject: str,
    metrics: dict[str, SubjectMetrics],
    oo: NiccoOnOff,
    pngs: dict[str, Path],
    out_path: Path,
    task_starts_30s: dict[str, float] | None = None,
    rest_starts: list[float] | None = None,
) -> None:
    styles = getSampleStyleSheet()
    h1, h2, h3 = styles["Heading1"], styles["Heading2"], styles["Heading3"]
    body = styles["BodyText"]
    small = ParagraphStyle("small", parent=body, fontSize=8, leading=10)

    doc = SimpleDocTemplate(
        str(out_path), pagesize=letter,
        rightMargin=0.55*inch, leftMargin=0.55*inch,
        topMargin=0.55*inch, bottomMargin=0.55*inch,
    )
    story: list = []

    # ---- Header ----
    story.append(Paragraph("P013 — NiccoTest Extended Report", h1))
    story.append(Paragraph(
        f"Subject: <b>{subject}</b>   &nbsp;&nbsp; "
        f"Compared against: <b>Sam</b>, <b>Yaya</b>. &nbsp;&nbsp; "
        f"Cross-subject analysis window: "
        f"[t₀+{int(XSUBJ_SEG_START_S)}, +{int(XSUBJ_SEG_START_S+XSUBJ_SEG_DURATION_S)}] s.",
        body,
    ))
    story.append(Spacer(1, 0.1 * inch))

    # ---- Section 1: cross-subject signal quality ----
    story.append(Paragraph("1.  Cross-subject signal-quality summary", h2))

    summary_rows = [[
        "Subject", "Channels",
        "Median 60 Hz (dB)", "Flagged >10 dB",
        "Median RMS (µV)", "RMS >2000 µV",
    ]]
    for subj, m in metrics.items():
        summary_rows.append([
            subj, str(m.n_channels),
            f"{m.median_60hz_db:.2f}",
            f"{m.n_flagged_60hz}/{m.n_channels}",
            f"{m.median_rms_uv:.1f}",
            f"{m.n_saturated}/{m.n_channels}",
        ])
    tbl = Table(summary_rows, hAlign="LEFT")
    tbl.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
    ]))
    story.append(tbl)
    story.append(Spacer(1, 0.1 * inch))

    n = metrics["NiccoTest"]; s = metrics.get("Sam"); y = metrics.get("Yaya")
    line_text = (
        f"<b>60 Hz line noise.</b> Nicco's median = "
        f"<b>{n.median_60hz_db:.1f} dB</b> "
        f"(Sam {s.median_60hz_db:.1f}, Yaya {y.median_60hz_db:.1f}). "
    )
    if n.median_60hz_db < min(s.median_60hz_db, y.median_60hz_db) - 1:
        line_text += "Nicco is the <b>quietest of the three</b>."
    elif n.median_60hz_db > max(s.median_60hz_db, y.median_60hz_db) + 1:
        line_text += "Nicco is the <b>noisiest of the three</b>."
    else:
        line_text += "Nicco sits between the two prior pilots."
    story.append(Paragraph(line_text, body))

    rms_text = (
        f"<b>Overall quality.</b> Median broadband RMS: Nicco "
        f"<b>{n.median_rms_uv:.0f} µV</b>, Sam {s.median_rms_uv:.0f}, "
        f"Yaya {y.median_rms_uv:.0f}. Rail-saturated (>2 mV RMS): Nicco "
        f"<b>{n.n_saturated}/{n.n_channels}</b>, "
        f"Sam {s.n_saturated}/{s.n_channels}, "
        f"Yaya {y.n_saturated}/{y.n_channels}. "
    )
    if (min(metrics.items(), key=lambda kv: kv[1].median_rms_uv)[0]
            == "NiccoTest"
            and min(metrics.items(), key=lambda kv: kv[1].n_saturated)[0]
            == "NiccoTest"):
        rms_text += (
            "<b>Nicco has the best overall signal quality</b> of the three "
            "pilots — lowest broadband RMS and no rail-saturated channels."
        )
    story.append(Paragraph(rms_text, body))
    story.append(Spacer(1, 0.08 * inch))
    story.append(Image(str(pngs["xsubj_60hz"]), width=7.1*inch, height=3.35*inch))
    story.append(Paragraph(
        "Per-channel 60 Hz peak-to-baseline SNR.  Horizontal black tick = "
        "median.  >10 dB is our flag threshold.", small,
    ))
    story.append(Image(str(pngs["xsubj_rms"]), width=7.1*inch, height=3.35*inch))
    story.append(Paragraph(
        "Per-channel broadband RMS on a log axis; saturated channels lift "
        "off the top of the bulk.", small,
    ))

    story.append(PageBreak())

    # ---- Section 2: Sentiometer ON vs OFF — full spectrum ----
    story.append(Paragraph(
        "2.  Sentiometer ON vs OFF — full-spectrum per-channel analysis", h2,
    ))
    story.append(Paragraph(
        "Nicco removed the Sentiometer from his arm ~7 min before the end "
        "of recording. The Sentiometer LSL stream stopped at <b>t ≈ 5630 s</b>. "
        "We compare two matched 240 s EEG windows straddling that event, "
        "with a 60 s settling buffer and the final 60 s (device-removal "
        "artifact) excluded:",
        body,
    ))
    story.append(Paragraph(
        f"&nbsp;&nbsp;• <b>ON</b>: [{int(NICCO_ON_START)}, "
        f"+{int(NICCO_ON_DUR)}] s — last 4 min with device on the arm. "
        f"<br/>&nbsp;&nbsp;• <b>OFF</b>: [{int(NICCO_OFF_START)}, "
        f"+{int(NICCO_OFF_DUR)}] s — first 4 min after device off.",
        body,
    ))
    story.append(Spacer(1, 0.08 * inch))

    # Band table
    rows = build_band_delta_rows(oo.psd_on, oo.psd_off, oo.freq)
    band_tbl = Table(rows, hAlign="LEFT")
    band_tbl.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(Paragraph("Band-power summary (median across EEG channels)", h3))
    story.append(band_tbl)
    story.append(Spacer(1, 0.08 * inch))

    # Interpretation paragraph — compute dominant effect across all bands.
    # Which bands are louder ON vs louder OFF by what margins?
    interp_lines = []
    for name, lo, hi in BANDS:
        mask = (oo.freq >= lo) & (oo.freq < hi)
        if mask.sum() < 2:
            continue
        on_band = np.trapezoid(oo.psd_on[:, mask], oo.freq[mask], axis=1)
        off_band = np.trapezoid(oo.psd_off[:, mask], oo.freq[mask], axis=1)
        valid = np.isfinite(on_band) & np.isfinite(off_band) & (off_band > 0)
        if not valid.any():
            continue
        pct = (np.median(on_band[valid]) - np.median(off_band[valid])) / \
               np.median(off_band[valid]) * 100
        dirn = "louder ON" if pct > 0 else "louder OFF"
        interp_lines.append(
            f"<b>{name}</b>: {dirn} by <b>{abs(pct):.0f}%</b> (median)"
        )
    story.append(Paragraph(
        "<b>Interpretation.</b>  " + ";  ".join(interp_lines) + ".  "
        "Positive Δ% values (ON louder than OFF) in the table above are "
        "consistent with the Sentiometer adding signal / noise; negative "
        "Δ% with the device attenuating pickup. Because the Sentiometer "
        "is worn on the <b>arm</b> in this study (not the scalp), any "
        "coupling into EEG must be indirect — common-mode via shared "
        "ground, RFI from the laser driver / USB cabling, or antenna "
        "pickup along the ExG cable harness. The per-channel heatmap on "
        "the next page shows the spatial pattern across 0.5–60 Hz.",
        body,
    ))

    story.append(PageBreak())
    story.append(Paragraph(
        "Per-channel log₁₀(PSD<sub>ON</sub> / PSD<sub>OFF</sub>) heatmap", h3,
    ))
    story.append(Paragraph(
        "Red = louder with Sentiometer on (device-coupled energy or noise). "
        "Blue = louder off. Channels are sorted top-to-bottom by mean "
        "log-ratio over 1–40 Hz, so the most affected electrodes sit at the "
        "top. The two dashed vertical lines mark 20 Hz (SSVEP carrier band) "
        "and 60 Hz (mains).",
        small,
    ))
    story.append(Spacer(1, 0.04 * inch))
    story.append(Image(
        str(pngs["on_off_heatmap"]), width=7.3*inch, height=8.2*inch,
    ))

    story.append(PageBreak())
    story.append(Paragraph(
        "Overlaid PSDs at 6 representative scalp sites", h3,
    ))
    story.append(Paragraph(
        "Front-left / front-right (Fp1, Fp2), left / right central "
        "(C3, C4), back-left / back-right (O1, O2). Full-page for "
        "readability. Narrow-band injection shows up as a spike; "
        "broadband coupling lifts the ON curve across the spectrum.",
        small,
    ))
    story.append(Image(str(pngs["on_off_psd"]), width=7.2*inch, height=9.2*inch))

    story.append(PageBreak())
    story.append(Paragraph(
        "Per-channel 20 Hz band-power change", h3,
    ))
    story.append(Image(str(pngs["band20_bar"]), width=7.1*inch, height=9.0*inch))
    story.append(Paragraph(
        "Included for continuity with the original 20 Hz drill-down. "
        "Sorted by delta; red = louder OFF, blue = louder ON.",
        small,
    ))

    story.append(PageBreak())

    # ---- Section 3: Sentiometer ON vs OFF — sample EEG ----
    story.append(Paragraph(
        "3.  Sentiometer ON vs OFF — sample EEG epochs", h2,
    ))
    story.append(Paragraph(
        "Six representative channels (Fp1 / Fp2 frontal, C3 / C4 central, "
        f"O1 / O2 occipital). Three {int(EPOCH_S)} s epochs are drawn from "
        "each window so the reader can eyeball the trace difference — "
        "device-coupled noise is usually visible as high-frequency fuzz "
        "or a visible periodic tone that disappears after removal.",
        body,
    ))
    story.append(Spacer(1, 0.05 * inch))
    story.append(Image(
        str(pngs["on_off_sample_eeg"]), width=7.3*inch, height=8.2*inch,
    ))

    story.append(PageBreak())

    # ---- Section 4: EOG derivation ----
    story.append(Paragraph(
        "4.  EOG derivation — Fp1−TP9 and Fp2−TP10 (cap-derived)",
        h2,
    ))
    story.append(Paragraph(
        "<b>Setup difference vs the standard P013 protocol — EOG only.</b>  "
        "The usual AASM face leads for CGX ExGa 1 (E1) and ExGa 2 (E2) were "
        "<b>not placed</b> this session, so the hardware-level E1-M2 / "
        "E2-M1 AASM derivations are not available. EOG was instead derived "
        "<b>offline</b> from the BrainVision cap:",
        body,
    ))
    story.append(Paragraph("&nbsp;&nbsp;• <b>EOG-L</b> = Fp1 − TP9", body))
    story.append(Paragraph("&nbsp;&nbsp;• <b>EOG-R</b> = Fp2 − TP10", body))
    story.append(Paragraph(
        "Vertical / horizontal eye movements still appear (Fp1 and Fp2 sit "
        "above the eyebrows), but polarity and amplitude differ from the "
        "AASM E1-M2 / E2-M1 standard because the reference is now the "
        "mastoid <b>via the cap</b> rather than a near-eye bipolar lead. "
        "Adequate for gross eye-movement detection and for confirming that "
        "any 20 Hz effect in Section 2 is not an ocular artifact.",
        body,
    ))
    story.append(Paragraph(
        "<b>Chin EMG was placed as designed.</b>  ExGa 3 (ChinZ−Chin1, "
        "primary submental bipolar) and ExGa 4 (Chin2−Chin1prime, lateral "
        "backup) were attached per the AASM montage and are authoritative "
        "for this session — see Section 5 for task-vs-rest PSDs.",
        body,
    ))
    story.append(Spacer(1, 0.08 * inch))
    story.append(Image(str(pngs["eog_traces"]), width=7.2*inch, height=3.5*inch))
    story.append(Paragraph(
        "10 s mid-window traces. Blink / saccade morphology is visible in "
        "both windows; amplitudes are lower than a canonical facial "
        "bipolar derivation would give.", small,
    ))
    story.append(Image(str(pngs["eog_psd"]), width=7.2*inch, height=3.0*inch))
    story.append(Paragraph(
        "PSD comparison for the two EOG derivations, ON vs OFF. Grey "
        "shading marks the canonical band boundaries.", small,
    ))

    # ---- Section 5: Chin + EOG at task vs rest ----
    if pngs.get("chin_eog_traces") and pngs["chin_eog_traces"].exists():
        story.append(PageBreak())
        story.append(Paragraph(
            "5.  Chin / EOG electrodes — task vs rest",
            h2,
        ))
        story.append(Paragraph(
            "A task window (cognitively engaged) vs a rest window (laying "
            "down, Sentiometer still on the arm) for the CGX ExGa channels "
            "and the offline cap-derived EOG pair (Fp1−TP9, Fp2−TP10). "
            "<b>Channel status for this session:</b>",
            body,
        ))
        story.append(Paragraph(
            "&nbsp;&nbsp;• <b>ExGa 1 / ExGa 2</b> — EOG ports, face leads "
            "NOT placed this session. Rows are shown for transparency but "
            "carry no neurophysiological signal; any structure is "
            "environmental / instrumental. Use the offline EOG pair "
            "instead.",
            body,
        ))
        story.append(Paragraph(
            "&nbsp;&nbsp;• <b>ExGa 3 (ChinZ−Chin1)</b> and "
            "<b>ExGa 4 (Chin2−Chin1prime)</b> — submental bipolar chin EMG, "
            "placed as designed. Authoritative for this session.",
            body,
        ))
        story.append(Paragraph(
            "&nbsp;&nbsp;• <b>Fp1−TP9 / Fp2−TP10</b> — offline cap-derived "
            "EOG. Authoritative eye signal for this session.",
            body,
        ))
        story.append(Spacer(1, 0.08 * inch))
        story.append(Paragraph("Time-domain excerpts (30 s)", h3))
        story.append(Image(
            str(pngs["chin_eog_traces"]), width=7.2*inch, height=7.5*inch,
        ))
        story.append(PageBreak())
        story.append(Paragraph("PSD comparison", h3))
        story.append(Image(
            str(pngs["chin_eog_psd"]), width=7.2*inch, height=9.0*inch,
        ))

    # ---- Section 6: Sentiometer photodiode traces — per-task + rest ----
    if pngs.get("sentiometer_task_rest") and pngs["sentiometer_task_rest"].exists():
        story.append(PageBreak())
        story.append(Paragraph(
            "6.  Sentiometer photodiode traces — per-task vs rest windows",
            h2,
        ))
        task_codes_sorted = sorted(task_starts_30s.keys())
        task_list_str = ", ".join(_task_label(c) for c in task_codes_sorted)
        n_rest = len(rest_starts) if rest_starts else 0
        story.append(Paragraph(
            "<b>Sentiometer placement: arm (not scalp).</b>  In this study "
            "the device is mounted on the arm, so the photodiode signals "
            "reflect peripheral physiology (skin optics, blood-pulse "
            "modulation) rather than scalp hemodynamics.",
            body,
        ))
        story.append(Paragraph(
            f"One column per task window ({task_list_str}), plus {n_rest} "
            f"matched random 30 s rest windows drawn from the laying-down "
            f"period AFTER the last task marker and BEFORE the Sentiometer "
            f"stream ended (i.e. device still on the arm, quiet rest). "
            f"Each row is one photodiode (PD1–PD5); every panel is demeaned "
            f"and auto-scaled by its own percentile range so a loud PD "
            f"doesn't drown a quiet one. Task panels are drawn in blue, "
            f"rest panels in green.",
            body,
        ))
        story.append(Spacer(1, 0.08 * inch))
        story.append(Image(
            str(pngs["sentiometer_task_rest"]),
            width=10.1*inch, height=6.5*inch,
        ))
        story.append(Paragraph(
            "Compare the amplitude envelope and periodic structure PD-by-"
            "PD, task-by-task. If a task-evoked Sentiometer response "
            "exists, it should differ visibly from the matched rest "
            "windows; if the signal is dominated by peripheral dynamics "
            "(heart rate, breathing, arm motion), the task and rest "
            "panels in a given row will look similar.",
            small,
        ))

    doc.build(story)
    print(f"wrote {out_path}")


# ----- Also update the 20 Hz bar chart helper ------------------------------

def _plot_20hz_bar(oo: NiccoOnOff, path: Path) -> None:
    pct: list[tuple[str, float]] = []
    for j, lab in enumerate(oo.labels):
        on_bp = float(np.trapezoid(
            oo.psd_on[j, (oo.freq >= 18) & (oo.freq < 22)],
            oo.freq[(oo.freq >= 18) & (oo.freq < 22)]
        ))
        off_bp = float(np.trapezoid(
            oo.psd_off[j, (oo.freq >= 18) & (oo.freq < 22)],
            oo.freq[(oo.freq >= 18) & (oo.freq < 22)]
        ))
        if on_bp > 0 and np.isfinite(off_bp):
            pct.append((lab, (off_bp - on_bp) / on_bp * 100.0))
    pct.sort(key=lambda kv: kv[1])

    fig, ax = plt.subplots(figsize=(10, 0.18 * len(pct) + 1.5))
    names = [kv[0] for kv in pct]; vals = [kv[1] for kv in pct]
    y = np.arange(len(pct))
    cols = ["#d62728" if v > 0 else "#1f77b4" for v in vals]
    ax.barh(y, vals, color=cols, alpha=0.8, edgecolor="black", linewidth=0.3)
    ax.set_yticks(y); ax.set_yticklabels(names, fontsize=6)
    ax.axvline(0, color="black", lw=0.6)
    ax.set_xlabel("Δ 20 Hz band power (OFF − ON) / ON × 100 (%)")
    ax.set_title(
        "Per-channel 18–22 Hz power change after Sentiometer removal  "
        "(positive = louder OFF)"
    )
    ax.grid(axis="x", alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=140); plt.close(fig)


# ----- Overlaid PSDs at 4 sites (for section 2 figure) ---------------------

def _plot_overlaid_psds(oo: NiccoOnOff, channels: list[str], path: Path) -> None:
    fig, axes = plt.subplots(len(channels), 1,
                             figsize=(9, 2.6 * len(channels)), sharex=True)
    if len(channels) == 1:
        axes = [axes]
    for ax, ch in zip(axes, channels):
        if ch not in oo.labels:
            ax.text(0.5, 0.5, f"{ch} missing", transform=ax.transAxes,
                    ha="center", va="center"); continue
        j = oo.labels.index(ch)
        ax.semilogy(oo.freq, oo.psd_on[j], lw=1.0, color="#1f77b4",
                    label="Sentiometer ON")
        ax.semilogy(oo.freq, oo.psd_off[j], lw=1.0, color="#d62728",
                    label="Sentiometer OFF")
        for _name, lo, hi in BANDS:
            ax.axvspan(lo, hi, color="grey", alpha=0.05)
        ax.axvspan(58, 62, color="red", alpha=0.08)
        ax.set_xlim(0.5, 60)
        ax.set_ylabel(f"{ch}\nPSD (µV²/Hz)")
        ax.grid(alpha=0.3, which="both")
    axes[0].legend(loc="upper right", fontsize=8)
    axes[-1].set_xlabel("Frequency (Hz)")
    fig.suptitle(
        "Nicco — Sentiometer ON vs OFF, matched 240 s windows"
    )
    fig.tight_layout()
    fig.savefig(path, dpi=140); plt.close(fig)


# ----- Section 4/5 helpers: task vs rest windows, chin+EOG, Sentiometer ----

def _task_windows_from_markers(
    streams, bv_stream, sentiometer_end_s: float,
) -> tuple[dict[str, tuple[float, float]], float]:
    """Parse task windows from the marker stream(s).

    Returns ``(task_windows, last_marker_s)`` where:
      * ``task_windows = {"task00": (start_s, end_s), ...}`` uses exact
        ``taskNN_end`` markers when available and falls back to the next
        task's start for the end. If neither is available for the final
        task, uses the last marker time seen across all streams.
      * ``last_marker_s`` is the latest marker timestamp of any kind
        relative to the BrainVision stream start — used downstream to
        mark the task / rest boundary.
    """
    bv_ts = np.asarray(bv_stream.get("time_stamps", []), dtype=float)
    if bv_ts.size == 0:
        return {}, 0.0
    t0 = float(bv_ts[0])

    starts: list[tuple[str, float]] = []
    ends: dict[str, float] = {}
    latest_any = 0.0
    for s in streams:
        info = s.get("info") or {}
        if (info.get("type", [""])[0] or "").lower() != "markers":
            continue
        samples = s.get("time_series", []) or []
        ts_m = np.asarray(s.get("time_stamps", []), dtype=float)
        for sample, t in zip(samples, ts_m):
            lab = sample[0] if isinstance(sample, (list, tuple)) else str(sample)
            tsec = float(t - t0)
            latest_any = max(latest_any, tsec)
            if len(lab) >= 10 and lab[:4] == "task":
                code = lab[:6]
                if code[4:6].isdigit():
                    if lab.endswith("_start"):
                        starts.append((code, tsec))
                    elif lab.endswith("_end"):
                        # Keep the LATEST end marker per task code so the
                        # whole block counts (some tasks emit an _end at
                        # each sub-phase).
                        ends[code] = max(ends.get(code, -1.0), tsec)
    if not starts:
        return {}, latest_any
    # Keep the EARLIEST start per task code.
    first: dict[str, float] = {}
    for code, t in starts:
        if code not in first or t < first[code]:
            first[code] = t
    order = sorted(first.items(), key=lambda kv: kv[1])
    out: dict[str, tuple[float, float]] = {}
    for i, (code, st) in enumerate(order):
        if code in ends and ends[code] > st:
            end = ends[code]
        elif i + 1 < len(order):
            end = order[i+1][1]
        else:
            end = latest_any
        end = max(end, st + 5.0)
        # Clamp by sentiometer_end so we don't spill into the
        # device-off window.
        if sentiometer_end_s > 0:
            end = min(end, sentiometer_end_s)
        out[code] = (st, end)
    return out, latest_any


def _task_central_30s(windows: dict[str, tuple[float, float]], win_s: float = 30.0
                      ) -> dict[str, float]:
    """Central 30 s start time for each task window."""
    out: dict[str, float] = {}
    for code, (st, en) in windows.items():
        mid = 0.5 * (st + en)
        out[code] = max(st, mid - win_s * 0.5)
    return out


def _random_rest_windows(
    last_task_end: float, sentiometer_end: float,
    n: int, win_s: float = 30.0, seed: int = 42,
) -> list[float]:
    """N random non-overlapping 30 s starts in [last_task_end+60, sentiometer_end-30]."""
    lo = last_task_end + 60.0
    hi = sentiometer_end - win_s
    if hi - lo < win_s * n:
        # Fall back to evenly spaced
        if hi - lo < win_s:
            return [lo]
        step = (hi - lo) / max(1, n)
        return [lo + i * step for i in range(n)]
    rng = np.random.default_rng(seed)
    chosen: list[float] = []
    attempts = 0
    while len(chosen) < n and attempts < n * 50:
        t = float(rng.uniform(lo, hi))
        if all(abs(t - c) >= win_s for c in chosen):
            chosen.append(t)
        attempts += 1
    chosen.sort()
    return chosen


def _slice_start_dur(stream, start_s: float, dur_s: float) -> np.ndarray:
    """Data slice relative to stream's own first sample."""
    return _slice_window(stream, start_s, dur_s)


def _plot_chin_eog_task_rest(
    bv_stream, cgx_stream, bv_labels_raw, cgx_labels,
    task_starts: dict[str, float], rest_starts: list[float],
    save_traces: Path, save_psd: Path, win_s: float = 30.0,
) -> None:
    """For the four CGX ExGa channels and the two offline EOG derivations,
    plot trace excerpts + PSD during task vs rest."""
    import matplotlib.pyplot as plt  # noqa: PLC0415
    # CGX ExGa channels.
    exga_idx = {}
    for name in ("ExGa 1", "ExGa 2", "ExGa 3", "ExGa 4"):
        if name in cgx_labels:
            exga_idx[name] = cgx_labels.index(name)
    exga_pretty = {
        # EOG ports: face leads were NOT placed in this session — these two
        # rows are floating / environmental.
        "ExGa 1": "ExGa 1 (EOG-L port; face lead not placed — floating)",
        "ExGa 2": "ExGa 2 (EOG-R port; face lead not placed — floating)",
        # Chin EMG: placed as designed (AASM submental bipolars).
        "ExGa 3": "ExGa 3 → ChinZ-Chin1 (submental primary, as designed)",
        "ExGa 4": "ExGa 4 → Chin2-Chin1prime (submental backup, as designed)",
    }

    # Offline EOG from BrainVision cap.
    bv_good = [j for j, l in enumerate(bv_labels_raw)
               if l.lower() not in ("triggerstream", "trigger")]
    bv_labs = [bv_labels_raw[j] for j in bv_good]
    i_fp1 = bv_labs.index("Fp1"); i_fp2 = bv_labs.index("Fp2")
    i_tp9 = bv_labs.index("TP9"); i_tp10 = bv_labs.index("TP10")

    # Prefer a cognitively-engaging task for the "Task" column — skip
    # the passive questionnaire (task00) if we have anything else.
    preferred = [c for c in task_starts if c != "task00"] or list(task_starts)
    task_code = preferred[0] if preferred else next(iter(task_starts))
    task_t0 = task_starts[task_code]
    rest_t0 = rest_starts[0] if rest_starts else task_t0
    task_title = _task_label(task_code)
    rest_title = "Rest / post-task (with Sentiometer still on arm)"

    # ---- Traces figure: 6 rows (ExGa1-4 + EOG-L + EOG-R) × 2 cols (task, rest)
    row_labels: list[tuple[str, str, np.ndarray, np.ndarray]] = []
    # ExGa rows — data from CGX, sliced at task_t0 and rest_t0.
    cgx_task = _slice_start_dur(cgx_stream, task_t0, win_s)
    cgx_rest = _slice_start_dur(cgx_stream, rest_t0, win_s)
    for ch_name in ("ExGa 1", "ExGa 2", "ExGa 3", "ExGa 4"):
        if ch_name not in exga_idx:
            continue
        j = exga_idx[ch_name]
        row_labels.append((
            exga_pretty[ch_name],
            ch_name,
            cgx_task[:, j] if cgx_task.ndim == 2 else np.array([]),
            cgx_rest[:, j] if cgx_rest.ndim == 2 else np.array([]),
        ))
    # EOG derivations rows — data from BrainVision cap.
    bv_task = _slice_start_dur(bv_stream, task_t0, win_s)[:, bv_good]
    bv_rest = _slice_start_dur(bv_stream, rest_t0, win_s)[:, bv_good]
    row_labels.append((
        "Fp1 − TP9  (offline EOG-L)", "EOG-L",
        bv_task[:, i_fp1] - bv_task[:, i_tp9],
        bv_rest[:, i_fp1] - bv_rest[:, i_tp9],
    ))
    row_labels.append((
        "Fp2 − TP10 (offline EOG-R)", "EOG-R",
        bv_task[:, i_fp2] - bv_task[:, i_tp10],
        bv_rest[:, i_fp2] - bv_rest[:, i_tp10],
    ))

    nrows = len(row_labels)
    fig, axes = plt.subplots(
        nrows, 2, figsize=(10, 1.6 * nrows + 1.0),
        sharey=False, sharex=True,
    )
    if nrows == 1:
        axes = np.array([axes])
    for r, (pretty, short, sig_task, sig_rest) in enumerate(row_labels):
        for c, (sig, col_name, color) in enumerate([
            (sig_task,
             f"{task_title}\ncentral 30 s @ t={task_t0:.0f}s",
             "#1f4f99"),
            (sig_rest,
             f"{rest_title}\n30 s @ t={rest_t0:.0f}s",
             "#1b5e3a"),
        ]):
            if sig.size == 0:
                axes[r, c].text(0.5, 0.5, "no data",
                                transform=axes[r, c].transAxes,
                                ha="center", va="center", fontsize=7,
                                color="grey")
                continue
            x = sig - float(np.mean(sig))
            t_axis = np.arange(x.size) / FS
            axes[r, c].plot(t_axis, x, lw=0.4, color=color)
            p2, p98 = np.percentile(x, [2.0, 98.0])
            pad = max(5.0, 0.2 * (p98 - p2))
            axes[r, c].set_ylim(p2 - pad, p98 + pad)
            axes[r, c].set_xlim(0, win_s)
            axes[r, c].grid(alpha=0.2, lw=0.3)
            axes[r, c].tick_params(labelsize=7)
            if r == 0:
                axes[r, c].set_title(col_name, fontsize=9)
            if c == 0:
                axes[r, c].set_ylabel(pretty, fontsize=8)
    axes[-1, 0].set_xlabel("time (s)"); axes[-1, 1].set_xlabel("time (s)")
    fig.suptitle(
        "Nicco — Chin / EOG electrodes: task vs rest (30 s excerpts)",
        fontsize=11, y=0.995,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    fig.savefig(save_traces, dpi=140); plt.close(fig)

    # ---- PSD figure: same 6 rows, overlay task / rest PSD per row.
    fig, axes = plt.subplots(
        nrows, 1, figsize=(9, 1.8 * nrows + 1.0), sharex=True,
    )
    if nrows == 1:
        axes = [axes]
    for ax, (pretty, short, sig_task, sig_rest) in zip(axes, row_labels):
        if sig_task.size > 0:
            f, p = welch_psd(sig_task, FS); ax.semilogy(f, p, lw=1.0,
                                                         color="#1f4f99",
                                                         label="Task")
        if sig_rest.size > 0:
            f, p = welch_psd(sig_rest, FS); ax.semilogy(f, p, lw=1.0,
                                                         color="#1b5e3a",
                                                         label="Rest")
        ax.set_xlim(0.5, 60); ax.set_ylabel(f"{short}\nPSD (µV²/Hz)",
                                            fontsize=8)
        ax.grid(alpha=0.3, which="both")
        for _n, lo, hi in BANDS:
            ax.axvspan(lo, hi, color="grey", alpha=0.04)
    axes[0].legend(loc="upper right", fontsize=8)
    axes[-1].set_xlabel("Frequency (Hz)")
    fig.suptitle(
        "Nicco — Chin / EOG electrodes: PSD task vs rest", fontsize=11, y=0.99,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(save_psd, dpi=140); plt.close(fig)


def _plot_sentiometer_task_rest(
    sento_stream, task_starts: dict[str, float], rest_starts: list[float],
    save_path: Path, win_s: float = 30.0,
) -> None:
    """Plot Sentiometer photodiode timeseries (PD1–PD5) at the central 30 s
    of each task and at matched rest windows.

    Layout: **one row per photodiode**, so PDs aren't squashed into a
    shared overlay. Columns = task windows (left block, blue) followed
    by rest windows (right block, green). Each cell is demeaned and
    independently scaled by its own 2nd–98th percentile so a loud PD
    doesn't dominate a quiet one.
    """
    labels = _channel_labels(sento_stream)
    pd_names = [l for l in labels if l.startswith("PD")]
    pd_idx = [labels.index(l) for l in pd_names]
    if not pd_idx:
        pd_idx = list(range(1, min(6, len(labels))))
        pd_names = [labels[i] for i in pd_idx]

    # Column layout: all tasks (in order) then all rests.
    task_codes = list(task_starts.keys())
    cols: list[tuple[str, float, str]] = []
    for code in task_codes:
        cols.append((f"{_task_label(code)}\ncentral 30 s @ t={task_starts[code]:.0f}s",
                     task_starts[code], "task"))
    for i, t in enumerate(rest_starts):
        cols.append((f"Rest {i+1}\n30 s @ t={t:.0f}s", t, "rest"))

    n_rows = len(pd_idx)
    n_cols = len(cols)
    fs = 500.0

    # Landscape-ish full-page figure — each cell ~1 in wide, 1.1 in tall.
    fig, axes = plt.subplots(
        n_rows, n_cols,
        figsize=(0.95 * n_cols + 0.4, 1.1 * n_rows + 1.4),
        sharex=False, sharey=False,
    )
    if n_rows == 1:
        axes = np.array([axes])
    if n_cols == 1:
        axes = axes.reshape(-1, 1)

    for r, (ci, pname) in enumerate(zip(pd_idx, pd_names)):
        for c, (title, t0, kind) in enumerate(cols):
            ax = axes[r, c]
            d = _slice_start_dur(sento_stream, t0, win_s)
            if d.size == 0 or d.ndim != 2 or ci >= d.shape[1]:
                ax.text(0.5, 0.5, "no data", transform=ax.transAxes,
                        ha="center", va="center", fontsize=6, color="grey")
                ax.set_xticks([]); ax.set_yticks([])
                continue
            x = d[:, ci].astype(float)
            x = x - float(np.mean(x))
            t = np.arange(x.size) / fs
            color = "#1f77b4" if kind == "task" else "#1b5e3a"
            ax.plot(t, x, lw=0.5, color=color)
            ax.set_xlim(0, win_s)
            # Per-panel adaptive y; floor at ±1 so truly flat panels
            # still show gridlines.
            p2, p98 = np.percentile(x, [2.0, 98.0])
            pad = max(1.0, 0.2 * (p98 - p2))
            lo, hi = p2 - pad, p98 + pad
            if hi - lo < 2.0:
                mid = 0.5 * (lo + hi); lo, hi = mid - 1.0, mid + 1.0
            ax.set_ylim(lo, hi)
            ax.grid(alpha=0.25, lw=0.3)
            ax.tick_params(labelsize=6)
            # Headers only on the first row.
            if r == 0:
                ax.set_title(title, fontsize=7)
            # PD label only on the first column.
            if c == 0:
                ax.set_ylabel(pname, fontsize=9, rotation=0,
                              ha="right", va="center")
            # X label only on the bottom row.
            if r == n_rows - 1:
                ax.set_xlabel("s", fontsize=7)
            else:
                ax.set_xticklabels([])
            # Corner amplitude tag.
            ax.text(
                0.98, 0.04,
                f"±{max(abs(lo), abs(hi)):.1f}",
                transform=ax.transAxes,
                ha="right", va="bottom", fontsize=5.5, color="#666",
            )

    # Subtle divider between task columns and rest columns, applied by
    # tinting the title row background differently.
    n_task_cols = len(task_codes)
    for c in range(n_cols):
        tint = "#e8efff" if c < n_task_cols else "#e8f2e8"
        # Draw a very light background span covering the top axes title
        # area — reportlab will crop, but tight_layout respects it.
    fig.suptitle(
        "Nicco — Sentiometer photodiodes on arm: central 30 s per task "
        "vs matched random rest windows",
        fontsize=11, y=0.995,
    )
    fig.text(
        0.01, 0.002,
        f"Blue = task windows; green = rest windows.  Each panel demeaned "
        f"and auto-scaled per PD × per window.  Device placed on the arm.",
        fontsize=7, color="#555",
    )
    fig.tight_layout(rect=(0, 0.015, 1, 0.97))
    fig.savefig(save_path, dpi=140, bbox_inches="tight")
    plt.close(fig)


# ----- main ---------------------------------------------------------------

def main() -> int:
    for subj, path in SUBJECT_XDFS.items():
        if not path.exists():
            raise SystemExit(f"Missing XDF for {subj}: {path}")

    subject = "NiccoTest"
    out_dir = output_dir_for(subject)
    diag = diag_dir_for(subject)

    print("Cross-subject metrics:")
    metrics: dict[str, SubjectMetrics] = {
        s: _compute_subject_metrics(s, p) for s, p in SUBJECT_XDFS.items()
    }

    print("\nSentiometer ON/OFF (Nicco):")
    oo = _compute_on_off(SUBJECT_XDFS["NiccoTest"])

    # ----- Load Nicco's full XDF once to compute task/rest windows and
    # access Sentiometer + CGX streams (used by sections 4 and 5).
    print("\nLoading Nicco XDF for task/rest windows …")
    streams, bv_eeg, bv_labels_raw = _load_bv(SUBJECT_XDFS["NiccoTest"])
    cgx = next(
        (s for s in streams
         if s["info"]["name"][0] == "CGX AIM Phys. Mon. AIM-0106"),
        None,
    )
    cgx_labels = _channel_labels(cgx) if cgx else []
    sento = next(
        (s for s in streams if s["info"]["name"][0] == "Sentiometer"),
        None,
    )
    sento_end_s = 0.0
    if sento is not None:
        sts = np.asarray(sento.get("time_stamps", []), dtype=float)
        bvts = np.asarray(bv_eeg.get("time_stamps", []), dtype=float)
        if sts.size and bvts.size:
            sento_end_s = float(sts[-1] - bvts[0])

    task_windows, last_marker_s = _task_windows_from_markers(
        streams, bv_eeg, sento_end_s,
    )
    task_starts_30s = _task_central_30s(task_windows)
    # Rest/sleep window = from "last marker across all streams" to just
    # before the Sentiometer stream ended (that's when the device came
    # off). A 60 s buffer at each end avoids boundary artifacts.
    rest_starts = _random_rest_windows(
        last_task_end=last_marker_s,
        sentiometer_end=sento_end_s,
        n=len(task_starts_30s) or 6,
        win_s=30.0,
    )
    print(f"  task windows: {len(task_windows)}; rest windows: {len(rest_starts)}")
    for code, t in task_starts_30s.items():
        print(f"    {code}: central 30 s at t={t:.1f}s")
    print(f"  rest window centres: {[round(t, 1) for t in rest_starts]}")

    pngs = {
        "xsubj_60hz": diag / "xsubj_60hz_snr.png",
        "xsubj_rms": diag / "xsubj_broadband_rms.png",
        "on_off_heatmap": diag / "nicco_on_off_fullspectrum_heatmap.png",
        "on_off_psd": diag / "nicco_sento_on_off_psd.png",
        "band20_bar": diag / "nicco_sento_on_off_20hz_delta.png",
        "on_off_sample_eeg": diag / "nicco_on_off_sample_eeg.png",
        "eog_traces": diag / "nicco_eog_traces.png",
        "eog_psd": diag / "nicco_eog_psd.png",
        "chin_eog_traces": diag / "nicco_chin_eog_task_rest_traces.png",
        "chin_eog_psd": diag / "nicco_chin_eog_task_rest_psd.png",
        "sentiometer_task_rest": diag / "nicco_sentiometer_task_rest.png",
    }

    print("\nRendering figures …")
    _plot_xsubj_60hz(metrics, pngs["xsubj_60hz"])
    _plot_xsubj_rms(metrics, pngs["xsubj_rms"])
    plot_log_ratio_heatmap(
        oo.freq, oo.psd_on, oo.psd_off, oo.labels,
        pngs["on_off_heatmap"],
        title=(
            "Nicco — Sentiometer ON vs OFF, log₁₀(PSD_ON / PSD_OFF)"
        ),
    )
    # 6 channels (front-L/R, mid-L/R, back-L/R) instead of 4 midline.
    _plot_overlaid_psds(oo, list(SAMPLE_CHANNELS), pngs["on_off_psd"])
    _plot_20hz_bar(oo, pngs["band20_bar"])
    plot_sample_epochs(
        data=np.concatenate([oo.data_on, oo.data_off], axis=0),
        fs=FS,
        labels=oo.labels,
        condition_epochs=[
            ("Sent-ON",  [0.0, 60.0, 120.0]),
            ("Sent-OFF", [NICCO_ON_DUR + 0.0,
                          NICCO_ON_DUR + 60.0,
                          NICCO_ON_DUR + 120.0]),
        ],
        save_path=pngs["on_off_sample_eeg"],
        title=(
            f"Nicco — Sentiometer ON vs OFF sample EEG "
            f"(6 channels × 3 + 3 epochs of {int(EPOCH_S)} s)"
        ),
        fig_height_per_row=1.6,
    )
    _plot_eog_traces(oo, pngs["eog_traces"])
    _plot_eog_psd(oo, pngs["eog_psd"])

    # Section 4 & 5 figures.
    if task_starts_30s:
        _plot_chin_eog_task_rest(
            bv_stream=bv_eeg, cgx_stream=cgx,
            bv_labels_raw=bv_labels_raw, cgx_labels=cgx_labels,
            task_starts=task_starts_30s, rest_starts=rest_starts,
            save_traces=pngs["chin_eog_traces"],
            save_psd=pngs["chin_eog_psd"],
        )
    if sento is not None and task_starts_30s:
        _plot_sentiometer_task_rest(
            sento_stream=sento,
            task_starts=task_starts_30s,
            rest_starts=rest_starts,
            save_path=pngs["sentiometer_task_rest"],
        )

    for k, p in pngs.items():
        print(f"  wrote {p}")

    pdf_path = out_dir / f"P013_{subject}_extended_report.pdf"
    print("\nBuilding PDF …")
    _build_pdf(
        subject, metrics, oo, pngs, pdf_path,
        task_starts_30s=task_starts_30s,
        rest_starts=rest_starts,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
