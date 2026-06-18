"""Derive a standalone cardiac/HRV bundle from a session XDF.

The cardiac analog of ``sentiometer_derive.py``. Loads ONLY the CGX
physiology stream (for the ``ECG`` and ``Resp.`` channels) and
``P013_Task_Markers``, detects R-peaks with neurokit2, and builds:

Continuous sliding-window metrics (``cardiac_timeseries.parquet``), one
row per 5 s step, each summarising a 120 s window ENDING... no — CENTERED
is ambiguous; we use a RIGHT-ALIGNED (trailing) window so each sample
reflects "the last 120 s", which is causal and matches how HRV is read
during a task. Columns, all on the synchronised LSL clock:
  * ``t_lsl``    — window centre... see WIN_ALIGN below (LSL seconds)
  * ``t_rel_s``  — seconds relative to ``session_start``
  * ``HR``       — heart rate, bpm (60000 / mean RR)
  * ``SDNN``     — std of RR within window, ms (overall variability)
  * ``LF``       — 0.04-0.15 Hz power (ms^2), Welch on 4 Hz-resampled RR
  * ``HF``       — 0.15-0.40 Hz power (ms^2), respiration-linked
  * ``LF_HF``    — LF/HF ratio (sympatho-vagal balance proxy)
  * ``RespRate`` — breaths/min from the CGX Resp. channel (companion series)
  * ``n_beats``  — beats contributing to this window (QC)
  * ``task``     — task block label for the window anchor time

Beat-level table (``cardiac_beats.parquet``): one row per detected beat:
  ``t_lsl``, ``t_rel_s``, ``rr_ms`` (RR to previous beat), ``inst_hr``,
  ``artifact`` (RR outside [300,2000] ms). Used for event-locked
  instantaneous-HR responses (e.g. collision-evoked HR change).

Markers (``cardiac_markers.parquet``): same shape as the Sentiometer
bundle's markers.

Plus ``cardiac_meta.json`` and ``README.md``.

WINDOW: 120 s wide, 5 s step, RIGHT-ALIGNED (trailing). The first full
window lands ~120 s into the recording; since the session opens with the
~15 min questionnaire (task00), HRV is fully stabilised before Task 01.

Usage (deps injected per-run; not project deps):
    uv run --with neurokit2 --with pandas --with pyarrow --with scipy \
        python scripts/cardiac_derive.py [session.xdf]
Default XDF: sample-data/P013_S01.xdf
"""

from __future__ import annotations

import json
import os
import sys
import warnings
from pathlib import Path

import neurokit2 as nk
import numpy as np
import pandas as pd
from scipy import signal as sps

warnings.filterwarnings("ignore")
np.seterr(all="ignore")
trapz = np.trapezoid

REPO = Path(__file__).resolve().parents[1]
XDF = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "sample-data" / "P013_S01.xdf"
SUBJECT = os.environ.get("SENT_SUBJECT", XDF.stem)
OUTDIR = REPO / "outputs" / SUBJECT / "cardiac"

CGX_NAME = "CGX AIM Phys. Mon. AIM-0106"
WIN_S = 120.0          # sliding-window width
STEP_S = 5.0           # window step
RR_MIN_MS, RR_MAX_MS = 300.0, 2000.0   # physiological RR bounds
LF_BAND = (0.04, 0.15)
HF_BAND = (0.15, 0.40)
RESAMP_HZ = 4.0        # RR resampling rate for spectral HRV


def cgx_channel(stream, name):
    labels = [c["label"][0].strip() for c in
              stream["info"]["desc"][0]["channels"][0]["channel"]]
    return labels.index(name)


