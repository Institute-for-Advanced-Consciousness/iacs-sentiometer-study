"""Shared library for the P013 GROUP (multi-subject) reports.

Both group reports — Sentiometer and cardiac — pool an arbitrary number of
per-subject derived bundles into one IACS-styled PDF. The design rule is:

  * Reuse the *exact* per-subject analysis code. The single-subject libs
    (``sentiometer_report_lib`` / ``cardiac_report_lib``) load a bundle from a
    module-level ``BUNDLE`` path and expose pure functions that take a bundle
    argument (``epoch``, ``window_means``, ``block_metrics``, ``corr_matrix`` …).
    The group scripts loop over subjects, repoint ``BUNDLE`` at each subject's
    folder, call ``load()``, and feed the resulting bundle through the SAME
    functions. So "same preprocessing, same analysis" is guaranteed by
    construction, not by re-implementation.

  * Aggregate three ways, matching what Nicco asked for:
      - time-series / ERPs  -> overlay one line per subject + a bold MEAN line
                               (between-subject SEM band).
      - bar charts          -> grand mean of the per-subject means with a
                               POOLED variance (pooled within-subject SD) error
                               bar, plus per-subject dots so the spread is honest.
      - correlation matrices-> the MEAN correlation across subjects, averaged in
                               Fisher-z space and back-transformed.

Subjects are auto-discovered from ``outputs/`` so the only thing required to
add a participant is to derive their bundle (drop the XDF in ``sample-data/``
and run the per-subject pipeline); the group report then picks them up.
"""

from __future__ import annotations

import os
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import numpy as np

REPO = Path(__file__).resolve().parents[1]
OUTROOT = REPO / "outputs"
GROUPROOT = OUTROOT / "_group"

# ---- IACS palette (identical to the single-subject reports) --------------
INK = "#141437"
PURPLE = "#5d5179"
PAPER = "#faf8f4"
RULE = "#d9d9e0"
FG = "#2f2f37"
MUTED = "#5e5e6c"
C = ["#5d5179", "#141437", "#6b8fb5", "#7a9572", "#c9a45a", "#a8473b"]

# Per-subject colours for overlays (distinct, IACS-toned). The grand mean is
# always drawn in INK on top.
SUBJECT_COLORS = ["#6b8fb5", "#a8473b", "#7a9572", "#c9a45a",
                  "#9a86c4", "#3b5da8", "#4a7a52", "#b0743a"]
GRAND = INK


def subject_color(i: int) -> str:
    return SUBJECT_COLORS[i % len(SUBJECT_COLORS)]


# ---- subject discovery ----------------------------------------------------
def discover_subjects(kind: str) -> list[str]:
    """All subjects with a derived bundle of the given kind.

    kind = "sentiometer"  -> needs outputs/<S>/preprocessed/sentiometer_timeseries.parquet
    kind = "cardiac"      -> needs outputs/<S>/cardiac/cardiac_timeseries.parquet

    Override with env GROUP_SUBJECTS="P013_S01,P013_S02" to pin an explicit set
    / order. Folders beginning with "_" (e.g. the group output dir) are ignored.
    """
    override = os.environ.get("GROUP_SUBJECTS", "").strip()
    if override:
        subs = [s.strip() for s in override.split(",") if s.strip()]
    else:
        if kind == "sentiometer":
            globbed = OUTROOT.glob("*/preprocessed/sentiometer_timeseries.parquet")
            subs = sorted(p.parents[1].name for p in globbed)
        elif kind == "cardiac":
            globbed = OUTROOT.glob("*/cardiac/cardiac_timeseries.parquet")
            subs = sorted(p.parents[1].name for p in globbed)
        else:
            raise ValueError(f"unknown kind {kind!r}")
        subs = [s for s in subs if not s.startswith("_")]
    if not subs:
        raise SystemExit(f"No {kind} bundles found under {OUTROOT}.")
    return subs


