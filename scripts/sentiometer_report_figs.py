"""Generate every figure for the P013 Sentiometer phase-resolved report.

Three families, all covering the 7 Sentiometer series:
  1. Full-session two-band overview (one landscape page).
  2. Per-block zoom (one landscape page per phase) with event markers.
  3. Event-grouped analysis: baseline-corrected ERPs (-300..+300 ms) and
     condition bar charts.

Run:
  uv run --with pandas --with pyarrow --with matplotlib --with scipy \
      python scripts/sentiometer_report_figs.py
Writes PNGs to outputs/P013_S01/report/figs/ and a figs_manifest.json.
"""

from __future__ import annotations

import json

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.lines import Line2D
import numpy as np

import sentiometer_report_lib as L

LAND = (11.0, 7.6)     # landscape figure inches (fits Letter landscape page)
manifest: dict = {}


# =========================================================================
# 1. FULL-SESSION TWO-BAND OVERVIEW
# =========================================================================
def fig_overview(b: L.Bundle):
    task_end = next(p.t1 for p in b.phases if p.key == "task05")
    nap = next(p for p in b.phases if p.key == "nap")
    t = b.ts["t_rel_s"].to_numpy()

    task_m = t <= task_end
    nap_m = t > task_end

    fig, (axt, axn) = plt.subplots(
        2, 1, figsize=LAND, height_ratios=[1.0, 0.62])
    fig.subplots_adjust(left=0.06, right=0.985, top=0.90, bottom=0.07, hspace=0.42)

    # offset each series vertically so 7 z-scored traces are legible
    offsets = {col: i * 7.0 for i, col in enumerate(L.SERIES_COLS)}

    def draw(ax, mask, n_buckets):
        tt = t[mask]
        for col, label, color, lw, alpha, emph in L.SERIES:
            y = b.ts[col + "_z"].to_numpy()[mask]
            td, yd = L.minmax_downsample(tt, y, n_buckets)
            ax.plot(td / 60.0, yd + offsets[col], color=color,
                    lw=lw if emph else 0.5, alpha=alpha, solid_capstyle="round")
        ax.set_yticks([offsets[c] for c in L.SERIES_COLS])
        ax.set_yticklabels([s[1] for s in L.SERIES])
        L.finish_axes(ax)
        ax.margins(x=0.005)

    # --- top band: task suite ---
    draw(axt, task_m, 2600)
    axt.set_xlim(t[task_m].min() / 60.0, task_end / 60.0)
    axt.set_title("Task suite — 0 to {:.0f} min (z-scored, vertically offset)".format(task_end / 60),
                  loc="left", pad=18)
    axt.set_xlabel("Time from session start (min)")

    # block bands + labels on the task axis
    ymax = max(offsets.values()) + 6
    ymin = -6
    for p in b.phases:
        if p.key == "nap":
            continue
        axt.axvspan(p.t0 / 60, p.t1 / 60, color=p.color, alpha=0.05, lw=0)
        axt.axvline(p.t0 / 60, color=p.color, lw=0.6, alpha=0.45, ls=(0, (4, 3)))
        axt.text((p.t0) / 60 + 0.15, ymax, p.title.split(" (")[0],
                 rotation=0, va="bottom", ha="left", fontsize=6.0,
                 color=L.INK, fontweight="bold")
    axt.set_ylim(ymin, ymax + 5)

    # --- bottom band: nap ---
    draw(axn, nap_m, 2600)
    axn.set_xlim(task_end / 60.0, nap.t1 / 60.0)
    axn.set_title("Post-session nap — {:.0f} to {:.0f} min ({:.0f} min continuous)".format(
        task_end / 60, nap.t1 / 60, (nap.t1 - nap.t0) / 60), loc="left", pad=8)
    axn.set_xlabel("Time from session start (min)")
    axn.axvspan(nap.t0 / 60, nap.t1 / 60, color=INK_A(), alpha=0.04, lw=0)
    axn.set_ylim(ymin, ymax + 5)

    fig.suptitle("", )
    p = L.savefig(fig, "overview.png")
    manifest["overview"] = p.name