def freq_hrv(rr_ms, rr_t_s):
    """LF, HF power (ms^2) from an RR series via Welch on 4 Hz resample.

    Returns NaN if a large RR gap (dropped/ectopic beats) would force the
    linear interpolation to invent >a few seconds of fake RR dynamics —
    that would corrupt the band powers. With clean ECG this never triggers.
    """
    if len(rr_ms) < 20:
        return np.nan, np.nan
    if np.max(np.diff(rr_t_s)) > 5.0:    # gap > 5 s of missing beats
        return np.nan, np.nan
    ti = np.arange(rr_t_s[0], rr_t_s[-1], 1.0 / RESAMP_HZ)
    if len(ti) < 16:
        return np.nan, np.nan
    ri = np.interp(ti, rr_t_s, rr_ms)
    ri = ri - ri.mean()
    nper = min(len(ri), int(RESAMP_HZ * 60))  # up to 60 s segments
    f, P = sps.welch(ri, fs=RESAMP_HZ, nperseg=nper)
    lf = trapz(P[(f >= LF_BAND[0]) & (f < LF_BAND[1])],
               f[(f >= LF_BAND[0]) & (f < LF_BAND[1])])
    hf = trapz(P[(f >= HF_BAND[0]) & (f < HF_BAND[1])],
               f[(f >= HF_BAND[0]) & (f < HF_BAND[1])])
    return float(lf), float(hf)


def resp_rate_series(resp, fs, ts, win_centres_lsl, t0_lsl):
    """Breaths/min in a trailing WIN_S window ending at each centre time."""
    # bandpass 0.1-0.5 Hz to isolate respiration, then count peaks per window
    sos = sps.butter(3, [0.1, 0.6], "bandpass", fs=fs, output="sos")
    rclean = sps.sosfiltfilt(sos, resp - resp.mean())
    out = []
    for c in win_centres_lsl:
        a_t, b_t = c - WIN_S, c  # trailing window
        a = int(np.searchsorted(ts, a_t, "left"))
        b = int(np.searchsorted(ts, b_t, "left"))
        if b - a < int(10 * fs):
            out.append(np.nan); continue
        seg = rclean[a:b]
        pk, _ = sps.find_peaks(seg, distance=int(1.5 * fs))
        out.append(len(pk) / (WIN_S / 60.0))
    return np.asarray(out)