# ---- matplotlib house style (shared with the single-subject reports) -----
def _pick_font() -> str:
    installed = {f.name for f in fm.fontManager.ttflist}
    for cand in ("Open Sans", "Segoe UI", "DejaVu Sans"):
        if cand in installed:
            return cand
    return "DejaVu Sans"


def apply_style() -> None:
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


def finish_axes(ax) -> None:
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(RULE)
        ax.spines[side].set_linewidth(0.5)
    ax.tick_params(length=2.5, width=0.5)


def savefig(fig, figdir: Path, name: str) -> Path:
    figdir.mkdir(parents=True, exist_ok=True)
    p = figdir / name
    fig.savefig(p)
    plt.close(fig)
    return p


# ---- aggregation primitives ----------------------------------------------
def pooled_stats(per_subject_arrays: list[np.ndarray]) -> dict:
    """Pool one condition's per-subject value arrays.

    Each element is a 1-D array of that subject's bin/window values for the
    condition (the SAME unit of observation the single-subject bar used).

    Returns:
      subj_means   – one mean per subject (subjects with no data dropped)
      grand_mean   – mean of the subject means (each subject weighted equally;
                     the subject is the unit of replication)
      pooled_sd    – sqrt of the pooled within-subject variance
                     Σ(n_i−1)s_i² / Σ(n_i−1)  (the classic pooled variance)
      pooled_sem   – pooled_sd / sqrt(Σ n_i)   (spread of the pooled sample mean)
      between_sd   – SD of the subject means (between-subject spread)
      sem_between  – between_sd / sqrt(k)       (SEM of the grand mean over subjects)
      n_subj, ns
    """
    means, vars_, ns = [], [], []
    for a in per_subject_arrays:
        a = np.asarray(a, float)
        a = a[~np.isnan(a)]
        if a.size == 0:
            continue
        means.append(a.mean())
        ns.append(a.size)
        vars_.append(a.var(ddof=1) if a.size > 1 else 0.0)
    means = np.asarray(means, float)
    ns = np.asarray(ns, float)
    vars_ = np.asarray(vars_, float)
    if means.size == 0:
        return dict(subj_means=means, grand_mean=np.nan, pooled_sd=np.nan,
                    pooled_sem=np.nan, between_sd=np.nan, sem_between=np.nan,
                    n_subj=0, ns=ns)
    grand = float(means.mean())
    dfsum = float((ns - 1).sum())
    pooled_var = float(((ns - 1) * vars_).sum() / dfsum) if dfsum > 0 else np.nan
    pooled_sd = float(np.sqrt(pooled_var)) if pooled_var == pooled_var else np.nan
    ntot = float(ns.sum())
    pooled_sem = float(pooled_sd / np.sqrt(ntot)) if (pooled_sd == pooled_sd and ntot > 0) else np.nan
    between_sd = float(means.std(ddof=1)) if means.size > 1 else 0.0
    sem_between = float(between_sd / np.sqrt(means.size)) if means.size else np.nan
    return dict(subj_means=means, grand_mean=grand, pooled_sd=pooled_sd,
                pooled_sem=pooled_sem, between_sd=between_sd, sem_between=sem_between,
                n_subj=int(means.size), ns=ns)


def fisher_mean_r(r_stack: np.ndarray):
    """Mean correlation across subjects via Fisher z.

    r_stack : (n_subj, nrow, ncol) stack of per-subject Pearson-r matrices
              (NaN where a subject couldn't estimate a cell).
    Returns (r_mean, n_used, all_same_sign, r_sd) all shaped (nrow, ncol):
      r_mean       – tanh(mean over subjects of arctanh(r))
      n_used       – count of non-NaN subjects per cell
      all_same_sign– True where every contributing subject's r has the same sign
                     (and ≥2 subjects contribute) — a simple consistency flag
      r_sd         – SD of r across subjects (raw r units; descriptive)
    """
    r = np.asarray(r_stack, float)
    rc = np.clip(r, -0.999999, 0.999999)
    z = np.arctanh(rc)
    with np.errstate(invalid="ignore"):
        zbar = np.nanmean(z, axis=0)
        r_mean = np.tanh(zbar)
        r_sd = np.nanstd(r, axis=0)
    n_used = np.sum(~np.isnan(r), axis=0)
    pos = np.sum(r > 0, axis=0)
    neg = np.sum(r < 0, axis=0)
    all_same_sign = ((pos == n_used) | (neg == n_used)) & (n_used >= 2)
    r_mean = np.where(n_used >= 1, r_mean, np.nan)
    return r_mean, n_used, all_same_sign, r_sd


