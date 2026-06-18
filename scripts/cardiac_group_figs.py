"""Group (multi-subject) cardiac/HRV figures.

Cardiac analog of sentiometer_group_figs.py. Pools every subject under
outputs/*/cardiac/ and renders:

  * Overview            -> per-subject overview PNGs reused as small multiples.
  * Event-locked inst-HR-> overlay one line per subject + bold grand mean
                           (collision / oddball / masking / RGB).
  * Bars                -> per-subject dots + grand mean with a pooled-variance
                           error bar (phases / mind-state / SSVEP bands).
  * Correlation matrices-> the MEAN correlation across subjects (Fisher-z
                           averaged) of the 6 cardiac metrics × 7 Sentiometer
                           series, whole session + per block, with a flag where
                           every subject agrees in sign.

Reuses the single-subject code (cardiac_report_lib / cardiac_report_figs /
cardiac_xcorr) verbatim by repointing each module's BUNDLE / SENT_PARQUET.

Run:
  uv run --with pandas --with pyarrow --with matplotlib --with scipy \
      python scripts/cardiac_group_figs.py
"""

from __future__ import annotations

import json
import shutil

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
import numpy as np

import group_report_lib as G
import cardiac_report_lib as CL
import cardiac_report_figs as CF      # reuse _faces_by_outcome
import cardiac_xcorr as XC            # reuse build_aligned / corr_matrix / cmap

LAND = (11.0, 7.6)
FIGDIR = G.GROUPROOT / "cardiac" / "report" / "figs"
SUBJECTS = G.discover_subjects("cardiac")
manifest: dict = {"subjects": SUBJECTS}

METRICS = CL.METRIC_COLS
SENT_LABELS = XC.SENT_LABELS


def load_all():
    out = []
    for s in SUBJECTS:
        CL.BUNDLE = G.OUTROOT / s / "cardiac"
        out.append((s, CL.load()))
    return out


# =========================================================================
# 1. OVERVIEW small multiples
# =========================================================================
def fig_overview_reuse():
    names = []
    for s in SUBJECTS:
        src = G.OUTROOT / s / "cardiac" / "report" / "figs" / "overview.png"
        if src.exists():
            FIGDIR.mkdir(parents=True, exist_ok=True)
            dst = FIGDIR / f"overview_{s}.png"
            shutil.copyfile(src, dst)
            names.append(dst.name)
    manifest["overview"] = names


# =========================================================================
# 2. EVENT-LOCKED INSTANTANEOUS-HR OVERLAYS
# =========================================================================
def fig_inst_hr_overlay(bundles, onsets_by_subj, cond_styles, title, name,
                        pre, post, baseline):
    grid = np.arange(-pre, post + 1e-9, 0.25)
    per_cond = {c: [] for c in cond_styles}
    ncounts = {c: 0 for c in cond_styles}
    for s, b in bundles:
        for c in cond_styles:
            ons = onsets_by_subj[s].get(c)
            if ons is None or len(ons) == 0:
                per_cond[c].append(None); continue
            g, M = CL.epoch_inst_hr(b, ons, pre, post, baseline_s=baseline)
            if M.shape[0]:
                per_cond[c].append(M.mean(0)); ncounts[c] += int(M.shape[0])
            else:
                per_cond[c].append(None)
    fig, ax = plt.subplots(figsize=(8.4, 5.0))
    fig.subplots_adjust(left=0.10, right=0.78, top=0.90, bottom=0.12)
    handles = G.draw_overlay(ax, grid, per_cond, cond_styles, SUBJECTS)
    ax.axvline(0, color=G.MUTED, lw=0.6, ls="--", alpha=0.7)
    ax.axhline(0, color=G.RULE, lw=0.5)
    ax.set_xlabel("Time from event (s)")
    ax.set_ylabel("Δ instantaneous HR (bpm, baseline-corr.)")
    ax.set_title(title, loc="left", fontsize=10, pad=8)
    G.finish_axes(ax)
    subj_leg = [Line2D([0], [0], color=G.MUTED, lw=0.8, alpha=0.4, label=s) for s in SUBJECTS]
    leg1 = ax.legend(handles=handles, loc="upper left", bbox_to_anchor=(1.02, 1.0),
                     fontsize=7, title="condition (mean)")
    ax.add_artist(leg1)
    ax.legend(handles=subj_leg, loc="lower left", bbox_to_anchor=(1.02, 0.0),
              fontsize=6.6, title="thin = subjects")
    for c, n in ncounts.items():
        pass
    manifest.setdefault("erp", {})[name] = G.savefig(fig, FIGDIR, name).name


