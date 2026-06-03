"""Shared library for the P013 Sentiometer phase-resolved report.

Loads the derived Sentiometer bundle (see ``sentiometer_derive.py``),
defines the experiment's phase/event structure, and provides IACS-styled
matplotlib helpers plus the epoching / aggregation primitives used by
every figure.

All 7 Sentiometer series are treated uniformly:
    PD1..PD5  (raw photodiodes)  +  sent_mean  +  pc1

Overlay plots use a GLOBAL z-score per series (mean/SD over the whole
recording) so between-block amplitude shifts stay visible on one axis.
Event-locked averages (ERPs) use RAW units, baseline-corrected per epoch.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
# Subject/session is a single knob shared by the whole report pipeline.
# Set SENT_SUBJECT to run for any session (matches the XDF filename stem,
# e.g. "P013_S01", "P013_S02"). Defaults to P013_S01.
import os
SUBJECT = os.environ.get("SENT_SUBJECT", "P013_S01")
BUNDLE = REPO / "outputs" / SUBJECT / "preprocessed"
FIGDIR = REPO / "outputs" / SUBJECT / "report" / "figs"

# ---- IACS palette --------------------------------------------------------
INK = "#141437"
PURPLE = "#5d5179"
PAPER = "#faf8f4"
RULE = "#d9d9e0"
FG = "#2f2f37"
MUTED = "#5e5e6c"
C = ["#5d5179", "#141437", "#6b8fb5", "#7a9572", "#c9a45a", "#a8473b"]

# The 7 series: column, display label, colour, linewidth, alpha, emphasis.
SERIES = [
    ("PD1", "PD1", "#6b8fb5", 0.6, 0.60, False),
    ("PD2", "PD2", "#7a9572", 0.6, 0.60, False),
    ("PD3", "PD3", "#c9a45a", 0.6, 0.60, False),
    ("PD4", "PD4", "#a8473b", 0.6, 0.60, False),
    ("PD5", "PD5", "#b29bd6", 0.6, 0.60, False),
    ("sent_mean", "Mean", PURPLE, 1.4, 0.95, True),
    ("pc1", "PC1", INK, 1.4, 0.95, True),
]
SERIES_COLS = [s[0] for s in SERIES]


# ---- matplotlib house style ---------------------------------------------
def _pick_font() -> str:
    """Prefer Open Sans if installed; else a clean sans fallback."""
    installed = {f.name for f in fm.fontManager.ttflist}
    for cand in ("Open Sans", "Segoe UI", "DejaVu Sans"):
        if cand in installed:
            return cand
    return "DejaVu Sans"


def apply_style() -> None:
    f = _pick_font()
    plt.rcParams.update({
        "font.family": f,
        "font.size": 8.0,
        "axes.edgecolor": RULE,
        "axes.linewidth": 0.5,
        "axes.labelcolor": FG,
        "axes.titlecolor": INK,
        "axes.titlesize": 9.5,
        "axes.titleweight": "bold",
        "axes.labelsize": 8.0,
        "axes.facecolor": "white",
        "figure.facecolor": "white",
        "xtick.color": MUTED,
        "ytick.color": MUTED,
        "xtick.labelsize": 7.0,
        "ytick.labelsize": 7.0,
        "legend.fontsize": 6.8,
        "legend.frameon": False,
        "grid.color": "#ededf1",
        "grid.linewidth": 0.4,
        "savefig.dpi": 200,
        "savefig.bbox": "tight",
        "savefig.facecolor": "white",
    })


def finish_axes(ax) -> None:
    """Hairline, top/right spines off — the IACS table aesthetic in a chart."""
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(RULE)
        ax.spines[side].set_linewidth(0.5)
    ax.tick_params(length=2.5, width=0.5)


# ---- data loading --------------------------------------------------------
@dataclass
class Phase:
    key: str
    title: str
    t0: float          # t_rel_s
    t1: float
    color: str = PURPLE


@dataclass
class Bundle:
    ts: pd.DataFrame                    # full continuous frame (+ z-scored cols)
    mk: pd.DataFrame                    # markers
    meta: dict
    fs: float
    phases: list[Phase] = field(default_factory=list)

    def marker_times(self, *events, task=None) -> np.ndarray:
        """t_rel_s for markers whose `event` is in *events (optionally
        filtered to a task prefix). Order preserved by time."""
        m = self.mk
        sel = m["event"].isin(events)
        if task is not None:
            sel &= (m["task"] == task)
        return m.loc[sel, "t_rel_s"].to_numpy()

    def marker_times_lsl(self, *events, task=None) -> np.ndarray:
        m = self.mk
        sel = m["event"].isin(events)
        if task is not None:
            sel &= (m["task"] == task)
        return m.loc[sel, "t_lsl"].to_numpy()


def load() -> Bundle:
    ts = pd.read_parquet(BUNDLE / "sentiometer_timeseries.parquet")
    mk = pd.read_parquet(BUNDLE / "sentiometer_markers.parquet")
    meta = json.loads((BUNDLE / "sentiometer_pca.json").read_text())
    fs = float(meta["fs_estimated_hz"])

    # global z-score per series for overlay plots (suffix _z)
    for col in SERIES_COLS:
        x = ts[col].to_numpy(dtype=np.float64)
        mu, sd = x.mean(), x.std()
        ts[col + "_z"] = ((x - mu) / (sd if sd else 1.0)).astype(np.float32)

    # phase structure from *_start/_end markers; nap = task05_end -> EOF
    def span(p):
        s = mk.loc[mk.label == f"{p}_start", "t_rel_s"]
        e = mk.loc[mk.label == f"{p}_end", "t_rel_s"]
        return (float(s.iloc[0]), float(e.iloc[0])) if len(s) and len(e) else None

    titles = {
        "task00": "Pre-Session Questionnaire",
        "task01": "Auditory Oddball (P300)",
        "task02": "RGB Illuminance",
        "task03": "Backward Masking (Faces)",
        "task04": "Mind-State Switching",
        "task05": "SSVEP Frequency Ramp",
    }
    phases = []
    for p in ["task00", "task01", "task02", "task03", "task04", "task05"]:
        sp = span(p)
        if sp:
            phases.append(Phase(p, titles[p], sp[0], sp[1]))
    t05e = float(mk.loc[mk.label == "task05_end", "t_rel_s"].iloc[0])
    tmax = float(ts["t_rel_s"].max())
    phases.append(Phase("nap", "Post-Session Nap", t05e, tmax, color=INK))

    return Bundle(ts=ts, mk=mk, meta=meta, fs=fs, phases=phases)


# ---- downsampling --------------------------------------------------------
def minmax_downsample(t: np.ndarray, y: np.ndarray, n_buckets: int):
    """Min/max envelope downsample — preserves visual extent (peaks/troughs)
    so a heavily-decimated trace still looks fully zoomed-in.

    Returns (t_ds, y_ds) with up to 2*n_buckets points.
    """
    n = len(y)
    if n <= 2 * n_buckets:
        return t, y
    edges = np.linspace(0, n, n_buckets + 1, dtype=int)
    tt, yy = [], []
    for i in range(n_buckets):
        a, b = edges[i], edges[i + 1]
        if b <= a:
            continue
        seg = y[a:b]
        imn = a + int(np.argmin(seg))
        imx = a + int(np.argmax(seg))
        for idx in sorted((imn, imx)):
            tt.append(t[idx]); yy.append(y[idx])
    return np.asarray(tt), np.asarray(yy)


# ---- epoching ------------------------------------------------------------
def epoch(bundle: Bundle, col: str, onsets_lsl: np.ndarray,
          pre_ms: float, post_ms: float, baseline_ms=None):
    """Cut epochs of `col` around onset LSL timestamps.

    Returns (t_ms, M) where t_ms is the epoch time axis (ms, 0=onset) and
    M is (n_epochs, n_samples). If baseline_ms=(a,b) is given, each epoch
    is baseline-corrected by subtracting its own mean over [a,b] ms.
    Epochs that fall off the recording edge are dropped.
    """
    t_lsl = bundle.ts["t_lsl"].to_numpy()
    y = bundle.ts[col].to_numpy(dtype=np.float64)
    fs = bundle.fs
    pre_n = int(round(pre_ms / 1000.0 * fs))
    post_n = int(round(post_ms / 1000.0 * fs))
    t_ms = (np.arange(-pre_n, post_n + 1) / fs) * 1000.0
    rows = []
    for o in onsets_lsl:
        i = int(np.searchsorted(t_lsl, o))
        a, b = i - pre_n, i + post_n + 1
        if a < 0 or b > len(y):
            continue
        seg = y[a:b].copy()
        if baseline_ms is not None:
            m = (t_ms >= baseline_ms[0]) & (t_ms <= baseline_ms[1])
            seg = seg - seg[m].mean()
        rows.append(seg)
    M = np.vstack(rows) if rows else np.empty((0, len(t_ms)))
    return t_ms, M


def window_means(bundle: Bundle, col: str, starts_lsl: np.ndarray,
                 ends_lsl: np.ndarray) -> np.ndarray:
    """Mean of `col` over each [start,end] LSL window. Returns one value
    per window (NaN if a window contains no samples)."""
    t_lsl = bundle.ts["t_lsl"].to_numpy()
    y = bundle.ts[col].to_numpy(dtype=np.float64)
    out = []
    for s, e in zip(starts_lsl, ends_lsl):
        # half-open interval [s, e): include samples with s <= t < e so that
        # consecutive non-overlapping windows never share a boundary sample.
        a = int(np.searchsorted(t_lsl, s, "left"))
        b = int(np.searchsorted(t_lsl, e, "left"))
        out.append(y[a:b].mean() if b > a else np.nan)
    return np.asarray(out)


# ---- SSVEP analytic frequency -------------------------------------------
def ssvep_ramp_spec(bundle: Bundle) -> dict | None:
    """Pull the ramp_start JSON from the markers (effective-Hz spec)."""
    j = bundle.mk.loc[bundle.mk["is_json"], "label"]
    for s in j:
        try:
            d = json.loads(s)
        except json.JSONDecodeError:
            continue
        if d.get("event") == "ramp_start":
            return d
    return None


def eff_hz_at(t_rel: np.ndarray, ramp_start_rel: float, spec: dict) -> np.ndarray:
    """Effective SSVEP Hz at each t_rel (clipped to 0 outside the ramp)."""
    dur = float(spec["durationSeconds"])
    f0, f1 = float(spec["stimFreqHz"]), float(spec["stimFreqEndHz"])
    prog = (t_rel - ramp_start_rel) / dur
    hz = f0 + prog * (f1 - f0)
    hz = np.where((prog >= 0) & (prog <= 1), hz, 0.0)
    return hz


def savefig(fig, name: str) -> Path:
    FIGDIR.mkdir(parents=True, exist_ok=True)
    p = FIGDIR / name
    fig.savefig(p)
    plt.close(fig)
    return p
