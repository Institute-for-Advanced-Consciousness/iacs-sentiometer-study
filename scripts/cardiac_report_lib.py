"""Shared library for the P013 cardiac/HRV phase-resolved report.

Mirrors ``sentiometer_report_lib.py`` in style and structure, adapted for
the six windowed cardiac metrics (HR, SDNN, LF, HF, LF/HF, RespRate) plus
the beat-level instantaneous-HR table used for event-locked responses.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
SUBJECT = os.environ.get("SENT_SUBJECT", "P013_S01")
BUNDLE = REPO / "outputs" / SUBJECT / "cardiac"
FIGDIR = REPO / "outputs" / SUBJECT / "cardiac" / "report" / "figs"

# ---- IACS palette (identical to the Sentiometer report) ------------------
INK = "#141437"
PURPLE = "#5d5179"
PAPER = "#faf8f4"
RULE = "#d9d9e0"
FG = "#2f2f37"
MUTED = "#5e5e6c"
C = ["#5d5179", "#141437", "#6b8fb5", "#7a9572", "#c9a45a", "#a8473b"]

# The 6 windowed cardiac metrics, in display order.
METRIC_COLS = ["HR", "SDNN", "LF", "HF", "LF_HF", "RespRate"]
METRIC_META = {
    "HR": ("Heart Rate", "bpm", "#a8473b"),
    "SDNN": ("SDNN", "ms", "#5d5179"),
    "LF": ("LF power", "ms²", "#6b8fb5"),
    "HF": ("HF power", "ms²", "#7a9572"),
    "LF_HF": ("LF/HF", "ratio", "#c9a45a"),
    "RespRate": ("Respiration", "br/min", "#141437"),
}


def _pick_font():
    installed = {f.name for f in fm.fontManager.ttflist}
    for cand in ("Open Sans", "Segoe UI", "DejaVu Sans"):
        if cand in installed:
            return cand
    return "DejaVu Sans"


def apply_style():
    f = _pick_font()
    plt.rcParams.update({
        "font.family": f, "font.size": 8.0,
        "axes.edgecolor": RULE, "axes.linewidth": 0.5,
        "axes.labelcolor": FG, "axes.titlecolor": INK,
        "axes.titlesize": 9.5, "axes.titleweight": "bold",
        "axes.labelsize": 8.0, "axes.facecolor": "white",
        "figure.facecolor": "white",
        "xtick.color": MUTED, "ytick.color": MUTED,
        "xtick.labelsize": 7.0, "ytick.labelsize": 7.0,
        "legend.fontsize": 6.8, "legend.frameon": False,
        "grid.color": "#ededf1", "grid.linewidth": 0.4,
        "savefig.dpi": 200, "savefig.bbox": "tight", "savefig.facecolor": "white",
    })


def finish_axes(ax):
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(RULE); ax.spines[side].set_linewidth(0.5)
    ax.tick_params(length=2.5, width=0.5)


@dataclass
class Phase:
    key: str
    title: str
    t0: float
    t1: float
    color: str = PURPLE


@dataclass
class Bundle:
    ts: pd.DataFrame
    beats: pd.DataFrame
    mk: pd.DataFrame
    meta: dict
    phases: list = field(default_factory=list)

    def marker_times(self, *events, task=None):
        m = self.mk
        sel = m["event"].isin(events)
        if task is not None:
            sel &= (m["task"] == task)
        return m.loc[sel, "t_rel_s"].to_numpy()

    def marker_times_lsl(self, *events, task=None):
        m = self.mk
        sel = m["event"].isin(events)
        if task is not None:
            sel &= (m["task"] == task)
        return m.loc[sel, "t_lsl"].to_numpy()


def load():
    ts = pd.read_parquet(BUNDLE / "cardiac_timeseries.parquet")
    beats = pd.read_parquet(BUNDLE / "cardiac_beats.parquet")
    mk = pd.read_parquet(BUNDLE / "cardiac_markers.parquet")
    meta = json.loads((BUNDLE / "cardiac_meta.json").read_text())

    # global z-score per metric for the overlay panel (valid rows only)
    for col in METRIC_COLS:
        x = ts[col].to_numpy(dtype=np.float64)
        mu, sd = np.nanmean(x), np.nanstd(x)
        ts[col + "_z"] = ((x - mu) / (sd if sd else 1.0)).astype(np.float32)

    def span(p):
        s = mk.loc[mk.label == f"{p}_start", "t_rel_s"]
        e = mk.loc[mk.label == f"{p}_end", "t_rel_s"]
        return (float(s.iloc[0]), float(e.iloc[0])) if len(s) and len(e) else None

    titles = {
        "task00": "Pre-Session Questionnaire", "task01": "Auditory Oddball (P300)",
        "task02": "RGB Illuminance", "task03": "Backward Masking (Faces)",
        "task04": "Mind-State Switching", "task05": "SSVEP Frequency Ramp",
    }
    phases = []
    for p in ["task00", "task01", "task02", "task03", "task04", "task05"]:
        sp = span(p)
        if sp:
            phases.append(Phase(p, titles[p], sp[0], sp[1]))
    t05e = float(mk.loc[mk.label == "task05_end", "t_rel_s"].iloc[0])
    tmax = float(ts["t_rel_s"].max())
    phases.append(Phase("nap", "Post-Session Nap", t05e, tmax, color=INK))
    return Bundle(ts=ts, beats=beats, mk=mk, meta=meta, phases=phases)


def epoch_inst_hr(bundle, onsets_lsl, pre_s, post_s, baseline_s=None):
    """Event-locked instantaneous HR. Resamples the irregular beat series to
    a uniform 4 Hz grid around each onset, returns (t_s, M[n_epochs, n_t]).
    If baseline_s=(a,b), subtract each epoch's mean over [a,b] s.
    """
    bt = bundle.beats
    t = bt["t_lsl"].to_numpy()
    hr = bt["inst_hr"].to_numpy(dtype=np.float64)
    art = bt["artifact"].to_numpy()
    t, hr = t[~art], hr[~art]
    grid = np.arange(-pre_s, post_s + 1e-9, 0.25)
    rows = []
    for o in onsets_lsl:
        # require beats on BOTH sides of the onset, else np.interp would
        # silently flat-extrapolate an edge onset into a fake baseline.
        m_pre = (t >= o - pre_s - 2) & (t < o)
        m_post = (t >= o) & (t <= o + post_s + 2)
        if m_pre.sum() < 2 or m_post.sum() < 2:
            continue
        m = m_pre | m_post
        seg = np.interp(o + grid, t[m], hr[m])
        if baseline_s is not None:
            bm = (grid >= baseline_s[0]) & (grid < baseline_s[1])  # half-open
            seg = seg - seg[bm].mean()
        rows.append(seg)
    M = np.vstack(rows) if rows else np.empty((0, len(grid)))
    return grid, M


def window_means(bundle, col, starts_lsl, ends_lsl):
    """Mean of a windowed metric over each [start,end] LSL window.

    NOTE: operates on the pre-windowed (120 s trailing) metric series, so
    values near a window start still carry pre-window history. Prefer
    ``block_metrics`` for clean, contamination-free condition contrasts;
    this is kept only for ad-hoc queries.
    """
    t = bundle.ts["t_lsl"].to_numpy()
    y = bundle.ts[col].to_numpy(dtype=np.float64)
    out = []
    for s, e in zip(starts_lsl, ends_lsl):
        a = int(np.searchsorted(t, s, "left"))
        b = int(np.searchsorted(t, e, "left"))
        seg = y[a:b]
        seg = seg[~np.isnan(seg)]
        out.append(seg.mean() if len(seg) else np.nan)
    return np.asarray(out)


def _metrics_from_rr(rr, rt):
    """Compute (HR, SDNN, LF, HF, LF_HF, RespRate=None) from one RR segment.
    RespRate is filled separately (it comes from the Resp channel)."""
    from scipy import signal as sps
    if len(rr) < 12:
        return {k: np.nan for k in METRIC_COLS}
    hr = 60000.0 / np.mean(rr)
    sdnn = float(np.std(rr, ddof=1))
    lf = hf = np.nan
    if len(rr) >= 20:
        ti = np.arange(rt[0], rt[-1], 0.25)
        if len(ti) >= 16:
            ri = np.interp(ti, rt, rr) - np.mean(np.interp(ti, rt, rr))
            f, P = sps.welch(ri, fs=4.0, nperseg=min(len(ri), 240))
            trapz = np.trapezoid
            lf = float(trapz(P[(f >= 0.04) & (f < 0.15)], f[(f >= 0.04) & (f < 0.15)]))
            hf = float(trapz(P[(f >= 0.15) & (f < 0.40)], f[(f >= 0.15) & (f < 0.40)]))
    lfhf = lf / hf if (hf and hf > 0) else np.nan
    return {"HR": hr, "SDNN": sdnn, "LF": lf, "HF": hf, "LF_HF": lfhf, "RespRate": np.nan}


def block_metrics(bundle, start_lsl, end_lsl, sub_s=30.0):
    """Per-metric arrays for a block, computed DIRECTLY from beats inside
    [start,end], chunked into NON-OVERLAPPING ``sub_s`` sub-windows.

    This avoids the two flaws of querying the pre-windowed series: (1) no
    pre-block contamination (only beats truly inside the block are used),
    and (2) sub-windows are independent, so SEM across them is valid.

    RespRate per sub-window is read from the bundle's windowed series
    (respiration is continuous, not beat-based) as a mean over the sub-window.
    """
    bt = bundle.beats
    bt_t = bt["t_lsl"].to_numpy()
    rr = bt["rr_ms"].to_numpy(dtype=np.float64)
    art = bt["artifact"].to_numpy()
    edges = np.arange(start_lsl, end_lsl, sub_s)
    out = {k: [] for k in METRIC_COLS}
    rt_full = bundle.ts["t_lsl"].to_numpy()
    resp_full = bundle.ts["RespRate"].to_numpy(dtype=np.float64)
    for a, b in zip(edges[:-1], edges[1:]):
        m = (bt_t >= a) & (bt_t < b) & (~art)
        seg_rr, seg_t = rr[m], bt_t[m]
        vals = _metrics_from_rr(seg_rr, seg_t)
        # respiration: mean of windowed RespRate samples whose anchor sits in [a,b)
        rm = (rt_full >= a) & (rt_full < b)
        rv = resp_full[rm]; rv = rv[~np.isnan(rv)]
        vals["RespRate"] = float(rv.mean()) if len(rv) else np.nan
        for k in METRIC_COLS:
            out[k].append(vals[k])
    return {k: np.asarray(v, float) for k, v in out.items()}


def block_metrics_lsl(bundle, start_lsl, end_lsl, sub_s=30.0):
    """Same as block_metrics but takes explicit LSL bounds (already the
    signature). Alias kept for readability at call sites."""
    return block_metrics(bundle, start_lsl, end_lsl, sub_s=sub_s)


# ---- SSVEP ramp helpers (mirror sentiometer_report_lib) ------------------
def ssvep_ramp_spec(bundle):
    """Pull the ramp_start JSON (effective-Hz spec) from the markers."""
    j = bundle.mk.loc[bundle.mk["is_json"], "label"]
    for s in j:
        try:
            d = json.loads(s)
        except json.JSONDecodeError:
            continue
        if d.get("event") == "ramp_start":
            return d
    return None


def eff_hz_at(t_rel, ramp_start_rel, spec):
    """Effective SSVEP Hz at each t_rel (clipped to 0 outside the ramp)."""
    dur = float(spec["durationSeconds"])
    f0, f1 = float(spec["stimFreqHz"]), float(spec["stimFreqEndHz"])
    prog = (t_rel - ramp_start_rel) / dur
    hz = f0 + prog * (f1 - f0)
    return np.where((prog >= 0) & (prog <= 1), hz, 0.0)


def ramp_band_window_lsl(bundle, lo_hz, hi_hz):
    """LSL [start,end] of the contiguous ramp segment whose effective freq
    is in [lo,hi). Because the ramp is monotonic 40->1 Hz, each band maps to
    one contiguous time chunk — returned as (start_lsl, end_lsl) or None."""
    spec = ssvep_ramp_spec(bundle)
    rs = bundle.mk.loc[bundle.mk.event == "ramp_start", "t_rel_s"]
    if spec is None or not len(rs):
        return None
    rs_rel = float(rs.iloc[0])
    dur = float(spec["durationSeconds"])
    t = bundle.ts["t_rel_s"].to_numpy()
    tl = bundle.ts["t_lsl"].to_numpy()
    inramp = (t >= rs_rel) & (t <= rs_rel + dur)
    hz = eff_hz_at(t[inramp], rs_rel, spec)
    band = (hz >= lo_hz) & (hz < hi_hz)
    if not band.any():
        return None
    tl_in = tl[inramp][band]
    return float(tl_in.min()), float(tl_in.max())


def savefig(fig, name):
    FIGDIR.mkdir(parents=True, exist_ok=True)
    p = FIGDIR / name
    fig.savefig(p)
    plt.close(fig)
    return p