# =========================================================================
# 3. POOLED BAR GRID (6 metrics, 2x3)
# =========================================================================
def fig_bar_grid(per_subject_groups, cond_order, cond_colors, title, name):
    fig, axes = plt.subplots(2, 3, figsize=LAND)
    fig.subplots_adjust(left=0.07, right=0.985, top=0.86, bottom=0.12,
                        hspace=0.46, wspace=0.30)
    axes = axes.ravel()
    x = np.arange(len(cond_order))
    for ax_i, c in enumerate(METRICS):
        ax = axes[ax_i]
        lab, unit, _ = CL.METRIC_META[c]
        centred = {cc: [] for cc in cond_order}
        subj_dots = {cc: [] for cc in cond_order}
        for s in SUBJECTS:
            g = per_subject_groups.get(s)
            if g is None:
                for cc in cond_order:
                    subj_dots[cc].append(np.nan)
                continue
            cmeans, raw = [], {}
            for cc in cond_order:
                v = np.asarray(g[cc][c], float); v = v[~np.isnan(v)]
                raw[cc] = v
                cmeans.append(v.mean() if v.size else np.nan)
            sg = np.nanmean(cmeans)
            for cc in cond_order:
                v = raw[cc]
                if v.size:
                    centred[cc].append(v - sg)
                    subj_dots[cc].append(float(v.mean() - sg))
                else:
                    subj_dots[cc].append(np.nan)
        bar_h, bar_err = [], []
        for cc in cond_order:
            ps = G.pooled_stats(centred[cc])
            bar_h.append(ps["grand_mean"]); bar_err.append(ps["pooled_sd"])
        bar_h = np.asarray(bar_h, float); bar_err = np.asarray(bar_err, float)
        colors = [cond_colors[cc] for cc in cond_order]
        ax.bar(x, bar_h, yerr=bar_err, color=colors, alpha=0.80, edgecolor=G.INK,
               linewidth=0.4, capsize=2, error_kw={"elinewidth": 0.6})
        for si, s in enumerate(SUBJECTS):
            yvals = [subj_dots[cc][si] if si < len(subj_dots[cc]) else np.nan
                     for cc in cond_order]
            ax.scatter(x, yvals, s=11, color=G.subject_color(si),
                       edgecolor="white", linewidth=0.3, zorder=5)
        ax.axhline(0, color=G.RULE, lw=0.6)
        allv = [v for cc in cond_order for v in subj_dots[cc]]
        allv = np.array(allv + list(np.abs(bar_h) + np.nan_to_num(bar_err)), float)
        span = np.nanmax(np.abs(allv)) if allv.size else 1.0
        span = max(span * 1.30, 1e-9)
        ax.set_ylim(-span, span)
        ax.set_xticks(x); ax.set_xticklabels(cond_order, fontsize=6.0, rotation=0)
        ax.set_title(f"{lab} ({unit})", loc="left", fontsize=8.0, pad=3)
        if ax_i % 3 == 0:
            ax.set_ylabel("Δ from subject grand mean")
        G.finish_axes(ax)
    legend = [Line2D([0], [0], marker="o", linestyle="", markersize=4,
                     markerfacecolor=G.subject_color(i), markeredgecolor="white",
                     label=s) for i, s in enumerate(SUBJECTS)]
    fig.legend(handles=legend, loc="lower center", ncol=len(SUBJECTS), fontsize=6.8,
               frameon=False, bbox_to_anchor=(0.5, 0.005))
    fig.suptitle(title, x=0.07, ha="left", fontsize=11, fontweight="bold", color=G.INK)
    fig.text(0.985, 0.04, "Bars = across-subject mean dev. from subject grand mean · "
             "error = pooled within-subject SD · dots = subjects",
             ha="right", fontsize=6.6, color=G.MUTED)
    manifest.setdefault("bars", {})[name] = G.savefig(fig, FIGDIR, name).name