def INK_A():
    return L.INK


# =========================================================================
# 2. PER-BLOCK ZOOM PAGES
# =========================================================================
# event marker overlays per block: (event, label, colour, linestyle)
BLOCK_EVENTS = {
    "task01": [
        ("tone_deviant", "deviant", L.C[5], "-"),
        ("response_hit", "hit", L.C[3], ":"),
    ],
    "task02": [
        ("color_red", "R", "#a8473b", "-"),
        ("color_green", "G", "#4a7a52", "-"),
        ("color_blue", "B", "#3b5da8", "-"),
    ],
    "task03": [
        ("face_onset", "face", L.C[0], "-"),
        ("catch_trial", "catch", L.MUTED, "--"),
        ("response_seen", "seen", L.C[3], ":"),
    ],
    "task04": [
        ("collision", "collision", L.C[5], "-"),
        ("speed_increase", "speed+", L.C[4], "--"),
    ],
    "task05": [],
}


def _draw_block_series(ax, b, t0, t1, n_buckets=2400):
    t = b.ts["t_rel_s"].to_numpy()
    m = (t >= t0) & (t <= t1)
    tt = t[m]
    offsets = {col: i * 7.0 for i, col in enumerate(L.SERIES_COLS)}
    for col, label, color, lw, alpha, emph in L.SERIES:
        y = b.ts[col + "_z"].to_numpy()[m]
        td, yd = L.minmax_downsample(tt, y, n_buckets)
        ax.plot(td, yd + offsets[col], color=color,
                lw=lw if emph else 0.5, alpha=alpha, solid_capstyle="round")
    ax.set_yticks([offsets[c] for c in L.SERIES_COLS])
    ax.set_yticklabels([s[1] for s in L.SERIES])
    L.finish_axes(ax)
    ax.margins(x=0.005)
    return offsets


def fig_block(b: L.Bundle, phase: L.Phase):
    fig, ax = plt.subplots(figsize=LAND)
    fig.subplots_adjust(left=0.06, right=0.985, top=0.88, bottom=0.10)
    offsets = _draw_block_series(ax, b, phase.t0, phase.t1)
    ymax = max(offsets.values()) + 6
    ax.set_ylim(-6, ymax + 3)
    ax.set_xlim(phase.t0, phase.t1)
    ax.set_xlabel("Time from session start (s)")
    ax.set_title(f"{phase.title} — {phase.t0:.0f}–{phase.t1:.0f} s "
                 f"({phase.t1 - phase.t0:.0f} s, z-scored & vertically offset)",
                 loc="left", pad=22)

    # sub-block shading for task04
    if phase.key == "task04":
        for nm, lbl, col in [("game", "Gameplay", L.C[3]),
                             ("meditation", "Meditation", L.C[0])]:
            s = b.mk.loc[b.mk.label == f"task04_{nm}_start", "t_rel_s"]
            e = b.mk.loc[b.mk.label == f"task04_{nm}_end", "t_rel_s"]
            if len(s) and len(e):
                ax.axvspan(s.iloc[0], e.iloc[0], color=col, alpha=0.06, lw=0)
                ax.text((s.iloc[0] + e.iloc[0]) / 2, ymax, lbl, ha="center",
                        va="bottom", fontsize=7, color=L.INK, fontweight="bold")
        br_s = b.mk.loc[b.mk.label == "task04_break_start", "t_rel_s"]
        br_e = b.mk.loc[b.mk.label == "task04_break_end", "t_rel_s"]
        if len(br_s):
            ax.axvspan(br_s.iloc[0], br_e.iloc[0], color=L.C[4], alpha=0.10, lw=0)

    # event marker overlays
    handles = []
    for ev, lbl, color, ls in BLOCK_EVENTS.get(phase.key, []):
        times = b.marker_times(ev, task=phase.key)
        times = times[(times >= phase.t0) & (times <= phase.t1)]
        for x in times:
            ax.axvline(x, color=color, lw=0.4, ls=ls, alpha=0.35, ymax=0.97)
        if len(times):
            handles.append(Line2D([0], [0], color=color, ls=ls, lw=1.0,
                                  label=f"{lbl} (n={len(times)})"))

    # SSVEP: overlay effective Hz on a twin axis
    if phase.key == "task05":
        spec = L.ssvep_ramp_spec(b)
        rb = b.marker_times("ramp_begin", task="task05")
        if spec is not None and len(rb):
            rs = b.mk.loc[b.mk.event == "ramp_start", "t_rel_s"]
            rs = float(rs.iloc[0]) if len(rs) else float(rb[0])
            tt = np.linspace(phase.t0, phase.t1, 1500)
            hz = L.eff_hz_at(tt, rs, spec)
            ax2 = ax.twinx()
            ax2.plot(tt, hz, color=L.C[4], lw=1.3, alpha=0.9)
            ax2.set_ylabel("Effective SSVEP (Hz)", color=L.C[4])
            ax2.tick_params(axis="y", colors=L.C[4])
            for sp in ("top",):
                ax2.spines[sp].set_visible(False)
            ax2.spines["right"].set_color(L.C[4])
            handles.append(Line2D([0], [0], color=L.C[4], lw=1.3,
                                  label="effective Hz (40→1)"))

    if handles:
        ax.legend(handles=handles, loc="upper right", ncol=min(4, len(handles)),
                  bbox_to_anchor=(1.0, 1.10))

    p = L.savefig(fig, f"block_{phase.key}.png")
    manifest.setdefault("blocks", {})[phase.key] = p.name


