"""Group (multi-subject) Sentiometer figures.

Pools every subject discovered under outputs/*/preprocessed/ and renders the
group analogs of the single-subject report's figures:

  * Overview      -> small-multiples (the per-subject overview PNGs, reused),
                     because whole-session timelines don't share a time axis.
  * ERPs          -> overlay one thin line per subject + a bold grand-mean line
                     (between-subject SEM band), on a common time-from-onset grid.
  * Bars          -> per-subject dots + grand mean (of the per-subject means)
                     with a pooled-variance error bar.
  * Effect sizes  -> per-subject Cohen's d + the across-subject mean, per contrast.

Reuses the single-subject analysis code verbatim (sentiometer_report_lib /
sentiometer_report_figs) by repointing L.BUNDLE at each subject.

Run:
  uv run --with pandas --with pyarrow --with matplotlib --with scipy \
      python scripts/sentiometer_group_figs.py
"""

from __future__ import annotations

import json
import shutil

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
import numpy as np

import group_report_lib as G
import sentiometer_report_lib as L
import sentiometer_report_figs as F  # reuse _faces_by_outcome

LAND = (11.0, 7.6)
FIGDIR = G.GROUPROOT / "sentiometer" / "report" / "figs"
SUBJECTS = G.discover_subjects("sentiometer")
manifest: dict = {"subjects": SUBJECTS}

# Common ERP grid (ms). Subjects are interpolated onto this so slightly
# different sampling rates still average cleanly.
T_ERP = np.arange(-300.0, 300.0 + 1e-6, 2.0)


def load_all() -> list[tuple[str, L.Bundle]]:
    out = []
    for s in SUBJECTS:
        L.BUNDLE = G.OUTROOT / s / "preprocessed"
        out.append((s, L.load()))
    return out


# =========================================================================
# 1. OVERVIEW — reuse each subject's overview.png as small multiples
# =========================================================================
def fig_overview_reuse():
    names = []
    for s in SUBJECTS:
        src = G.OUTROOT / s / "report" / "figs" / "overview.png"
        if src.exists():
            dst = FIGDIR / f"overview_{s}.png"
            FIGDIR.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            names.append(dst.name)
    manifest["overview"] = names


# =========================================================================
# 2. ERP OVERLAYS
# =========================================================================
def _subject_erp(b, col, onsets_lsl, pre=300, post=300, baseline=(-300, 0)):
    """One subject's mean ERP waveform for a series/condition, on T_ERP."""
    if onsets_lsl is None or len(onsets_lsl) == 0:
        return None, 0
    t_ms, M = L.epoch(b, col, onsets_lsl, pre, post, baseline_ms=baseline)
    if M.shape[0] == 0:
        return None, 0
    return G.to_common_grid(t_ms, M.mean(0), T_ERP), int(M.shape[0])


def fig_erp_overlay(bundles, onsets_by_subj: dict, cond_styles: dict,
                    title: str, name: str):
    """7-panel ERP grid (one per series); overlay subjects + grand mean.

    onsets_by_subj : {subject_id: {cond_label: onsets_lsl}}
    cond_styles    : {cond_label: (color, display_label)}
    """
    fig, axes = plt.subplots(2, 4, figsize=LAND)
    fig.subplots_adjust(left=0.06, right=0.985, top=0.88, bottom=0.09,
                        hspace=0.42, wspace=0.30)
    axes = axes.ravel()
    n_counts = {c: [] for c in cond_styles}
    for ax_i, (col, slabel, *_rest) in enumerate(L.SERIES):
        ax = axes[ax_i]
        per_cond = {c: [] for c in cond_styles}
        for s, b in bundles:
            for cond in cond_styles:
                wf, n = _subject_erp(b, col, onsets_by_subj[s].get(cond))
                per_cond[cond].append(wf)
                if ax_i == 0:
                    n_counts[cond].append(n)
        handles = G.draw_overlay(ax, T_ERP, per_cond, cond_styles, SUBJECTS)
        ax.axvline(0, color=G.MUTED, lw=0.6, ls="--", alpha=0.7)
        ax.axhline(0, color=G.RULE, lw=0.5)
        ax.set_title(slabel, loc="left", fontsize=8.5, pad=4)
        ax.set_xlabel("Time from onset (ms)")
        if ax_i % 4 == 0:
            ax.set_ylabel("Δ amplitude (baseline-corr.)")
        G.finish_axes(ax)
        if ax_i == 0:
            ax.legend(handles=handles, loc="upper left", fontsize=6.0)
    axes[-1].axis("off")
    note = ["Thin = per-subject mean ERP", "Bold = grand mean across subjects",
            "Shaded = ±1 between-subject SEM", "Baseline −300..0 ms · 0 = onset",
            f"{len(SUBJECTS)} subjects: " + ", ".join(SUBJECTS)]
    for c, (color, lbl) in cond_styles.items():
        tot = sum(n_counts[c])
        note.append(f"{lbl}: {tot} epochs pooled")
    axes[-1].text(0.02, 0.95, "\n".join(note), va="top", fontsize=6.6,
                  color=G.MUTED, transform=axes[-1].transAxes)
    fig.suptitle(title, x=0.06, ha="left", fontsize=11, fontweight="bold", color=G.INK)
    manifest.setdefault("erp", {})[name] = G.savefig(fig, FIGDIR, name).name