# =========================================================================
# 4. MEAN-r CORRELATION MATRICES (Fisher-z averaged)
# =========================================================================
def group_heatmap(r_mean, n_used, same_sign, r_sd, n_subj, title, name):
    fig, ax = plt.subplots(figsize=(6.8, 5.4))
    fig.subplots_adjust(left=0.16, right=0.99, top=0.86, bottom=0.20)
    im = ax.imshow(r_mean, cmap=XC._CMAP, vmin=-1, vmax=1, aspect="auto")
    ax.set_xticks(range(len(SENT_LABELS))); ax.set_xticklabels(SENT_LABELS, fontsize=8)
    ax.set_yticks(range(len(METRICS)))
    ax.set_yticklabels([CL.METRIC_META[c][0] for c in METRICS], fontsize=8)
    ax.set_xlabel("Sentiometer series", fontsize=8.5)
    for i in range(r_mean.shape[0]):
        for j in range(r_mean.shape[1]):
            v = r_mean[i, j]
            if np.isnan(v):
                ax.text(j, i, "—", ha="center", va="center", fontsize=7, color=G.MUTED)
                continue
            tc = "white" if abs(v) > 0.55 else G.INK
            ax.text(j, i, f"{v:.2f}", ha="center", va="center", fontsize=7.2, color=tc)
            if same_sign[i, j]:
                ax.text(j + 0.31, i - 0.24, "‡", ha="center", va="center",
                        fontsize=7.0, color=tc, fontweight="bold")
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_xticks(np.arange(-.5, len(SENT_LABELS), 1), minor=True)
    ax.set_yticks(np.arange(-.5, len(METRICS), 1), minor=True)
    ax.grid(which="minor", color="white", linewidth=1.2)
    ax.tick_params(which="both", length=0)
    cb = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.03, ticks=[-1, -0.5, 0, 0.5, 1])
    cb.ax.tick_params(labelsize=7); cb.outline.set_visible(False)
    cb.set_label("mean Pearson r", fontsize=7.5)
    ax.set_title(title, loc="left", fontsize=10, pad=10, color=G.INK, fontweight="bold")
    finite_n = n_used[n_used > 0]
    min_n = int(finite_n.min()) if finite_n.size else 0
    across = (f"{n_subj} subjects" if min_n == n_subj
              else f"{min_n}–{n_subj} subjects per cell")
    fig.text(0.16, 0.085, f"Cell = mean r across {across} (Fisher-z averaged).  "
             "‡ = all contributing subjects agree in sign.", fontsize=6.6, color=G.INK)
    fig.text(0.16, 0.045, "Navy = negative · purple = positive", fontsize=7, color=G.MUTED)
    return G.savefig(fig, FIGDIR, name)


def per_subject_corr(bundles, task_key=None):
    """Stack of per-subject r matrices (n_subj, 6, 7). NaN where unavailable."""
    mats = []
    used = []
    for s, b in bundles:
        XC.SENT_PARQUET = G.OUTROOT / s / "preprocessed" / "sentiometer_timeseries.parquet"
        A = XC.build_aligned(b)
        if A is None:
            mats.append(np.full((len(METRICS), len(SENT_LABELS)), np.nan)); continue
        sub = A if task_key is None else A[A["task"] == task_key]
        R, Q, Neff, n = XC.corr_matrix(sub)
        if n < 8:
            mats.append(np.full((len(METRICS), len(SENT_LABELS)), np.nan))
        else:
            mats.append(R); used.append(s)
    return np.stack(mats, axis=0), used


def fig_corr_matrices(bundles):
    out = {"whole": None, "blocks": {}, "values": {}}
    # whole session
    stack, used = per_subject_corr(bundles, None)
    r_mean, n_used, same_sign, r_sd = G.fisher_mean_r(stack)
    out["whole"] = group_heatmap(r_mean, n_used, same_sign, r_sd, len(used),
                                 "Cardiac × Sentiometer — mean r (whole session)",
                                 "g_xcorr_whole.png").name
    out["values"]["whole"] = {"r_mean": np.where(np.isnan(r_mean), None, r_mean).tolist(),
                              "n_subjects": len(used)}
    # per block
    BLOCK_TITLES = [("task00", "Questionnaire"), ("task01", "Auditory Oddball"),
                    ("task02", "RGB Illuminance"), ("task03", "Backward Masking"),
                    ("task04", "Mind-State"), ("task05", "SSVEP"), ("nap", "Nap")]
    for key, title in BLOCK_TITLES:
        stack, used = per_subject_corr(bundles, key)
        if len(used) < 2:   # need >=2 subjects with a stable matrix to average
            continue
        r_mean, n_used, same_sign, r_sd = G.fisher_mean_r(stack)
        nm = group_heatmap(r_mean, n_used, same_sign, r_sd, len(used),
                           f"Cardiac × Sentiometer — mean r ({title})",
                           f"g_xcorr_{key}.png").name
        out["blocks"][key] = {"name": nm, "title": title, "n_subjects": len(used)}
    manifest["xcorr"] = out
    print(f"  xcorr: whole + {len(out['blocks'])} block mean-r matrices")