# =========================================================================
# 3. EVENT-GROUPED ANALYSIS
# =========================================================================
def _erp_panel(ax, t_ms, curves, title, ylabel=True):
    """curves: list of (label, color, M) where M=(n,samp)."""
    for label, color, M in curves:
        if M.shape[0] == 0:
            continue
        mu = M.mean(0)
        sem = M.std(0) / max(np.sqrt(M.shape[0]), 1)
        ax.fill_between(t_ms, mu - sem, mu + sem, color=color, alpha=0.15, lw=0)
        ax.plot(t_ms, mu, color=color, lw=1.4, label=f"{label} (n={M.shape[0]})")
    ax.axvline(0, color=L.MUTED, lw=0.6, ls="--", alpha=0.7)
    ax.axhline(0, color=L.RULE, lw=0.5)
    ax.set_title(title, loc="left", fontsize=8.5, pad=4)
    ax.set_xlabel("Time from onset (ms)")
    if ylabel:
        ax.set_ylabel("Δ amplitude (baseline-corr.)")
    L.finish_axes(ax)
    ax.legend(loc="upper left", fontsize=6.2)


def fig_erp_grid(b: L.Bundle, onsets: dict, title: str, name: str,
                 pre=300, post=300, baseline=(-300, 0)):
    """7-panel ERP grid (one per series). onsets: {label:(color,onsets_lsl)}."""
    fig, axes = plt.subplots(2, 4, figsize=LAND)
    fig.subplots_adjust(left=0.06, right=0.985, top=0.88, bottom=0.09,
                        hspace=0.42, wspace=0.30)
    axes = axes.ravel()
    raw_baselines = {}
    for ax_i, (col, slabel, color, *_rest) in enumerate(L.SERIES):
        ax = axes[ax_i]
        curves = []
        for clabel, (ccolor, ons) in onsets.items():
            t_ms, M = L.epoch(b, col, ons, pre, post, baseline_ms=baseline)
            # capture raw (uncorrected) pre-onset level for caption
            _, Mraw = L.epoch(b, col, ons, pre, post, baseline_ms=None)
            if Mraw.shape[0]:
                bl = (t_ms >= baseline[0]) & (t_ms <= baseline[1])
                raw_baselines.setdefault(col, {})[clabel] = float(Mraw[:, bl].mean())
            curves.append((clabel, ccolor, M))
        _erp_panel(ax, t_ms, curves, slabel, ylabel=(ax_i % 4 == 0))
    axes[-1].axis("off")  # 7 series in an 8-grid; last cell holds the legend note
    axes[-1].text(0.02, 0.95, "Shaded = ±1 SEM\nBaseline: {}..{} ms\n0 = event onset".format(*baseline),
                  va="top", fontsize=7, color=L.MUTED, transform=axes[-1].transAxes)
    fig.suptitle(title, x=0.06, ha="left", fontsize=11, fontweight="bold", color=L.INK)
    p = L.savefig(fig, name)
    manifest.setdefault("erp", {})[name] = p.name
    return raw_baselines


