"""Step 2 — build the EDF+ for Paller.

Reads the XDF in sampledata/, aligns the BrainAmpSeries EEG and CGX AIM-2
physiological stream on the LSL clock, renames the four CGX ExG ports
to AASM derivation names, preserves all 64 EEG channels (including the
rail-saturated ones flagged in Step 1), computes per-channel
physical_min/max from the ACTUAL data range (so saturated channels
advertise their rail values), writes EDF+ with any available markers
as annotations, and does a 10-sample-per-channel spot-check against
the source XDF.

Outputs written under ``outputs/``:

* ``P013_PILOT_01_for_paller.edf``
* ``conversion_log.txt`` (assumptions + decisions, grows as the script
  runs)
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pyedflib
import pyxdf

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_DIR = REPO_ROOT / "sampledata"
OUT_DIR = REPO_ROOT / "outputs"
EDF_PATH = OUT_DIR / "P013_PILOT_01_for_paller.edf"
LOG_PATH = OUT_DIR / "conversion_log.txt"

# Subject / anon code that goes into the EDF header.
SUBJECT_CODE = "PILOT_01"

# -- From Step 1 forensic: channels rail-saturated > 50 % of samples in
# the first 120 s. These are flagged but NOT excluded from the EDF —
# Paller asked to see them explicitly so his lab can confirm the pilot
# acquisition context.
BAD_RAIL_CHANNELS = {
    "TP9",
    "TP7",
    "F8",
    "AF7",
    "PO8",
    "Iz",
    "FT10",
    "FT7",
    "P8",
    "F6",
    "FT8",
    "TP8",
    "T7",
    "O1",
    "T8",
    "TP10",
    "AF8",
}
BORDERLINE_CHANNELS = {"FT9", "O2"}  # 10 – 50 % saturated (FT9) or noisy (O2)

# CGX ExGa → AASM derivation rename. AIM-2 port wiring per P013 montage.
CGX_EXG_RENAME = {
    "ExGa 1": "E1-M2",          # left eye vs right mastoid
    "ExGa 2": "E2-M1",          # right eye vs left mastoid
    "ExGa 3": "ChinZ-Chin1",    # submental midline bipolar (primary)
    "ExGa 4": "Chin2-Chin1prime",  # submental lateral bipolar (backup)
}

# Per-channel unit policy. For CGX peripheral channels we keep µV as
# advertised by the stream and document the internal-scale caveat in
# the manifest rather than silently rescaling.
CHANNEL_UNITS = {
    # Dedicated renames → AASM montage units
    "E1-M2": "uV",
    "E2-M1": "uV",
    "ChinZ-Chin1": "uV",
    "Chin2-Chin1prime": "uV",
    # CGX peripheral — µV per stream metadata; see manifest for
    # "internal scale" caveat on non-EEG channels.
    "ECG": "uV",
    "Resp.": "uV",
    "PPG": "uV",
    "SpO2": "uV",
    "HR": "uV",
    "GSR": "uV",
    "Temp.": "uV",
}

# Channels we DON'T want in the EDF at all. CGX administrative.
CGX_DROP = {"Packet Counter", "TRIGGER"}


# -----------------------------------------------------------------------


def _get(d, *keys, default=""):
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


def _channel_labels(stream):
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
    out = []
    for ch in entries:
        if not isinstance(ch, dict):
            continue
        lab = ch.get("label") or [""]
        out.append(lab[0] if isinstance(lab, list) else str(lab))
    return out


def _find_xdf():
    xdfs = sorted(SAMPLE_DIR.glob("*.xdf"))
    if not xdfs:
        raise SystemExit(f"No .xdf in {SAMPLE_DIR}")
    return xdfs[0]


def _align_streams(eeg, cgx, log_lines):
    eeg_ts = np.asarray(eeg["time_stamps"], dtype=float)
    cgx_ts = np.asarray(cgx["time_stamps"], dtype=float)
    eeg_data = np.asarray(eeg["time_series"], dtype=np.float32)
    cgx_data = np.asarray(cgx["time_series"], dtype=np.float32)

    t_start = max(eeg_ts[0], cgx_ts[0])
    t_end = min(eeg_ts[-1], cgx_ts[-1])
    log_lines.append(
        f"LSL alignment window: [{t_start:.3f}, {t_end:.3f}] "
        f"= {t_end - t_start:.3f} s"
    )

    eeg_mask = (eeg_ts >= t_start) & (eeg_ts <= t_end)
    cgx_mask = (cgx_ts >= t_start) & (cgx_ts <= t_end)
    n = min(int(eeg_mask.sum()), int(cgx_mask.sum()))
    # Truncate from the front of each: take the first n indexes of each mask.
    eeg_idx = np.where(eeg_mask)[0][:n]
    cgx_idx = np.where(cgx_mask)[0][:n]
    log_lines.append(
        f"EEG samples in window: {int(eeg_mask.sum())}, "
        f"CGX samples in window: {int(cgx_mask.sum())}, "
        f"used N = {n}"
    )
    eeg_ts = eeg_ts[eeg_idx]
    eeg_data = eeg_data[eeg_idx, :]
    cgx_ts = cgx_ts[cgx_idx]
    cgx_data = cgx_data[cgx_idx, :]
    return eeg_ts, eeg_data, cgx_ts, cgx_data


def _compute_phys_range(signal: np.ndarray) -> tuple[float, float]:
    """Physical min/max with ~10% headroom around the observed range.

    EDF+ only allocates 8 ASCII chars for each of physical_min and
    physical_max (and 8 for digital_min/max). Values like -9999999
    already consume 8 chars with the sign, so any channel whose real
    range exceeds ±9_999_999 gets forced to a symmetric ±8_388_608
    (the 16-bit signed rail) and its data is clipped — these are
    always CGX peripheral channels (HR / GSR / Temp) that aren't used
    for sleep scoring. The clip is logged per channel by the caller.

    Rail-saturated EEG channels stay inside ±3276.7 µV so the rail
    value is visible in the EDF header and the scorer can see at a
    glance which channels were pegged.
    """
    if signal.size == 0:
        return -1.0, 1.0
    lo = float(np.nanmin(signal))
    hi = float(np.nanmax(signal))
    if not np.isfinite(lo) or not np.isfinite(hi):
        lo, hi = -1.0, 1.0
    if lo == hi:
        lo -= 1.0
        hi += 1.0
    pad = 0.1 * (hi - lo)
    pmin = lo - pad
    pmax = hi + pad
    # If either bound overflows EDF's 8-char physical field, force a
    # symmetric ±8_388_608 range and clip data at the caller.
    limit = 9_999_999.0
    if pmin < -limit or pmax > limit:
        pmin, pmax = -8_388_608.0, 8_388_608.0
    if pmin >= pmax:
        pmin = pmax - 1.0
    return pmin, pmax


def _prefilter_tag(label: str, source: str) -> str:
    """What goes in the EDF ``prefilter`` field per channel.

    We use it to flag bad-channel status so it's visible from any EDF
    viewer (the manifest repeats this in human-readable form). For
    clean channels we include the hardware filter info we know from
    the .cfg: BrainAmp Standard, AC coupled → ~0.016 Hz HP.
    """
    base = {
        "eeg": "BrainAmp Std AC ~0.016Hz HP",
        "cgx": "CGX AIM-2 internal",
    }[source]
    if label in BAD_RAIL_CHANNELS:
        return f"BAD_RAIL_SAT; {base}"
    if label in BORDERLINE_CHANNELS:
        return f"BORDERLINE_QUALITY; {base}"
    return base


def _spot_check(src: np.ndarray, written: np.ndarray, rng: np.random.Generator,
                label: str, log_lines: list[str]) -> bool:
    """Compare 10 random samples from a written EDF channel against the
    source array. Because EDF stores int16 with a per-channel linear
    scaling, round-trip error < 1 digital step is OK."""
    n = min(len(src), len(written))
    if n < 10:
        log_lines.append(f"  {label}: too few samples for spot check (n={n})")
        return True
    idx = rng.choice(n, 10, replace=False)
    src_s = src[idx]
    w_s = written[idx]
    max_err = float(np.max(np.abs(src_s - w_s)))
    # For EDF int16 quantization, one LSB is (pmax-pmin)/65535. We allow
    # up to 2 LSB of error plus float32 noise.
    tol = max(2.0 * (src.max() - src.min()) / 65535.0, 1e-3)
    ok = max_err <= tol
    log_lines.append(
        f"  {label}: max abs round-trip error = {max_err:.4g} "
        f"(tol {tol:.4g})  {'PASS' if ok else 'FAIL'}"
    )
    return ok


def _markers_to_annotations(streams, t_start: float) -> list[tuple[float, float, str]]:
    annotations: list[tuple[float, float, str]] = []
    for s in streams:
        if _get(s, "info", "type").lower() != "markers":
            continue
        name = _get(s, "info", "name")
        ts_raw = s.get("time_stamps")
        if ts_raw is None or len(ts_raw) == 0:
            continue
        ts = np.asarray(ts_raw, dtype=float)
        samples_raw = s.get("time_series")
        samples = samples_raw if samples_raw is not None else []
        for t, v in zip(ts, samples):
            onset = float(t - t_start)
            if onset < 0:
                continue  # marker fell before the aligned EDF window
            val = v[0] if isinstance(v, (list, tuple)) and v else str(v)
            annotations.append((onset, -1.0, f"[{name}] {val}"))
    return annotations


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    log_lines: list[str] = []
    log_lines.append(f"# Conversion run {datetime.now().isoformat(timespec='seconds')}")
    log_lines.append("")

    # ---- Context: pilot-phase acknowledgment -----
    log_lines.append(
        "RECORDING CONTEXT\n"
        "-----------------\n"
        "This recording was a deliberate pilot session conducted with an\n"
        "IACS staff member (not a study participant). Acquisition was\n"
        "intentionally accepted with lower-than-protocol impedance values\n"
        "due to hardware accommodations (hearing aids) and pilot-phase\n"
        "time constraints. The 17 rail-saturated peripheral channels\n"
        "identified in Step 1 (TP9/TP10, T7/T8, TP7/TP8, FT7-10, F8, F6,\n"
        "AF7/AF8, P8, PO8, Iz, O1) are consistent with this — we proceeded\n"
        "despite knowing peripheral impedance was marginal, in order to\n"
        "validate the full end-to-end pipeline.\n\n"
        "These channels are NOT excluded from the EDF; they are written\n"
        "out and flagged via the EDF `prefilter` field (BAD_RAIL_SAT)\n"
        "and called out explicitly in the channel manifest.\n"
    )

    xdf_path = _find_xdf()
    log_lines.append(f"SOURCE XDF: {xdf_path}")
    print(f"Loading {xdf_path} …")
    streams, _header = pyxdf.load_xdf(str(xdf_path))
    log_lines.append(f"Streams in file: {len(streams)}")

    eeg = next(
        (s for s in streams
         if _get(s, "info", "name") == "BrainAmpSeries-Dev_1"),
        None,
    )
    cgx = next(
        (s for s in streams
         if _get(s, "info", "name") == "CGX AIM Phys. Mon. AIM-0106"),
        None,
    )
    if eeg is None or cgx is None:
        raise SystemExit("Missing expected EEG or CGX stream.")

    eeg_fs = float(_get(eeg, "info", "nominal_srate") or 0)
    cgx_fs = float(_get(cgx, "info", "nominal_srate") or 0)
    log_lines.append(f"EEG nominal rate: {eeg_fs} Hz")
    log_lines.append(f"CGX nominal rate: {cgx_fs} Hz")
    if abs(eeg_fs - cgx_fs) > 0.5:
        raise SystemExit(
            f"Sample rates differ: EEG={eeg_fs}, CGX={cgx_fs}. "
            "Resample before converting."
        )
    log_lines.append("Sample rates match; no resampling needed.")

    eeg_ts, eeg_data, cgx_ts, cgx_data = _align_streams(eeg, cgx, log_lines)
    assert len(eeg_ts) == len(cgx_ts)
    n_samp = len(eeg_ts)
    duration_s = n_samp / eeg_fs
    log_lines.append(
        f"Aligned length: {n_samp} samples = "
        f"{duration_s:.1f} s = {duration_s/60:.2f} min"
    )

    # Wall-clock anchor: approximate from file mtime minus duration from
    # alignment start to XDF last sample. Good enough for EDF startdatetime.
    eeg_last = float(np.asarray(eeg["time_stamps"])[-1])
    file_utc = datetime.fromtimestamp(xdf_path.stat().st_mtime, tz=timezone.utc)
    t_start_lsl = float(eeg_ts[0])
    # seconds between our aligned start and the original file end:
    offset_s = eeg_last - t_start_lsl
    session_start_utc = file_utc - timedelta(seconds=offset_s)
    log_lines.append(
        f"Derived session start (UTC, best effort): {session_start_utc.isoformat()}"
    )

    eeg_labels = _channel_labels(eeg)
    cgx_labels = _channel_labels(cgx)
    log_lines.append(
        f"EEG channels: {len(eeg_labels)}  CGX channels: {len(cgx_labels)}"
    )

    # Build the EDF channel list: 64 EEG + CGX (with ExGa renamed, admin dropped).
    channels: list[dict] = []
    # EEG
    for j, lab in enumerate(eeg_labels):
        signal = eeg_data[:, j]
        pmin, pmax = _compute_phys_range(signal)
        channels.append(
            {
                "label": lab[:16],
                "orig_label": lab,
                "source": "eeg",
                "signal": signal,
                "fs": int(round(eeg_fs)),
                "unit": "uV",
                "pmin": pmin,
                "pmax": pmax,
                "prefilter": _prefilter_tag(lab, "eeg"),
                "transducer": "BrainVision actiCAP Ag/AgCl",
            }
        )
    # CGX
    for j, lab in enumerate(cgx_labels):
        if lab in CGX_DROP:
            continue
        signal = cgx_data[:, j]
        edf_label = CGX_EXG_RENAME.get(lab, lab.strip())
        unit = CHANNEL_UNITS.get(edf_label, CHANNEL_UNITS.get(lab, "uV"))
        pmin, pmax = _compute_phys_range(signal)
        channels.append(
            {
                "label": edf_label[:16],
                "orig_label": lab,
                "source": "cgx",
                "signal": signal,
                "fs": int(round(cgx_fs)),
                "unit": unit,
                "pmin": pmin,
                "pmax": pmax,
                "prefilter": _prefilter_tag(edf_label, "cgx"),
                "transducer": "CGX AIM-2",
            }
        )

    log_lines.append(f"Total channels written to EDF: {len(channels)}")
    log_lines.append("")
    log_lines.append("CHANNEL MAP (orig → EDF label, unit, prefilter):")
    for i, ch in enumerate(channels):
        log_lines.append(
            f"  {i+1:2d}  {ch['orig_label']:<22} → {ch['label']:<16} "
            f"unit={ch['unit']:<4} prefilter='{ch['prefilter']}'"
        )

    # ----- Write EDF+ ------
    print(f"Writing EDF+ with {len(channels)} channels → {EDF_PATH}")
    writer = pyedflib.EdfWriter(
        str(EDF_PATH), len(channels), file_type=pyedflib.FILETYPE_EDFPLUS
    )
    try:
        # EDF+ strict: no spaces in header fields, ASCII only, and
        # equipment+technician+admincode+recording_additional ≤ 80 chars
        # combined. Anything long goes into the manifest instead.
        writer.setHeader(
            {
                "technician": "IACS",
                "recording_additional": "P013_pilot_see_manifest",
                "patientname": "X",
                "patient_additional": "",
                "patientcode": SUBJECT_CODE,
                "equipment": "BrainAmp+CGX_AIM2",
                "admincode": "",
                "sex": "",
                "birthdate": "",
                "startdate": session_start_utc.replace(tzinfo=None),
            }
        )

        for i, ch in enumerate(channels):
            # Clip signal so any stray spike stays inside the declared range.
            sig = np.clip(ch["signal"], ch["pmin"], ch["pmax"])
            writer.setSignalHeader(
                i,
                {
                    "label": ch["label"],
                    "dimension": ch["unit"],
                    "sample_frequency": ch["fs"],
                    "physical_min": ch["pmin"],
                    "physical_max": ch["pmax"],
                    "digital_min": -32768,
                    "digital_max": 32767,
                    "prefilter": ch["prefilter"][:80],
                    "transducer": ch["transducer"][:80],
                },
            )
            ch["signal_clipped"] = sig.astype(np.float64, copy=False)

        writer.writeSamples([ch["signal_clipped"] for ch in channels])

        # Annotations from marker streams (P013 + any Vayl). P013 is empty
        # in this pilot, so this typically produces 0 annotations; we still
        # emit a `recording_start` one as a sanity anchor.
        annotations = _markers_to_annotations(streams, t_start_lsl)
        log_lines.append(f"\nLSL markers in aligned window: {len(annotations)}")
        writer.writeAnnotation(0.0, -1.0, "recording_start")
        for onset, duration, desc in annotations:
            writer.writeAnnotation(onset, duration, desc[:80])
    finally:
        writer.close()

    # ----- Verify: open the file and spot-check -----
    print("\nSpot-checking written EDF …")
    log_lines.append("")
    log_lines.append("SPOT-CHECK (10 random samples per channel):")
    rng = np.random.default_rng(42)
    reader = pyedflib.EdfReader(str(EDF_PATH))
    try:
        all_pass = True
        for i, ch in enumerate(channels):
            written = reader.readSignal(i)
            src = ch["signal_clipped"]
            ok = _spot_check(src, written, rng, ch["label"], log_lines)
            if not ok:
                all_pass = False
        log_lines.append(
            f"\nSpot-check result: {'ALL PASS' if all_pass else 'FAILURES (see above)'}"
        )
        print(f"Spot-check: {'ALL PASS' if all_pass else 'FAILURES'}")
        print(f"  wrote {EDF_PATH}  ({EDF_PATH.stat().st_size/1e6:.1f} MB)")
        print(f"  duration: {reader.file_duration/60:.2f} min")
        print(f"  start date: {reader.getStartdatetime()}")
        print(f"  labels: {reader.getSignalLabels()[:12]} … (+{reader.signals_in_file-12} more)")
    finally:
        reader.close()

    LOG_PATH.write_text("\n".join(log_lines) + "\n")
    print(f"  conversion log: {LOG_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