# =========================================================================
# 3. POOLED BAR GRIDS
# =========================================================================
def fig_bar_grid(per_subject_groups: dict, cond_order: list, cond_colors: dict,
                 title: str, name: str):
    """7-panel pooled bar grid (one per series).

    per_subject_groups : {subject_id: {cond_label: {series_col: values_array}}}
      where values_array is that subject's bin/window values for the condition
      (raw units), exactly as the single-subject bar used.

    For each series panel we centre every subject's condition means by that
    subject's panel grand mean (removing the per-subject DC offset), then:
      bar height = mean across subjects of the centred condition mean
      error bar  = pooled within-subject SD (the pooled variance)
      dots       = each subject's centred condition mean
    """
    fig, axes = plt.subplots(2, 4, figsize=LAND)
    fig.subplots_adjust(left=0.06, right=0.985, top=0.86, bottom=0.10,
                        hspace=0.50, wspace=0.34)
    axes = axes.ravel()
    x = np.arange(len(cond_order))
    values_out = {}
    for ax_i, (col, slabel, *_rest) in enumerate(L.SERIES):
        ax = axes[ax_i]
        # build per-condition list of per-subject CENTRED bin arrays
        centred = {c: [] for c in cond_order}
        subj_dots = {c: [] for c in cond_order}
        for s in SUBJECTS:
            g = per_subject_groups.get(s)
            if g is None:
                continue
            # subject panel grand mean = mean over conditions of the condition mean
            cmeans = []
            raw = {}
            for c in cond_order:
                v = np.asarray(g[c][col], float)
                v = v[~np.isnan(v)]
                raw[c] = v
                cmeans.append(v.mean() if v.size else np.nan)
            sg = np.nanmean(cmeans)
            for c in cond_order:
                v = raw[c]
                if v.size:
                    centred[c].append(v - sg)
                    subj_dots[c].append(float(v.mean() - sg))
                else:
                    subj_dots[c].append(np.nan)
        bar_h, bar_err = [], []
        for c in cond_order:
            ps = G.pooled_stats(centred[c])
            bar_h.append(ps["grand_mean"])
            bar_err.append(ps["pooled_sd"])
        bar_h = np.asarray(bar_h, float)
        bar_err = np.asarray(bar_err, float)
        colors = [cond_colors[c] for c in cond_order]
        ax.bar(x, bar_h, yerr=bar_err, color=colors, alpha=0.80,
               edgecolor=G.INK, linewidth=0.4, capsize=2, error_kw={"elinewidth": 0.6})
        # per-subject dots
        for si, s in enumerate(SUBJECTS):
            yvals = [subj_dots[c][si] if si < len(subj_dots[c]) else np.nan
                     for c in cond_order]
            ax.scatter(x, yvals, s=11, color=G.subject_color(si),
                       edgecolor="white", linewidth=0.3, zorder=5)
        ax.axhline(0, color=G.RULE, lw=0.6)
        allv = np.concatenate([np.abs(bar_h) + np.nan_to_num(bar_err),
                               np.abs(np.nan_to_num([v for c in cond_order
                                                     for v in subj_dots[c]]))])
        span = np.nanmax(allv) if allv.size else 1.0
        span = max(span * 1.30, 1e-9)
        ax.set_ylim(-span, span)
        ax.set_xticks(x)
        ax.set_xticklabels(cond_order, fontsize=6.3, rotation=0)
        ax.set_title(slabel, loc="left", fontsize=8.0, pad=3)
        if ax_i % 4 == 0:
            ax.set_ylabel("Δ from subject grand mean")
        G.finish_axes(ax)
        values_out[slabel] = {c: float(bar_h[i]) for i, c in enumerate(cond_order)}
    axes[-1].axis("off")
    legend = [Line2D([0], [0], marker="o", linestyle="", markersize=4,
                     markerfacecolor=G.subject_color(i), markeredgecolor="white",
                     label=s) for i, s in enumerate(SUBJECTS)]
    axes[-1].legend(handles=legend, loc="upper left", fontsize=6.6, title="subject dots")
    axes[-1].text(0.02, 0.50,
                  "Bar = mean across subjects of each condition's deviation\n"
                  "from that subject's panel grand mean (per-subject DC removed).\n"
                  "Error = pooled within-subject SD (pooled variance).\n"
                  "Dots = each subject's deviation.",
                  va="top", fontsize=6.6, color=G.MUTED, transform=axes[-1].transAxes)
    fig.suptitle(title, x=0.06, ha="left", fontsize=11, fontweight="bold", color=G.INK)
    manifest.setdefault("bars", {})[name] = G.savefig(fig, FIGDIR, name).name
    manifest.setdefault("bar_values", {})[name] = values_out