def fig_bar_grid(b: L.Bundle, groups: dict, title: str, name: str, ylab: str):
    """7-panel bar grid showing each condition's deviation from the panel's
    grand mean (raw units). 0 = no difference between conditions, so the
    scientific contrast is legible even when the absolute level is large.
    The grand mean for each series is printed in the panel title.

    groups maps cond_label -> (color, {series_col: per_window_values}).
    """
    fig, axes = plt.subplots(2, 4, figsize=LAND)
    fig.subplots_adjust(left=0.06, right=0.985, top=0.86, bottom=0.10,
                        hspace=0.50, wspace=0.34)
    axes = axes.ravel()
    labels = list(groups.keys())
    colors = [groups[k][0] for k in labels]
    for ax_i, (col, slabel, *_rest) in enumerate(L.SERIES):
        ax = axes[ax_i]
        means, sems = [], []
        for k in labels:
            vals = groups[k][1][col]
            vals = vals[~np.isnan(vals)]
            means.append(vals.mean() if len(vals) else np.nan)
            sems.append(vals.std() / np.sqrt(len(vals)) if len(vals) else 0.0)
        means = np.asarray(means, float)
        grand = np.nanmean(means)
        dev = means - grand
        x = np.arange(len(labels))
        ax.bar(x, dev, yerr=sems, color=colors, alpha=0.85,
               edgecolor=L.INK, linewidth=0.4, capsize=2, error_kw={"elinewidth": 0.6})
        ax.axhline(0, color=L.RULE, lw=0.6)
        # symmetric y so 0 sits centered and effect size reads honestly
        span = np.nanmax(np.abs(dev) + np.asarray(sems)) if len(dev) else 1.0
        span = max(span * 1.35, 1e-9)
        ax.set_ylim(-span, span)
        ax.set_xticks(x)
        ax.set_xticklabels(labels, fontsize=6.5, rotation=0)
        gm = f"{grand:.1f}" if abs(grand) >= 1 else f"{grand:.3f}"
        ax.set_title(f"{slabel}   (grand mean {gm})", loc="left", fontsize=8.0, pad=3)
        if ax_i % 4 == 0:
            ax.set_ylabel("Δ from grand mean")
        L.finish_axes(ax)
    axes[-1].axis("off")
    axes[-1].text(0.02, 0.95,
                  "Bars = condition mean − panel grand mean\nError bars = ±1 SEM · raw units\n"
                  "0 = no between-condition difference",
                  va="top", fontsize=7, color=L.MUTED, transform=axes[-1].transAxes)
    fig.suptitle(title, x=0.06, ha="left", fontsize=11, fontweight="bold", color=L.INK)
    p = L.savefig(fig, name)
    manifest.setdefault("bars", {})[name] = p.name


# ---- specific event analyses --------------------------------------------
def analysis_oddball(b):
    dev = b.marker_times_lsl("tone_deviant", task="task01")
    std = b.marker_times_lsl("tone_standard", task="task01")
    rb = fig_erp_grid(
        b,
        {"deviant": (L.C[5], dev), "standard": (L.C[0], std)},
        "Task 01 · Auditory Oddball — Deviant vs Standard ERP (−300 to +300 ms)",
        "erp_oddball.png")
    manifest.setdefault("raw_baselines", {})["oddball"] = rb


