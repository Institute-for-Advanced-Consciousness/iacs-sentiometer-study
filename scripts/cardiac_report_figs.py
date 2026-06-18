"""Generate every figure for the P013 cardiac/HRV phase-resolved report.

Mirrors sentiometer_report_figs.py:
  1. Full-session two-band overview (6 metrics z-scored, task suite + nap).
  2. Per-phase zoom pages (6 metrics, raw units, event markers overlaid).
  3. Event-grouped analysis: condition bar charts (deviation from grand mean)
     + event-locked instantaneous-HR responses.

Run:
  uv run --with pandas --with pyarrow --with matplotlib --with scipy \
      python scripts/cardiac_report_figs.py
"""

from __future__ import annotations

import json

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
import numpy as np

import cardiac_report_lib as L

LAND = (11.0, 7.6)
manifest: dict = {}


# =========================================================================
# 1. OVERVIEW (two-band)
# =========================================================================
def fig_overview(b):
    task_end = next(p.t1 for p in b.phases if p.key == "task05")
    nap = next(p for p in b.phases if p.key == "nap")
    t = b.ts["t_rel_s"].to_numpy()
    task_m = t <= task_end
    nap_m = t > task_end

    fig, (axt, axn) = plt.subplots(2, 1, figsize=LAND, height_ratios=[1.0, 0.62])
    fig.subplots_adjust(left=0.06, right=0.985, top=0.90, bottom=0.07, hspace=0.42)
    offsets = {c: i * 3.2 for i, c in enumerate(L.METRIC_COLS)}

    def draw(ax, mask):
        tt = t[mask]
        for c in L.METRIC_COLS:
            lab, unit, col = L.METRIC_META[c]
            y = b.ts[c + "_z"].to_numpy()[mask]
            ax.plot(tt / 60.0, y + offsets[c], color=col, lw=1.1, alpha=0.9,
                    solid_capstyle="round")
        ax.set_yticks([offsets[c] for c in L.METRIC_COLS])
        ax.set_yticklabels([L.METRIC_META[c][0] for c in L.METRIC_COLS])
        L.finish_axes(ax); ax.margins(x=0.005)

    draw(axt, task_m)
    axt.set_xlim(t[task_m].min() / 60.0, task_end / 60.0)
    axt.set_title("Task suite — 0 to {:.0f} min (each metric z-scored, vertically offset)".format(task_end / 60),
                  loc="left", pad=18)
    axt.set_xlabel("Time from session start (min)")
    ymax = max(offsets.values()) + 2.5
    for p in b.phases:
        if p.key == "nap":
            continue
        axt.axvspan(p.t0 / 60, p.t1 / 60, color=p.color, alpha=0.05, lw=0)
        axt.axvline(p.t0 / 60, color=p.color, lw=0.6, alpha=0.45, ls=(0, (4, 3)))
        axt.text(p.t0 / 60 + 0.15, ymax, p.title.split(" (")[0], va="bottom",
                 ha="left", fontsize=6.0, color=L.INK, fontweight="bold")
    axt.set_ylim(-2.5, ymax + 2)

    draw(axn, nap_m)
    axn.set_xlim(task_end / 60.0, nap.t1 / 60.0)
    axn.set_title("Post-session nap — {:.0f} to {:.0f} min ({:.0f} min continuous)".format(
        task_end / 60, nap.t1 / 60, (nap.t1 - nap.t0) / 60), loc="left", pad=8)
    axn.set_xlabel("Time from session start (min)")
    axn.axvspan(nap.t0 / 60, nap.t1 / 60, color=L.INK, alpha=0.04, lw=0)
    axn.set_ylim(-2.5, ymax + 2)
    manifest["overview"] = L.savefig(fig, "overview.png").name


# =========================================================================
# 2. PER-PHASE ZOOM (raw units, 6 stacked sub-axes so units are honest)
# =========================================================================
BLOCK_EVENTS = {
    "task01": [("tone_deviant", "deviant", L.C[5], "-")],
    "task02": [("color_red", "R", "#a8473b", "-"), ("color_green", "G", "#4a7a52", "-"),
               ("color_blue", "B", "#3b5da8", "-")],
    "task03": [("face_onset", "face", L.C[0], "-")],
    "task04": [("collision", "collision", L.C[5], "-"), ("speed_increase", "speed+", L.C[4], "--")],
    "task05": [],
}