# =========================================================================
# Per-subject onset / window extractors (mirror the single-subject scripts)
# =========================================================================
def rgb_windows(b):
    """{color_key: (starts_lsl, ends_lsl)} — color onset to next iti/color."""
    m = b.mk[b.mk.task == "task02"].sort_values("t_lsl").reset_index(drop=True)
    ev, tl = m["event"].to_numpy(), m["t_lsl"].to_numpy()
    wins = {"color_red": [], "color_green": [], "color_blue": []}
    for i, e in enumerate(ev):
        if e in wins:
            for j in range(i + 1, len(ev)):
                if ev[j] == "iti" or ev[j] in wins:
                    wins[e].append((tl[i], tl[j])); break
    out = {}
    for k, w in wins.items():
        if w:
            out[k] = (np.array([a for a, _ in w]), np.array([b_ for _, b_ in w]))
        else:
            out[k] = (np.array([]), np.array([]))
    return out


def mindstate_bins(b, nm, bin_s=5.0):
    s = b.mk.loc[b.mk.label == f"task04_{nm}_start", "t_lsl"]
    e = b.mk.loc[b.mk.label == f"task04_{nm}_end", "t_lsl"]
    if not (len(s) and len(e)):
        return None
    edges = np.arange(float(s.iloc[0]), float(e.iloc[0]), bin_s)
    return edges[:-1], edges[1:]