def analysis_masking(b):
    seen = b.marker_times_lsl("response_seen", task="task03")
    unseen = b.marker_times_lsl("response_unseen", task="task03")
    # face onset is the physical event; response label tags the epoch.
    # Build per-response face onsets by pairing each response to the
    # preceding face_onset within the same trial.
    rb = fig_erp_grid(
        b,
        {"seen": (L.C[3], seen), "unseen": (L.C[5], unseen)},
        "Task 03 · Backward Masking — Seen vs Unseen, locked to response (−300 to +300 ms)",
        "erp_masking.png")
    manifest.setdefault("raw_baselines", {})["masking"] = rb

    # also lock to FACE onset, split by trial outcome (matched-duration contrast)
    face_seen_lsl, face_unseen_lsl = _faces_by_outcome(b)
    rb2 = fig_erp_grid(
        b,
        {"face→seen": (L.C[3], face_seen_lsl), "face→unseen": (L.C[5], face_unseen_lsl)},
        "Task 03 · Backward Masking — Face onset by outcome (matched SOA≈34 ms)",
        "erp_masking_faceonset.png")
    manifest.setdefault("raw_baselines", {})["masking_face"] = rb2


def _faces_by_outcome(b):
    """Pair each main-task face_onset with the next response marker; return
    (face_onsets_lsl_for_seen, ...for_unseen)."""
    m = b.mk[b.mk.task == "task03"].sort_values("t_lsl")
    ev = m["event"].to_numpy()
    tl = m["t_lsl"].to_numpy()
    seen, unseen = [], []
    for i, e in enumerate(ev):
        if e != "face_onset":
            continue
        # find next response_* marker
        for j in range(i + 1, len(ev)):
            if ev[j].startswith("response_"):
                if ev[j] == "response_seen":
                    seen.append(tl[i])
                elif ev[j] == "response_unseen":
                    unseen.append(tl[i])
                break
    return np.asarray(seen), np.asarray(unseen)


def analysis_rgb(b):
    """Per-trial mean over each color window (color onset -> following iti)."""
    m = b.mk[b.mk.task == "task02"].sort_values("t_lsl").reset_index(drop=True)
    ev = m["event"].to_numpy()
    tl = m["t_lsl"].to_numpy()
    cols = {"color_red": [], "color_green": [], "color_blue": []}
    for i, e in enumerate(ev):
        if e in cols:
            # window end = next iti (or next color)
            end = None
            for j in range(i + 1, len(ev)):
                if ev[j] == "iti" or ev[j] in cols:
                    end = tl[j]; break
            if end is not None:
                cols[e].append((tl[i], end))
    groups = {}
    name_color = {"color_red": ("Red", "#a8473b"),
                  "color_green": ("Green", "#4a7a52"),
                  "color_blue": ("Blue", "#3b5da8")}
    per_series = {lbl: {} for (lbl, _) in name_color.values()}
    for key, wins in cols.items():
        lbl, color = name_color[key]
        starts = np.array([w[0] for w in wins])
        ends = np.array([w[1] for w in wins])
        for scol in L.SERIES_COLS:
            per_series[lbl][scol] = L.window_means(b, scol, starts, ends)
    groups = {lbl: (color, per_series[lbl]) for (lbl, color) in name_color.values()}
    fig_bar_grid(b, groups,
                 "Task 02 · RGB Illuminance — Mean Sentiometer amplitude per color (raw units)",
                 "bar_rgb.png", "Mean amplitude")
    # also an ERP locked to color onset (-300..+300) to see transient
    rb = fig_erp_grid(
        b,
        {"Red": ("#a8473b", b.marker_times_lsl("color_red", task="task02")),
         "Green": ("#4a7a52", b.marker_times_lsl("color_green", task="task02")),
         "Blue": ("#3b5da8", b.marker_times_lsl("color_blue", task="task02"))},
        "Task 02 · RGB — Color-onset ERP (−300 to +300 ms)",
        "erp_rgb.png")
    manifest.setdefault("raw_baselines", {})["rgb"] = rb