def fig_block(b, phase):
    t = b.ts["t_rel_s"].to_numpy()
    m = (t >= phase.t0) & (t <= phase.t1)
    fig, axes = plt.subplots(len(L.METRIC_COLS), 1, figsize=LAND, sharex=True)
    fig.subplots_adjust(left=0.08, right=0.985, top=0.91, bottom=0.07, hspace=0.18)
    for ax, c in zip(axes, L.METRIC_COLS):
        lab, unit, col = L.METRIC_META[c]
        ax.plot(t[m], b.ts[c].to_numpy()[m], color=col, lw=1.2)
        ax.set_ylabel(f"{lab}\n({unit})", fontsize=6.5)
        L.finish_axes(ax); ax.margins(x=0.01)
        # event markers
        for ev, elbl, ecol, els in BLOCK_EVENTS.get(phase.key, []):
            times = b.marker_times(ev, task=phase.key)
            times = times[(times >= phase.t0) & (times <= phase.t1)]
            for x in times:
                ax.axvline(x, color=ecol, lw=0.3, ls=els, alpha=0.25)
    # sub-block shading for task04 on the top axis
    if phase.key == "task04":
        for nm, lbl, cc in [("game", "Gameplay", L.C[3]), ("meditation", "Meditation", L.C[0])]:
            s = b.mk.loc[b.mk.label == f"task04_{nm}_start", "t_rel_s"]
            e = b.mk.loc[b.mk.label == f"task04_{nm}_end", "t_rel_s"]
            if len(s) and len(e):
                for ax in axes:
                    ax.axvspan(s.iloc[0], e.iloc[0], color=cc, alpha=0.06, lw=0)
                axes[0].text((s.iloc[0]+e.iloc[0])/2, axes[0].get_ylim()[1], lbl,
                             ha="center", va="bottom", fontsize=7, color=L.INK, fontweight="bold")
    axes[-1].set_xlabel("Time from session start (s)")
    axes[0].set_title(f"{phase.title} — {phase.t0:.0f}–{phase.t1:.0f} s "
                      f"({phase.t1 - phase.t0:.0f} s) · cardiac metrics, raw units",
                      loc="left", pad=18)
    manifest.setdefault("blocks", {})[phase.key] = L.savefig(fig, f"block_{phase.key}.png").name


# =========================================================================
# 3. EVENT-GROUPED ANALYSIS
# =========================================================================
def fig_bar_grid(b, groups, title, name):
    """6-panel bar grid (one per metric), deviation from panel grand mean."""
    fig, axes = plt.subplots(2, 3, figsize=LAND)
    fig.subplots_adjust(left=0.07, right=0.985, top=0.86, bottom=0.10, hspace=0.42, wspace=0.30)
    axes = axes.ravel()
    labels = list(groups.keys())
    colors = [groups[k][0] for k in labels]
    for ax_i, c in enumerate(L.METRIC_COLS):
        ax = axes[ax_i]
        lab, unit, _ = L.METRIC_META[c]
        means, sems = [], []
        for k in labels:
            vals = groups[k][1][c]; vals = vals[~np.isnan(vals)]
            means.append(vals.mean() if len(vals) else np.nan)
            sems.append(vals.std() / np.sqrt(len(vals)) if len(vals) else 0.0)
        means = np.asarray(means, float)
        grand = np.nanmean(means)
        dev = means - grand
        x = np.arange(len(labels))
        ax.bar(x, dev, yerr=sems, color=colors, alpha=0.85, edgecolor=L.INK,
               linewidth=0.4, capsize=2, error_kw={"elinewidth": 0.6})
        ax.axhline(0, color=L.RULE, lw=0.6)
        span = np.nanmax(np.abs(dev) + np.asarray(sems)) if len(dev) else 1.0
        ax.set_ylim(-max(span*1.35, 1e-9), max(span*1.35, 1e-9))
        ax.set_xticks(x); ax.set_xticklabels(labels, fontsize=6.5)
        gm = f"{grand:.1f}" if abs(grand) >= 1 else f"{grand:.3f}"
        ax.set_title(f"{lab}  ({unit}, grand {gm})", loc="left", fontsize=8.0, pad=3)
        if ax_i % 3 == 0:
            ax.set_ylabel("Δ from grand mean")
        L.finish_axes(ax)
    fig.suptitle(title, x=0.07, ha="left", fontsize=11, fontweight="bold", color=L.INK)
    fig.text(0.985, 0.02, "Δ from panel grand mean · ±1 SEM · raw units", ha="right",
             fontsize=7, color=L.MUTED)
    manifest.setdefault("bars", {})[name] = L.savefig(fig, name).name