def main():
    if not XDF.exists():
        raise SystemExit(f"XDF not found: {XDF}")
    OUTDIR.mkdir(parents=True, exist_ok=True)

    import pyxdf
    streams, _ = pyxdf.load_xdf(
        str(XDF),
        select_streams=[{"name": CGX_NAME}, {"name": "P013_Task_Markers"}],
        dejitter_timestamps=True, synchronize_clocks=True,
    )
    by = {s["info"]["name"][0]: s for s in streams}
    if CGX_NAME not in by or "P013_Task_Markers" not in by:
        raise SystemExit(f"Missing streams; got {list(by)}")
    cgx, mk = by[CGX_NAME], by["P013_Task_Markers"]

    X = np.asarray(cgx["time_series"], float)
    ts = np.asarray(cgx["time_stamps"], float)
    fs = float(1.0 / np.median(np.diff(ts)))
    ecg = X[:, cgx_channel(cgx, "ECG")]
    resp = X[:, cgx_channel(cgx, "Resp.")]
    if ts.size == 0:
        raise SystemExit("CGX stream loaded empty.")

    # ---- R-peak detection on the full recording -------------------------
    ecg_clean = nk.ecg_clean(ecg, sampling_rate=fs)
    _, info = nk.ecg_peaks(ecg_clean, sampling_rate=fs)
    rpk = np.asarray(info["ECG_R_Peaks"], int)
    if len(rpk) < 100:
        raise SystemExit(f"Too few R-peaks detected: {len(rpk)}")
    rpk_lsl = ts[rpk]
    rr_ms = np.diff(rpk_lsl) * 1000.0            # RR interval to previous beat
    beat_lsl = rpk_lsl[1:]                        # beats that have an RR
    inst_hr = 60000.0 / rr_ms
    artifact = (rr_ms < RR_MIN_MS) | (rr_ms > RR_MAX_MS)

    # ---- markers + session anchor ---------------------------------------
    mlabels = [r[0] for r in mk["time_series"]]
    mts = np.asarray(mk["time_stamps"], float)
    sess0 = mts[mlabels.index("session_start")] if "session_start" in mlabels else ts.min()
    sess_end = mts[mlabels.index("session_end")] if "session_end" in mlabels else ts.max()

    def parse(label):
        if label.startswith("{"):
            try:
                return ("task05", str(json.loads(label).get("event", "json")), True)
            except json.JSONDecodeError:
                return ("other", "json", True)
        if label.startswith("task") and "_" in label and label[4:6].isdigit():
            return (label[:6], label[7:], False)
        if label.startswith(("session", "participant")):
            return ("meta", label, False)
        return ("other", label, False)

    parsed = [parse(x) for x in mlabels]
    markers_df = pd.DataFrame({
        "t_lsl": mts, "t_rel_s": mts - sess0, "label": mlabels,
        "task": [p[0] for p in parsed], "event": [p[1] for p in parsed],
        "is_json": [p[2] for p in parsed],
    })

    # ---- task block lookup for a given lsl time -------------------------
    task_windows = []
    for p in ("task00", "task01", "task02", "task03", "task04", "task05"):
        if f"{p}_start" in mlabels and f"{p}_end" in mlabels:
            task_windows.append((p, mts[mlabels.index(f"{p}_start")],
                                 mts[mlabels.index(f"{p}_end")]))

    # The nap runs from task05_end to the end of recording (there is no
    # session_end marker before sleep), so tag everything after task05_end
    # as "nap" rather than letting it fall into between_tasks.
    t05_end = mts[mlabels.index("task05_end")] if "task05_end" in mlabels else sess_end

    def task_at(t_lsl):
        # window is right-aligned [c-WIN_S, c); label by its right edge with a
        # right-open task interval [a,b) so a boundary window isn't mislabelled.
        if t_lsl < sess0:
            return "pre_session"
        if t_lsl > t05_end:
            return "nap"
        for p, a, b in task_windows:
            if a <= t_lsl < b:
                return p
        return "between_tasks"

    # ---- sliding-window HRV (trailing window) ---------------------------
    # window centres step every STEP_S; window covers [c-WIN_S, c]
    rec_start, rec_end = ts[0], ts[-1]
    centres = np.arange(rec_start + WIN_S, rec_end, STEP_S)
    rows = []
    # use only physiological RR for HRV; keep beat times aligned
    good = ~artifact
    g_t, g_rr = beat_lsl[good], rr_ms[good]
    for c in centres:
        m = (g_t >= c - WIN_S) & (g_t < c)
        r = g_rr[m]; rt = g_t[m]
        if len(r) < 20:
            rows.append((c, np.nan, np.nan, np.nan, np.nan, np.nan, 0))
            continue
        hr = 60000.0 / np.mean(r)
        sdnn = float(np.std(r, ddof=1))
        lf, hf = freq_hrv(r, rt - rt[0])
        lfhf = lf / hf if (hf and hf > 0) else np.nan
        rows.append((c, hr, sdnn, lf, hf, lfhf, len(r)))
    arr = np.array(rows, float)
    centre_lsl = arr[:, 0]

    rr_table = pd.DataFrame({
        "t_lsl": centre_lsl,
        "t_rel_s": centre_lsl - sess0,
        "HR": arr[:, 1].astype(np.float32),
        "SDNN": arr[:, 2].astype(np.float32),
        "LF": arr[:, 3].astype(np.float32),
        "HF": arr[:, 4].astype(np.float32),
        "LF_HF": arr[:, 5].astype(np.float32),
        "n_beats": arr[:, 6].astype(np.int32),
    })
    # respiration rate companion (same window centres)
    rr_table["RespRate"] = resp_rate_series(resp, fs, ts, centre_lsl, sess0).astype(np.float32)
    rr_table["task"] = pd.Categorical([task_at(c) for c in centre_lsl])

    # ---- beat-level table -----------------------------------------------
    beats_df = pd.DataFrame({
        "t_lsl": beat_lsl,
        "t_rel_s": beat_lsl - sess0,
        "rr_ms": rr_ms.astype(np.float32),
        "inst_hr": inst_hr.astype(np.float32),
        "artifact": artifact,
    })

    # ---- write ----------------------------------------------------------
    ts_path = OUTDIR / "cardiac_timeseries.parquet"
    bt_path = OUTDIR / "cardiac_beats.parquet"
    mk_path = OUTDIR / "cardiac_markers.parquet"
    rr_table.to_parquet(ts_path, index=False, engine="pyarrow", compression="snappy")
    beats_df.to_parquet(bt_path, index=False, engine="pyarrow", compression="snappy")
    markers_df.to_parquet(mk_path, index=False, engine="pyarrow", compression="snappy")

    valid = rr_table.dropna(subset=["HR"])
    meta = {
        "source_xdf": str(XDF), "subject": SUBJECT,
        "ecg_channel": "ECG", "resp_channel": "Resp.", "fs_hz": fs,
        "n_beats": int(len(beat_lsl)),
        "artifact_fraction": float(artifact.mean()),
        "median_hr_bpm": float(np.nanmedian(inst_hr[~artifact])),
        "window_s": WIN_S, "step_s": STEP_S, "window_align": "trailing",
        "lf_band_hz": LF_BAND, "hf_band_hz": HF_BAND,
        "n_windows": int(len(rr_table)), "n_windows_valid": int(len(valid)),
        "session_start_lsl": float(sess0), "session_end_lsl": float(sess_end),
        "metrics": ["HR", "SDNN", "LF", "HF", "LF_HF", "RespRate"],
        "recording_span_min": float((ts[-1] - ts[0]) / 60),
    }
    (OUTDIR / "cardiac_meta.json").write_text(json.dumps(meta, indent=2))

    (OUTDIR / "README.md").write_text(
        f"# Cardiac/HRV bundle - {SUBJECT}\n\n"
        f"Derived from `{XDF.name}` by `scripts/cardiac_derive.py` (CGX ECG + Resp).\n\n"
        f"R-peaks: {len(beat_lsl):,} beats, {artifact.mean()*100:.2f}% artifact, "
        f"median HR {meta['median_hr_bpm']:.1f} bpm.\n\n"
        f"## cardiac_timeseries.parquet ({len(rr_table)} rows, {WIN_S:.0f}s window / {STEP_S:.0f}s step, trailing)\n"
        f"t_lsl, t_rel_s, HR (bpm), SDNN (ms), LF (ms^2), HF (ms^2), LF_HF, RespRate (br/min), n_beats, task.\n\n"
        f"## cardiac_beats.parquet ({len(beats_df):,} rows)\n"
        f"t_lsl, t_rel_s, rr_ms, inst_hr, artifact. For event-locked instantaneous-HR responses.\n\n"
        f"## cardiac_markers.parquet ({len(markers_df)} rows)\nSame schema as the Sentiometer bundle's markers.\n\n"
        f"Window: {WIN_S:.0f}s trailing, stepped {STEP_S:.0f}s. LF {LF_BAND}, HF {HF_BAND} Hz. "
        f"First full window ~{WIN_S:.0f}s in; the opening questionnaire stabilises HRV before Task 01.\n"
    )

    print("OK windows=%d valid=%d beats=%d artifact=%.2f%% HR=%.1f" % (
        len(rr_table), len(valid), len(beat_lsl), artifact.mean()*100, meta["median_hr_bpm"]))
    print("   metrics:", meta["metrics"])
    print("   wrote:", ts_path.name, bt_path.name, mk_path.name)


if __name__ == "__main__":
    main()