def cohens_d(a: np.ndarray, b: np.ndarray) -> float:
    """Pooled Cohen's d (a vs b). NaN-safe; 0 if no spread."""
    a = np.asarray(a, float); a = a[~np.isnan(a)]
    b = np.asarray(b, float); b = b[~np.isnan(b)]
    if a.size < 2 or b.size < 2:
        return float("nan")
    sp = np.sqrt(((a.size - 1) * a.var(ddof=1) + (b.size - 1) * b.var(ddof=1)) /
                 (a.size + b.size - 2))
    return float((a.mean() - b.mean()) / sp) if sp else 0.0


def to_common_grid(t_src: np.ndarray, y_src: np.ndarray, t_grid: np.ndarray) -> np.ndarray:
    """Interpolate a per-subject waveform onto a canonical time grid so
    subjects with slightly different sampling rates align for averaging.
    Outside the source support the result is NaN (not flat-extrapolated)."""
    t_src = np.asarray(t_src, float)
    y_src = np.asarray(y_src, float)
    out = np.interp(t_grid, t_src, y_src, left=np.nan, right=np.nan)
    return out


# ---- overlay drawing ------------------------------------------------------
def draw_overlay(ax, t_grid, per_subject_by_cond: dict, cond_styles: dict,
                 subject_ids: list[str], show_band: bool = True,
                 lw_subject: float = 0.7, lw_mean: float = 1.8):
    """Overlay per-subject curves + a bold grand-mean line per condition.

    per_subject_by_cond : {cond_label: list-over-subjects of 1-D arrays on
                           t_grid (NaN-padded where missing)}
    cond_styles         : {cond_label: (color, label)}
    Returns the list of legend handles describing the grand-mean lines.
    """
    from matplotlib.lines import Line2D
    handles = []
    for cond, curves in per_subject_by_cond.items():
        color, clabel = cond_styles[cond]
        stack = []
        for ci, y in enumerate(curves):
            if y is None:
                continue
            y = np.asarray(y, float)
            ax.plot(t_grid, y, color=color, lw=lw_subject, alpha=0.32,
                    solid_capstyle="round")
            stack.append(y)
        if not stack:
            continue
        M = np.vstack(stack)
        with np.errstate(invalid="ignore", divide="ignore"):
            mu = np.nanmean(M, axis=0)
            k = np.sum(~np.isnan(M), axis=0)
            # sample SD across subjects (ddof=1, unbiased — matches pooled_stats
            # convention); defined only where >=2 subjects contribute.
            ss = np.nansum((M - mu) ** 2, axis=0)
            sd = np.sqrt(np.where(k >= 2, ss / np.maximum(k - 1, 1), np.nan))
            sem = sd / np.sqrt(np.maximum(k, 1))
        if show_band:
            ax.fill_between(t_grid, mu - sem, mu + sem, color=color, alpha=0.13, lw=0)
        ax.plot(t_grid, mu, color=color, lw=lw_mean, solid_capstyle="round")
        n_sub = int(np.nanmax(k)) if k.size else 0
        handles.append(Line2D([0], [0], color=color, lw=lw_mean,
                              label=f"{clabel} — mean (n={n_sub} subj)"))
    return handles


def subject_legend_handles(subject_ids: list[str]):
    """A neutral legend mapping line styles to subjects (for overlays where
    subjects share a colour but we still want to name them)."""
    from matplotlib.lines import Line2D
    return [Line2D([0], [0], color=MUTED, lw=0.8, alpha=0.5, label=s)
            for s in subject_ids]