def fig_inst_hr_erp(b, onsets, title, name, pre=10, post=20, baseline=(-10, -2)):
    """Event-locked instantaneous-HR response. onsets: {label:(color, lsl)}."""
    fig, ax = plt.subplots(figsize=(8.0, 5.0))
    fig.subplots_adjust(left=0.10, right=0.97, top=0.90, bottom=0.12)
    for clabel, (color, ons) in onsets.items():
        t_s, M = L.epoch_inst_hr(b, ons, pre, post, baseline_s=baseline)
        if M.shape[0] == 0:
            continue
        mu = M.mean(0); sem = M.std(0) / max(np.sqrt(M.shape[0]), 1)
        ax.fill_between(t_s, mu - sem, mu + sem, color=color, alpha=0.15, lw=0)
        ax.plot(t_s, mu, color=color, lw=1.6, label=f"{clabel} (n={M.shape[0]})")
    ax.axvline(0, color=L.MUTED, lw=0.6, ls="--", alpha=0.7)
    ax.axhline(0, color=L.RULE, lw=0.5)
    ax.set_xlabel("Time from event (s)")
    ax.set_ylabel("Δ instantaneous HR (bpm, baseline-corr.)")
    ax.set_title(title, loc="left", fontsize=10, pad=8)
    L.finish_axes(ax); ax.legend(loc="upper right", fontsize=7.5)
    manifest.setdefault("erp", {})[name] = L.savefig(fig, name).name


# ---- specific analyses ---------------------------------------------------
def _block_window(b, label_prefix):
    s = b.mk.loc[b.mk.label == f"{label_prefix}_start", "t_lsl"]
    e = b.mk.loc[b.mk.label == f"{label_prefix}_end", "t_lsl"]
    if not (len(s) and len(e)):
        return None
    # Beat-based, non-overlapping 30 s sub-windows: no pre-block contamination,
    # independent sub-windows so SEM is valid.
    return L.block_metrics(b, float(s.iloc[0]), float(e.iloc[0]), sub_s=30.0)


def analysis_mindstate(b):
    game = _block_window(b, "task04_game")
    medi = _block_window(b, "task04_meditation")
    if game and medi:
        groups = {"Gameplay": (L.C[3], game), "Meditation": (L.C[0], medi)}
        fig_bar_grid(b, groups, "Task 04 · Mind-State — cardiac metrics: gameplay vs meditation", "bar_mindstate.png")
    coll = b.marker_times_lsl("collision", task="task04")
    if len(coll):
        fig_inst_hr_erp(b, {"collision": (L.C[5], coll)},
                        "Task 04 · Collision-locked instantaneous HR (−10 to +20 s)",
                        "erp_collision_hr.png")


def analysis_ssvep(b):
    """Cardiac metrics by SSVEP effective-frequency band. Mirrors the
    Sentiometer SSVEP bar. Because the ramp is monotonic 40->1 Hz over 300 s,
    each EEG band maps to ONE contiguous time chunk of the ramp (delta 23 s …
    beta 131 s). We compute beat-based metrics over each band's time window
    in non-overlapping 20 s sub-windows. LF/HF needs ~120 s, so the short
    high-frequency bands legitimately return few/no spectral estimates — that
    is shown honestly (NaN bars), not hidden."""
    bands = [(1, 4, "δ 1–4"), (4, 8, "θ 4–8"), (8, 13, "α 8–13"),
             (13, 30, "β 13–30"), (30, 40, "γ 30–40")]
    groups = {}
    for i, (lo, hi, lbl) in enumerate(bands):
        win = L.ramp_band_window_lsl(b, lo, hi)
        if win is None:
            continue
        groups[lbl] = (L.C[i % 6], L.block_metrics(b, win[0], win[1], sub_s=20.0))
    if groups:
        fig_bar_grid(b, groups,
                     "Task 05 · SSVEP — cardiac metrics by effective-frequency band",
                     "bar_ssvep.png")


def analysis_masking(b):
    """Seen vs unseen instantaneous-HR, locked to FACE onset (matched SOA,
    face-present only). Mirrors the Sentiometer masking ERP."""
    seen, unseen = _faces_by_outcome(b)
    onsets = {}
    if len(seen):
        onsets["seen"] = (L.C[3], seen)
    if len(unseen):
        onsets["unseen"] = (L.C[5], unseen)
    if onsets:
        fig_inst_hr_erp(b, onsets,
                        "Task 03 · Backward Masking — face-onset instantaneous HR (seen vs unseen, matched SOA)",
                        "erp_masking_hr.png", pre=5, post=10, baseline=(-5, -1))


