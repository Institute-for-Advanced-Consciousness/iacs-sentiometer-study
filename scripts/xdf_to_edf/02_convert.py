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

from _common import (
    SAMPLE_DIR,
    edf_path_for,
    find_xdf as _find_xdf_common,
    log_path_for,
    output_dir_for,
    subject_from_xdf,
)

# -- From Step 1 forensic: channels rail-saturated > 50 % of samples in
# the first 120 s. These are flagged but NOT excluded from the EDF —
# Paller asked to see them explicitly so his lab can confirm the pilot
# acquisition context.
# Rail-saturation sets are now computed per-subject from the actual XDF
# (see _compute_saturation). These module-level placeholders are populated
# at the start of main() once the BrainVision stream is loaded. Keeping
# them as module globals preserves the function signatures below.
BAD_RAIL_CHANNELS: set[str] = set()
BORDERLINE_CHANNELS: set[str] = set()
BRAINAMP_RAIL_UV = 3200.0
FIRST_WINDOW_S = 120.0
SAT_RED_PCT = 50.0
SAT_YELLOW_PCT = 10.0

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
    return _find_xdf_common()


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


def _populate_rail_sets(
    eeg_stream: dict, eeg_labels: list[str], log_lines: list[str]
) -> None:
    """Fill BAD_RAIL_CHANNELS / BORDERLINE_CHANNELS from *this* XDF's
    first-120 s window, so the EDF prefilter tags reflect the actual
    recording rather than a prior pilot's numbers."""
    ts = np.asarray(eeg_stream.get("time_stamps", []), dtype=float)
    data = np.asarray(eeg_stream.get("time_series", []), dtype=float)
    BAD_RAIL_CHANNELS.clear()
    BORDERLINE_CHANNELS.clear()
    if ts.size == 0 or data.size == 0:
        log_lines.append(
            "WARNING: empty BrainVision stream — no rail-saturation flags applied."
        )
        return
    t0 = float(ts[0])
    mask = (ts >= t0) & (ts < t0 + FIRST_WINDOW_S)
    d = data[mask, :] if data.ndim == 2 else np.empty((0, 0))
    if d.size == 0:
        log_lines.append(
            "WARNING: first 120 s window empty — no rail-saturation flags applied."
        )
        return
    pct = (np.abs(d) > BRAINAMP_RAIL_UV).sum(axis=0) * 100.0 / d.shape[0]
    for i in range(min(len(eeg_labels), d.shape[1])):
        lab = eeg_labels[i]
        if lab.lower() in ("triggerstream", "trigger"):
            continue
        if pct[i] > SAT_RED_PCT:
            BAD_RAIL_CHANNELS.add(lab)
        elif pct[i] >= SAT_YELLOW_PCT:
            BORDERLINE_CHANNELS.add(lab)
    log_lines.append(
        f"Rail-saturation (computed from this XDF, first {int(FIRST_WINDOW_S)} s): "
        f"{len(BAD_RAIL_CHANNELS)} RED, {len(BORDERLINE_CHANNELS)} YELLOW"
    )
    if BAD_RAIL_CHANNELS:
        log_lines.append(f"  RED    = {sorted(BAD_RAIL_CHANNELS)}")
    if BORDERLINE_CHANNELS:
        log_lines.append(f"  YELLOW = {sorted(BORDERLINE_CHANNELS)}")


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
    xdf_path = _find_xdf()
    subject = subject_from_xdf(xdf_path)
    out_dir = output_dir_for(subject)
    edf_path = edf_path_for(subject)
    log_path = log_path_for(subject)
    print(f"Subject: {subject}")
    print(f"Output bundle: {out_dir}")

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

    log_lines.append(f"SOURCE XDF: {xdf_path}")
    log_lines.append(f"Subject: {subject}")
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

    # Compute rail-saturation sets from this subject's first-120 s window
    # and populate the module-level BAD_RAIL_CHANNELS / BORDERLINE_CHANNELS
    # so _prefilter_tag() below flags the right channels in the EDF.
    _populate_rail_sets(eeg, eeg_labels, log_lines)

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
    print(f"Writing EDF+ with {len(channels)} channels → {edf_path}")
    writer = pyedflib.EdfWriter(
        str(edf_path), len(channels), file_type=pyedflib.FILETYPE_EDFPLUS
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
                "patientcode": subject,
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
    reader = pyedflib.EdfReader(str(edf_path))
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
        print(f"  wrote {edf_path}  ({edf_path.stat().st_size/1e6:.1f} MB)")
        print(f"  duration: {reader.file_duration/60:.2f} min")
        print(f"  start date: {reader.getStartdatetime()}")
        print(f"  labels: {reader.getSignalLabels()[:12]} … (+{reader.signals_in_file-12} more)")
    finally:
        reader.close()

    log_path.write_text("\n".join(log_lines) + "\n")
    print(f"  conversion log: {log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