def analysis_mindstate(b):
    """Game vs break vs meditation: mean amplitude over each block window."""
    def win(nm):
        s = b.mk.loc[b.mk.label == f"task04_{nm}_start", "t_lsl"]
        e = b.mk.loc[b.mk.label == f"task04_{nm}_end", "t_lsl"]
        return (float(s.iloc[0]), float(e.iloc[0])) if len(s) and len(e) else None
    # Per spec (CLAUDE.md / README), the break is a transition, NOT a
    # condition — exclude it from the contrast. Gameplay vs meditation only.
    blocks = {"Gameplay": ("game", L.C[3]), "Meditation": ("meditation", L.C[0])}
    # To get error bars, chunk each block into 5 s bins and take bin means.
    per_series = {lbl: {} for lbl in blocks}
    for lbl, (nm, color) in blocks.items():
        w = win(nm)
        if w is None:
            continue
        s, e = w
        edges = np.arange(s, e, 5.0)
        starts, ends = edges[:-1], edges[1:]
        for scol in L.SERIES_COLS:
            per_series[lbl][scol] = L.window_means(b, scol, starts, ends)
    groups = {lbl: (blocks[lbl][1], per_series[lbl]) for lbl in blocks}
    fig_bar_grid(b, groups,
                 "Task 04 · Mind-State — Mean amplitude by block (5 s bins, raw units)",
                 "bar_mindstate.png", "Mean amplitude")
    # collision ERP
    coll = b.marker_times_lsl("collision", task="task04")
    rb = fig_erp_grid(
        b, {"collision": (L.C[5], coll)},
        "Task 04 · Mind-State — Collision-locked ERP (−300 to +300 ms)",
        "erp_collision.png")
    manifest.setdefault("raw_baselines", {})["collision"] = rb


def analysis_ssvep(b):
    """Bin the ramp by effective-frequency band; mean amplitude per band."""
    spec = L.ssvep_ramp_spec(b)
    rb = b.mk.loc[b.mk.event == "ramp_begin", "t_rel_s"]
    rs_rel = b.mk.loc[b.mk.event == "ramp_start", "t_rel_s"]
    if spec is None or not len(rs_rel):
        return
    rs_rel = float(rs_rel.iloc[0])
    dur = float(spec["durationSeconds"])
    t = b.ts["t_rel_s"].to_numpy()
    inramp = (t >= rs_rel) & (t <= rs_rel + dur)
    hz = L.eff_hz_at(t[inramp], rs_rel, spec)
    bands = [(1, 4, "δ 1–4"), (4, 8, "θ 4–8"), (8, 13, "α 8–13"),
             (13, 30, "β 13–30"), (30, 41, "γ 30–40")]
    per_series = {lbl: {} for (_, _, lbl) in bands}
    t_lsl = b.ts["t_lsl"].to_numpy()
    for scol in L.SERIES_COLS:
        y = b.ts[scol].to_numpy(dtype=np.float64)[inramp]
        for lo, hi, lbl in bands:
            msk = (hz >= lo) & (hz < hi)
            # break into 2 s bins for error bars
            yy = y[msk]
            if len(yy) > 1000:
                nb = max(2, len(yy) // 1000)
                binned = np.array([seg.mean() for seg in np.array_split(yy, nb)])
            else:
                binned = yy if len(yy) else np.array([np.nan])
            per_series[lbl][scol] = binned
    groups = {lbl: (L.C[i % 6], per_series[lbl]) for i, (_, _, lbl) in enumerate(bands)}
    fig_bar_grid(b, groups,
                 "Task 05 · SSVEP — Mean amplitude by effective-frequency band (raw units)",
                 "bar_ssvep.png", "Mean amplitude")


# =========================================================================
def main():
    L.apply_style()
    b = L.load()
    print("loaded:", b.ts.shape, "phases:", [p.key for p in b.phases])

    fig_overview(b)
    print("overview done")
    for p in b.phases:
        if p.key == "task00":
            continue  # questionnaire — no stimulus events, still show timeseries
        fig_block(b, p)
    fig_block(b, next(p for p in b.phases if p.key == "task00"))
    print("blocks done")

    analysis_oddball(b)
    analysis_rgb(b)
    analysis_masking(b)
    analysis_mindstate(b)
    analysis_ssvep(b)
    print("event analyses done")

    (L.FIGDIR / "figs_manifest.json").write_text(json.dumps(manifest, indent=2, default=str))
    print("MANIFEST:", json.dumps(manifest, indent=2, default=str))


if __name__ == "__main__":
    main()