def _faces_by_outcome(b):
    """Pair each main-task face_onset with the next response marker; return
    (face_onsets_lsl_for_seen, ...for_unseen). Mirrors the Sentiometer logic."""
    m = b.mk[b.mk.task == "task03"].sort_values("t_lsl")
    ev = m["event"].to_numpy(); tl = m["t_lsl"].to_numpy()
    seen, unseen = [], []
    for i, e in enumerate(ev):
        if e != "face_onset":
            continue
        for j in range(i + 1, len(ev)):
            if ev[j].startswith("response_"):
                if ev[j] == "response_seen":
                    seen.append(tl[i])
                elif ev[j] == "response_unseen":
                    unseen.append(tl[i])
                break
    return np.asarray(seen), np.asarray(unseen)


def analysis_rgb(b):
    """For one-to-one figure parity with the Sentiometer report. NOTE: this is
    exploratory and timescale-limited — RGB colors interleave at ~1.6 s, far
    faster than HR/HRV can resolve, so the three 'per-color' spans cover nearly
    the same minutes of recording. The bars are near-identical by construction;
    we show them only for completeness and label the caveat loudly in the HTML."""
    name_color = {"color_red": ("Red", "#a8473b"), "color_green": ("Green", "#4a7a52"),
                  "color_blue": ("Blue", "#3b5da8")}
    groups = {}
    for key, (lbl, col) in name_color.items():
        ons = b.marker_times_lsl(key, task="task02")
        if len(ons) >= 2:
            groups[lbl] = (col, L.block_metrics(b, float(ons.min()), float(ons.max()), sub_s=20.0))
    if groups:
        fig_bar_grid(b, groups,
                     "Task 02 · RGB — cardiac metrics per color span (EXPLORATORY: see caveat)",
                     "bar_rgb.png")
    # color-onset instantaneous HR (transient), like erp_rgb on the optical side
    onsets = {}
    for key, (lbl, col) in name_color.items():
        ons = b.marker_times_lsl(key, task="task02")
        if len(ons):
            onsets[lbl] = (col, ons)
    if onsets:
        fig_inst_hr_erp(b, onsets,
                        "Task 02 · RGB — color-onset instantaneous HR (−3 to +5 s, exploratory)",
                        "erp_rgb_hr.png", pre=3, post=5, baseline=(-3, -1))


def analysis_phases(b):
    """Per-phase comparison of all metrics across the whole session."""
    groups = {}
    palette = [L.C[2], L.C[0], L.C[3], L.C[4], L.C[5], L.C[1], L.INK]
    order = [("task00", "Quest"), ("task01", "Oddball"), ("task02", "RGB"),
             ("task03", "Mask"), ("task04", "Mind"), ("task05", "SSVEP"), ("nap", "Nap")]
    for i, (key, short) in enumerate(order):
        sub = b.ts[b.ts.task == key]
        per = {c: sub[c].to_numpy(dtype=float) for c in L.METRIC_COLS}
        groups[short] = (palette[i % len(palette)], per)
    fig_bar_grid(b, groups, "All phases · cardiac metrics by session phase (incl. nap)", "bar_phases.png")


def analysis_oddball(b):
    dev = b.marker_times_lsl("tone_deviant", task="task01")
    std = b.marker_times_lsl("tone_standard", task="task01")
    if len(dev) and len(std):
        fig_inst_hr_erp(b, {"deviant": (L.C[5], dev), "standard": (L.C[0], std)},
                        "Task 01 · Tone-locked instantaneous HR (deviant vs standard, −5 to +10 s)",
                        "erp_oddball_hr.png", pre=5, post=10, baseline=(-5, -1))


def main():
    L.apply_style()
    b = L.load()
    print("loaded ts", b.ts.shape, "beats", b.beats.shape)
    fig_overview(b); print("overview done")
    for p in b.phases:
        fig_block(b, p)
    print("blocks done")
    analysis_phases(b)
    analysis_mindstate(b)
    analysis_oddball(b)
    analysis_masking(b)
    analysis_ssvep(b)
    analysis_rgb(b)
    print("event analyses done")
    import cardiac_xcorr
    cardiac_xcorr.run(b, manifest)
    print("xcorr done")
    (L.FIGDIR / "figs_manifest.json").write_text(json.dumps(manifest, indent=2))
    print("MANIFEST:", json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