def ssvep_band_bins(b):
    """{band_label: {series_col: binned values}} for one subject."""
    spec = L.ssvep_ramp_spec(b)
    rs_rel = b.mk.loc[b.mk.event == "ramp_start", "t_rel_s"]
    if spec is None or not len(rs_rel):
        return None
    rs_rel = float(rs_rel.iloc[0]); dur = float(spec["durationSeconds"])
    t = b.ts["t_rel_s"].to_numpy()
    inramp = (t >= rs_rel) & (t <= rs_rel + dur)
    hz = L.eff_hz_at(t[inramp], rs_rel, spec)
    bands = [(1, 4, "δ 1–4"), (4, 8, "θ 4–8"), (8, 13, "α 8–13"),
             (13, 30, "β 13–30"), (30, 41, "γ 30–40")]
    out = {lbl: {} for _, _, lbl in bands}
    for scol in L.SERIES_COLS:
        y = b.ts[scol].to_numpy(dtype=np.float64)[inramp]
        for lo, hi, lbl in bands:
            msk = (hz >= lo) & (hz < hi)
            yy = y[msk]
            if len(yy) > 1000:
                nb = max(2, len(yy) // 1000)
                binned = np.array([seg.mean() for seg in np.array_split(yy, nb)])
            else:
                binned = yy if len(yy) else np.array([np.nan])
            out[lbl][scol] = binned
    return out


# =========================================================================
def main():
    G.apply_style()
    bundles = load_all()
    print("loaded subjects:", [s for s, _ in bundles])
    fig_overview_reuse()

    # ---- ERP: oddball deviant vs standard --------------------------------
    onsets = {s: {"deviant": b.marker_times_lsl("tone_deviant", task="task01"),
                  "standard": b.marker_times_lsl("tone_standard", task="task01")}
              for s, b in bundles}
    fig_erp_overlay(bundles, onsets,
                    {"deviant": (L.C[5], "deviant"), "standard": (L.C[0], "standard")},
                    "Task 01 · Auditory Oddball — Deviant vs Standard ERP (group overlay + mean)",
                    "g_erp_oddball.png")

    # ---- ERP: RGB color onset --------------------------------------------
    onsets = {s: {"Red": b.marker_times_lsl("color_red", task="task02"),
                  "Green": b.marker_times_lsl("color_green", task="task02"),
                  "Blue": b.marker_times_lsl("color_blue", task="task02")}
              for s, b in bundles}
    fig_erp_overlay(bundles, onsets,
                    {"Red": ("#a8473b", "Red"), "Green": ("#4a7a52", "Green"),
                     "Blue": ("#3b5da8", "Blue")},
                    "Task 02 · RGB — Color-onset ERP (group overlay + mean)",
                    "g_erp_rgb.png")

    # ---- ERP: masking face-onset by outcome ------------------------------
    onsets = {}
    for s, b in bundles:
        seen, unseen = F._faces_by_outcome(b)
        onsets[s] = {"face→seen": seen, "face→unseen": unseen}
    fig_erp_overlay(bundles, onsets,
                    {"face→seen": (L.C[3], "face→seen"), "face→unseen": (L.C[5], "face→unseen")},
                    "Task 03 · Backward Masking — Face-onset by outcome (group overlay + mean)",
                    "g_erp_masking_faceonset.png")

    # ---- ERP: collision --------------------------------------------------
    onsets = {s: {"collision": b.marker_times_lsl("collision", task="task04")}
              for s, b in bundles}
    fig_erp_overlay(bundles, onsets, {"collision": (L.C[5], "collision")},
                    "Task 04 · Mind-State — Collision-locked ERP (group overlay + mean)",
                    "g_erp_collision.png")

    # ---- BAR: RGB per color ----------------------------------------------
    groups = {}
    for s, b in bundles:
        wins = rgb_windows(b)
        name_map = {"color_red": "Red", "color_green": "Green", "color_blue": "Blue"}
        g = {v: {} for v in name_map.values()}
        for key, lbl in name_map.items():
            st, en = wins[key]
            for scol in L.SERIES_COLS:
                g[lbl][scol] = L.window_means(b, scol, st, en)
        groups[s] = g
    fig_bar_grid(groups, ["Red", "Green", "Blue"],
                 {"Red": "#a8473b", "Green": "#4a7a52", "Blue": "#3b5da8"},
                 "Task 02 · RGB — Mean amplitude per color (group: pooled variance + subject dots)",
                 "g_bar_rgb.png")

    # ---- BAR: mind-state gameplay vs meditation --------------------------
    groups = {}
    for s, b in bundles:
        g = {"Gameplay": {}, "Meditation": {}}
        for lbl, nm in [("Gameplay", "game"), ("Meditation", "meditation")]:
            bins = mindstate_bins(b, nm)
            for scol in L.SERIES_COLS:
                g[lbl][scol] = (L.window_means(b, scol, bins[0], bins[1])
                                if bins else np.array([np.nan]))
        groups[s] = g
    fig_bar_grid(groups, ["Gameplay", "Meditation"],
                 {"Gameplay": L.C[3], "Meditation": L.C[0]},
                 "Task 04 · Mind-State — Gameplay vs Meditation (group: pooled variance + subject dots)",
                 "g_bar_mindstate.png")

    # ---- BAR: SSVEP by band ----------------------------------------------
    groups = {}
    band_order = ["δ 1–4", "θ 4–8", "α 8–13", "β 13–30", "γ 30–40"]
    for s, b in bundles:
        sb = ssvep_band_bins(b)
        if sb is not None:
            groups[s] = sb
    band_colors = {lbl: L.C[i % 6] for i, lbl in enumerate(band_order)}
    fig_bar_grid(groups, band_order, band_colors,
                 "Task 05 · SSVEP — Mean amplitude by effective-frequency band (group: pooled variance + subject dots)",
                 "g_bar_ssvep.png")

    (FIGDIR / "figs_manifest.json").write_text(json.dumps(manifest, indent=2, default=str))
    print("MANIFEST:", json.dumps({k: v for k, v in manifest.items() if k != "bar_values"},
                                  indent=2, default=str))


if __name__ == "__main__":
    main()
