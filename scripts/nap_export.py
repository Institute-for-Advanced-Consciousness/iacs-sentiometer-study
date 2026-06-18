"""Extract the clean nap epoch from a P013 session XDF and write it as EDF+
for sleep scoring (Ken Paller's lab).

For one subject:
  1. Load EEG (BrainAmpSeries 64ch) + CGX AIM-2 (EOG/EMG/ECG/Resp/PPG/GSR)
     + the task marker stream (for ``task05_end``, the nap anchor).
  2. Auto-detect the nap bookends from a movement score:
       - settle  = participant stops moving after getting into bed
                   (end of the settle-in motion that follows task05_end);
       - removal = movement resumes dramatically at the end (taking the gear
                   off) — or the recording end if it stops while still quiet.
     The score = max(EEG high-frequency power, CGX chin-EMG), each normalised
     to its own quiet baseline; the clean nap is the span between the FIRST and
     LAST sustained-quiet (>=60 s) runs, so brief movements DURING the nap are
     kept (Ken handles those).
  3. Write one continuous EDF+ of that span: 64 EEG (raw, FCz online ref) on
     their own clock; CGX channels resampled onto the EEG clock so EOG/EMG/ECG
     are sample-aligned to the EEG. No filtering, referencing, or other EEG
     preprocessing.
  4. Save a movement diagnostic PNG + a JSON sidecar documenting the cut.

Run (deps injected per-run):
  uv run --with pyxdf --with pyedflib --with numpy --with matplotlib \
      python scripts/nap_export.py sample-data/P013_S01.xdf
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
OUTROOT = REPO / "outputs" / "_paller_nap"

EEG_NAME = "BrainAmpSeries-Dev_1"
CGX_NAME = "CGX AIM Phys. Mon. AIM-0106"
MARK_NAME = "P013_Task_Markers"

# CGX ExGa -> AASM derivation names (P013 montage; matches xdf_to_edf/02_convert).
CGX_EXG_RENAME = {
    "ExGa 1": "E1-M2", "ExGa 2": "E2-M1",
    "ExGa 3": "ChinZ-Chin1", "ExGa 4": "Chin2-Chin1prime",
}
# CGX channels to KEEP, by stripped original label, with EDF unit.
# Dropped: SpO2/HR/Temp (flat sentinels, not populated), Packet Counter/TRIGGER
# (admin), and GSR (its ~4.25e8 device offset overflows the EDF numeric field and
# would flat-clip to the rail — it cannot be written raw; it stays in the XDF).
CGX_KEEP = ["ExGa 1", "ExGa 2", "ExGa 3", "ExGa 4", "ECG", "Resp.", "PPG"]
CGX_UNIT = {"E1-M2": "uV", "E2-M1": "uV", "ChinZ-Chin1": "uV",
            "Chin2-Chin1prime": "uV", "ECG": "uV", "Resp.": "uV",
            "PPG": "uV", "GSR": "uV"}

FS = 500
WIN = FS                     # 1 s movement windows
SMOOTH_WIN = 11              # moving-median smoothing (s)
MOTION_THRESH = 3.0          # fold over quiet baseline
MIN_QUIET_S = 60             # a "settled" run must be >= 60 s


# ---- XDF helpers ----------------------------------------------------------
def _labels(stream):
    chs = stream["info"]["desc"][0]["channels"][0]["channel"]
    return [c["label"][0] for c in chs]


def _movmed(x, k):
    k |= 1
    pad = k // 2
    xp = np.pad(x, pad, mode="edge")
    return np.array([np.median(xp[i:i + k]) for i in range(len(x))])


def _window_view(ts, data, t0, t1):
    """Rows of `data` whose timestamp is in [t0,t1), reshaped into whole 1 s
    windows. Returns (win_start_times, X[n_win, WIN, n_ch])."""
    m = (ts >= t0) & (ts < t1)
    t = ts[m]
    d = data[m]
    n = len(t) // WIN
    if n == 0:
        return np.array([]), np.empty((0, WIN, d.shape[1] if d.ndim == 2 else 1))
    d = d[:n * WIN]
    tw = t[:n * WIN].reshape(n, WIN)[:, 0]
    if d.ndim == 1:
        d = d[:, None]
    return tw, d.reshape(n, WIN, d.shape[1])


def detect_nap_window(eeg_ts, eeg_eeg, cgx_ts, cgx_chin, t05, region_end):
    """Return (settle_lsl, removal_lsl, diag).

    eeg_eeg  : EEG data restricted to the 64 scalp channels (n, 64).
    cgx_chin : the CGX chin-EMG channel (ExGa 3) samples (n,).
    """
    tw, EX = _window_view(eeg_ts, eeg_eeg, t05, region_end)
    if tw.size == 0:
        raise SystemExit("nap region too short to window")
    # EEG high-frequency power: mean over channels of windowed mean|sample diff|.
    eeg_hf = np.abs(np.diff(EX, axis=1)).mean(axis=(1, 2))
    # CGX chin-EMG: windowed std (aligned to the same window starts).
    twc, CX = _window_view(cgx_ts, cgx_chin, t05, region_end)
    emg = CX[:, :, 0].std(axis=1) if twc.size else np.zeros_like(eeg_hf)
    # align emg length to eeg windows (they share 1 s grid from t05)
    n = min(len(eeg_hf), len(emg))
    tw, eeg_hf, emg = tw[:n], eeg_hf[:n], emg[:n]

    def norm(x):
        # Normalise to the QUIET FLOOR (20th percentile), not the middle median:
        # the true resting level, so even a mild settle/removal stands out and
        # the threshold means the same multiple-of-quiet for every subject.
        base = float(np.percentile(x, 20))
        base = base if base > 1e-9 else (float(np.median(x)) or 1.0)
        return x / base, base

    hf_n, hf_base = norm(eeg_hf)
    emg_n, emg_base = norm(emg)
    score = np.maximum(hf_n, emg_n)
    score_s = _movmed(score, SMOOTH_WIN)

    quiet = score_s < MOTION_THRESH
    runs = []
    i = 0
    while i < len(quiet):
        if quiet[i]:
            j = i
            while j < len(quiet) and quiet[j]:
                j += 1
            if (j - i) >= MIN_QUIET_S:
                runs.append((i, j))
            i = j
        else:
            i += 1
    if runs:
        settle_lsl = float(tw[runs[0][0]])
        removal_win = runs[-1][1] - 1
        removal_lsl = float(tw[removal_win] + 1.0)
    else:
        settle_lsl, removal_lsl = float(t05), float(region_end)

    diag = dict(rel_min=((tw - t05) / 60.0), score=score_s,
                hf_norm=hf_n, emg_norm=emg_n, thresh=MOTION_THRESH,
                hf_base=hf_base, emg_base=emg_base,
                settle_rel_min=(settle_lsl - t05) / 60.0,
                removal_rel_min=(removal_lsl - t05) / 60.0,
                t05=float(t05), region_end=float(region_end))
    return settle_lsl, removal_lsl, diag


def _phys_range(sig):
    lo, hi = float(np.nanmin(sig)), float(np.nanmax(sig))
    if not (np.isfinite(lo) and np.isfinite(hi)):
        lo, hi = -1.0, 1.0
    if lo == hi:
        lo -= 1.0; hi += 1.0
    pad = 0.1 * (hi - lo)
    pmin, pmax = lo - pad, hi + pad
    if pmin < -9_999_999 or pmax > 9_999_999:   # EDF 8-char field overflow
        pmin, pmax = -8_388_608.0, 8_388_608.0
    if pmin >= pmax:
        pmin = pmax - 1.0
    return pmin, pmax


def _save_diag_png(diag, subject, settle, removal, dur_min, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    rel = diag["rel_min"]
    fig, ax = plt.subplots(figsize=(11, 3.3))
    ax.plot(rel, diag["score"], lw=0.6, color="#5d5179", label="movement score")
    ax.axhline(diag["thresh"], color="#a8473b", lw=0.8, ls="--",
               label=f"threshold ×{diag['thresh']:.0f}")
    ax.axvline(diag["settle_rel_min"], color="#7a9572", lw=1.4,
               label=f"settle (+{diag['settle_rel_min']:.1f} min)")
    ax.axvline(diag["removal_rel_min"], color="#141437", lw=1.4,
               label=f"end (+{diag['removal_rel_min']:.1f} min)")
    ax.axvspan(diag["settle_rel_min"], diag["removal_rel_min"], color="#7a9572", alpha=0.07)
    ax.set_yscale("log")
    ax.set_xlabel("minutes from task05_end")
    ax.set_ylabel("score (fold > quiet)")
    ax.set_title(f"{subject} — nap movement segmentation  ·  kept {dur_min:.1f} min",
                 loc="left", fontsize=10, color="#141437", fontweight="bold")
    ax.legend(fontsize=7, ncol=4, loc="upper center")
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    fig.savefig(path, dpi=130, bbox_inches="tight")
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("xdf")
    ap.add_argument("--subject", default=None)
    args = ap.parse_args()
    xdf = Path(args.xdf)
    if not xdf.exists():
        sys.exit(f"XDF not found: {xdf}")
    subject = args.subject or xdf.stem
    OUTROOT.mkdir(parents=True, exist_ok=True)
    (OUTROOT / "diagnostics").mkdir(exist_ok=True)
    print(f"=== nap export · {subject} ===")

    import pyxdf
    streams, _ = pyxdf.load_xdf(
        str(xdf),
        select_streams=[{"name": EEG_NAME}, {"name": CGX_NAME}, {"name": MARK_NAME}],
        dejitter_timestamps=True, synchronize_clocks=True)
    by = {s["info"]["name"][0]: s for s in streams}
    eeg, cgx, mk = by[EEG_NAME], by[CGX_NAME], by[MARK_NAME]

    eeg_ts = np.asarray(eeg["time_stamps"], float)
    eeg_data = np.asarray(eeg["time_series"], float)
    eeg_labels = _labels(eeg)
    cgx_ts = np.asarray(cgx["time_stamps"], float)
    cgx_data = np.asarray(cgx["time_series"], float)
    cgx_labels = [l.strip() for l in _labels(cgx)]
    mlab = [r[0] for r in mk["time_series"]]
    mts = np.asarray(mk["time_stamps"], float)
    if "task05_end" not in mlab:
        sys.exit("task05_end marker missing — cannot anchor the nap.")
    t05 = float(mts[mlab.index("task05_end")])

    eeg_scalp_idx = [i for i, l in enumerate(eeg_labels)
                     if l.lower() not in ("triggerstream", "trigger")]
    region_end = float(min(eeg_ts[-1], cgx_ts[-1]))
    print(f"  task05_end={t05:.1f}  region=[t05, {(region_end-t05)/60:.1f} min]")

    chin_idx = cgx_labels.index("ExGa 3")
    settle, removal, diag = detect_nap_window(
        eeg_ts, eeg_data[:, eeg_scalp_idx], cgx_ts, cgx_data[:, [chin_idx]], t05, region_end)
    dur_min = (removal - settle) / 60.0
    print(f"  settle=+{diag['settle_rel_min']:.1f}min  removal=+{diag['removal_rel_min']:.1f}min"
          f"  -> nap {dur_min:.1f} min")

    # ---- build EDF channels over [settle, removal] -----------------------
    master_mask = (eeg_ts >= settle) & (eeg_ts <= removal)
    master_ts = eeg_ts[master_mask]
    n_samp = len(master_ts)
    if n_samp < FS * 60:
        sys.exit(f"nap window too short ({n_samp} samples)")

    channels = []
    for j, lab in enumerate(eeg_labels):
        if j not in eeg_scalp_idx:
            continue
        sig = eeg_data[master_mask, j]              # raw EEG, untouched
        pmin, pmax = _phys_range(sig)
        channels.append(dict(label=lab[:16], unit="uV", fs=FS, signal=sig,
                             pmin=pmin, pmax=pmax,
                             prefilter="BrainAmp Std AC ~0.016Hz HP; ref FCz",
                             transducer="actiCAP Ag/AgCl"))
    # CGX channels resampled onto the EEG (master) clock for sample alignment
    for orig in CGX_KEEP:
        if orig not in cgx_labels:
            continue
        ci = cgx_labels.index(orig)
        edf_label = CGX_EXG_RENAME.get(orig, orig)
        sig = np.interp(master_ts, cgx_ts, cgx_data[:, ci]).astype(np.float64)
        pmin, pmax = _phys_range(sig)
        channels.append(dict(label=edf_label[:16], unit=CGX_UNIT.get(edf_label, "uV"),
                             fs=FS, signal=sig, pmin=pmin, pmax=pmax,
                             prefilter="CGX AIM-2 internal",
                             transducer="CGX AIM-2"))
    del eeg_data, cgx_data

    # wall-clock anchor (best effort) from file mtime
    file_utc = datetime.fromtimestamp(xdf.stat().st_mtime, tz=timezone.utc)
    nap_start_utc = file_utc - timedelta(seconds=(region_end - settle))

    import pyedflib
    edf_path = OUTROOT / f"{subject}_nap.edf"
    print(f"  writing {len(channels)} channels -> {edf_path.name}")
    w = pyedflib.EdfWriter(str(edf_path), len(channels), file_type=pyedflib.FILETYPE_EDFPLUS)
    try:
        w.setHeader(dict(technician="IACS", recording_additional="P013_nap",
                         patientname="X", patient_additional="",
                         patientcode=subject,
                         equipment="BrainAmp+CGX_AIM2", admincode="",
                         sex="", birthdate="",
                         startdate=nap_start_utc.replace(tzinfo=None)))
        for i, ch in enumerate(channels):
            ch["sig_clip"] = np.clip(ch["signal"], ch["pmin"], ch["pmax"]).astype(np.float64)
            w.setSignalHeader(i, dict(label=ch["label"], dimension=ch["unit"],
                                      sample_frequency=ch["fs"],
                                      physical_min=ch["pmin"], physical_max=ch["pmax"],
                                      digital_min=-32768, digital_max=32767,
                                      prefilter=ch["prefilter"][:80],
                                      transducer=ch["transducer"][:80]))
        w.writeSamples([ch["sig_clip"] for ch in channels])
        w.writeAnnotation(0.0, -1.0, "nap_start")
        for t, v in zip(mts, mlab):
            onset = t - settle
            if 0 <= onset <= (removal - settle):
                w.writeAnnotation(float(onset), -1.0, str(v)[:60])
    finally:
        w.close()

    # spot-check round-trip on a few channels
    rng = np.random.default_rng(0)
    r = pyedflib.EdfReader(str(edf_path))
    try:
        worst = 0.0
        for i in [0, len(channels)//2, len(channels)-1]:
            src = channels[i]["sig_clip"]; wr = r.readSignal(i)
            idx = rng.choice(min(len(src), len(wr)), 10, replace=False)
            worst = max(worst, float(np.max(np.abs(src[idx] - wr[idx]))))
        file_dur = r.file_duration
    finally:
        r.close()

    _save_diag_png(diag, subject, settle, removal, dur_min,
                   OUTROOT / "diagnostics" / f"{subject}_nap_segmentation.png")

    sidecar = dict(
        subject=subject, source_xdf=str(xdf), n_channels=len(channels),
        eeg_channels=len(eeg_scalp_idx),
        cgx_channels=[c["label"] for c in channels if c["label"] not in eeg_labels],
        fs_hz=FS, task05_end_lsl=t05,
        settle_lsl=settle, removal_lsl=removal,
        settle_rel_min=round(diag["settle_rel_min"], 2),
        removal_rel_min=round(diag["removal_rel_min"], 2),
        nap_minutes=round(dur_min, 2), n_samples=n_samp,
        edf_file_duration_min=round(file_dur / 60.0, 2),
        nap_start_utc=nap_start_utc.isoformat(),
        motion_method="max(EEG HF |diff|, CGX chin-EMG std), each /quiet-baseline; "
                      f"first..last sustained-quiet(>={MIN_QUIET_S}s) run; thresh×{MOTION_THRESH}",
        spotcheck_max_err=round(worst, 6))
    (OUTROOT / "diagnostics" / f"{subject}_nap.json").write_text(json.dumps(sidecar, indent=2))
    print(f"  EDF dur={file_dur/60:.1f} min  spotcheck_max_err={worst:.4g}  "
          f"size={edf_path.stat().st_size/1e6:.0f}MB")
    print(f"  wrote {edf_path.name} + diagnostics")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