# =========================================================================
# Per-subject onset / window extractors
# =========================================================================
def main():
    G.apply_style()
    bundles = load_all()
    print("loaded subjects:", [s for s, _ in bundles])
    fig_overview_reuse()

    # ---- inst-HR ERP overlays --------------------------------------------
    onsets = {s: {"collision": b.marker_times_lsl("collision", task="task04")}
              for s, b in bundles}
    fig_inst_hr_overlay(bundles, onsets, {"collision": (CL.C[5], "collision")},
                        "Task 04 · Collision-locked instantaneous HR (group overlay + mean)",
                        "g_hr_collision.png", pre=10, post=20, baseline=(-10, -2))

    onsets = {s: {"deviant": b.marker_times_lsl("tone_deviant", task="task01"),
                  "standard": b.marker_times_lsl("tone_standard", task="task01")}
              for s, b in bundles}
    fig_inst_hr_overlay(bundles, onsets,
                        {"deviant": (CL.C[5], "deviant"), "standard": (CL.C[0], "standard")},
                        "Task 01 · Tone-locked instantaneous HR (group overlay + mean)",
                        "g_hr_oddball.png", pre=5, post=10, baseline=(-5, -1))

    onsets = {}
    for s, b in bundles:
        seen, unseen = CF._faces_by_outcome(b)
        onsets[s] = {"seen": seen, "unseen": unseen}
    fig_inst_hr_overlay(bundles, onsets,
                        {"seen": (CL.C[3], "seen"), "unseen": (CL.C[5], "unseen")},
                        "Task 03 · Face-onset instantaneous HR, seen vs unseen (group overlay + mean)",
                        "g_hr_masking.png", pre=5, post=10, baseline=(-5, -1))

    onsets = {s: {"Red": b.marker_times_lsl("color_red", task="task02"),
                  "Green": b.marker_times_lsl("color_green", task="task02"),
                  "Blue": b.marker_times_lsl("color_blue", task="task02")}
              for s, b in bundles}
    fig_inst_hr_overlay(bundles, onsets,
                        {"Red": ("#a8473b", "Red"), "Green": ("#4a7a52", "Green"),
                         "Blue": ("#3b5da8", "Blue")},
                        "Task 02 · Colour-onset instantaneous HR (group overlay + mean, exploratory)",
                        "g_hr_rgb.png", pre=3, post=5, baseline=(-3, -1))

    # ---- pooled bars: phases ---------------------------------------------
    order = [("task00", "Quest"), ("task01", "Oddball"), ("task02", "RGB"),
             ("task03", "Mask"), ("task04", "Mind"), ("task05", "SSVEP"), ("nap", "Nap")]
    groups = {}
    for s, b in bundles:
        g = {}
        for key, short in order:
            sub = b.ts[b.ts.task == key]
            g[short] = {c: sub[c].to_numpy(dtype=float) for c in METRICS}
        groups[s] = g
    palette = [G.C[2], G.C[0], G.C[3], G.C[4], G.C[5], G.C[1], G.INK]
    cond_colors = {short: palette[i % len(palette)] for i, (_, short) in enumerate(order)}
    fig_bar_grid(groups, [short for _, short in order], cond_colors,
                 "Cardiac metrics by session phase (group: pooled variance + subject dots)",
                 "g_bar_phases.png")

    # ---- pooled bars: mind-state -----------------------------------------
    def block_win(b, prefix, sub_s=30.0):
        s_ = b.mk.loc[b.mk.label == f"{prefix}_start", "t_lsl"]
        e_ = b.mk.loc[b.mk.label == f"{prefix}_end", "t_lsl"]
        if not (len(s_) and len(e_)):
            return None
        return CL.block_metrics(b, float(s_.iloc[0]), float(e_.iloc[0]), sub_s=sub_s)
    groups = {}
    for s, b in bundles:
        game = block_win(b, "task04_game"); medi = block_win(b, "task04_meditation")
        if game and medi:
            groups[s] = {"Gameplay": game, "Meditation": medi}
    fig_bar_grid(groups, ["Gameplay", "Meditation"],
                 {"Gameplay": G.C[3], "Meditation": G.C[0]},
                 "Task 04 · Mind-State — gameplay vs meditation (group: pooled variance + subject dots)",
                 "g_bar_mindstate.png")

    # ---- pooled bars: SSVEP bands ----------------------------------------
    bands = [(1, 4, "δ 1–4"), (4, 8, "θ 4–8"), (8, 13, "α 8–13"),
             (13, 30, "β 13–30"), (30, 40, "γ 30–40")]
    groups = {}
    for s, b in bundles:
        g = {}
        ok = True
        for lo, hi, lbl in bands:
            win = CL.ramp_band_window_lsl(b, lo, hi)
            if win is None:
                ok = False; break
            g[lbl] = CL.block_metrics(b, win[0], win[1], sub_s=20.0)
        if ok:
            groups[s] = g
    band_colors = {lbl: G.C[i % 6] for i, (_, _, lbl) in enumerate(bands)}
    fig_bar_grid(groups, [lbl for _, _, lbl in bands], band_colors,
                 "Task 05 · SSVEP — cardiac metrics by frequency band (group: pooled variance + subject dots)",
                 "g_bar_ssvep.png")

    # ---- mean-r correlation matrices -------------------------------------
    fig_corr_matrices(bundles)

    (FIGDIR / "figs_manifest.json").write_text(json.dumps(manifest, indent=2, default=str))
    print("MANIFEST keys:", list(manifest.keys()))


if __name__ == "__main__":
    main()
