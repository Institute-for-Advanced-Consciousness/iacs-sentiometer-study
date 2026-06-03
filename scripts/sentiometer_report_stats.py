"""Compute the summary statistics + contrast tables for the report.

Writes outputs/P013_S01/report/figs/stats.json — consumed by the HTML
builder so no number in the report is hand-transcribed.
"""

from __future__ import annotations

import json

import numpy as np
from scipy import stats as sps

import sentiometer_report_lib as L

FIGDIR = L.FIGDIR


def welch(a, b):
    a = a[~np.isnan(a)]; b = b[~np.isnan(b)]
    if len(a) < 2 or len(b) < 2:
        return dict(mean_a=float(np.mean(a)) if len(a) else None,
                    mean_b=float(np.mean(b)) if len(b) else None,
                    t=None, p=None, d=None, n_a=len(a), n_b=len(b))
    t, p = sps.ttest_ind(a, b, equal_var=False)
    # Cohen's d (pooled)
    sp = np.sqrt(((len(a) - 1) * a.var(ddof=1) + (len(b) - 1) * b.var(ddof=1)) /
                 (len(a) + len(b) - 2))
    d = (a.mean() - b.mean()) / sp if sp else 0.0
    return dict(mean_a=float(a.mean()), mean_b=float(b.mean()),
                t=float(t), p=float(p), d=float(d), n_a=len(a), n_b=len(b))


def anova(groups):
    groups = [g[~np.isnan(g)] for g in groups]
    groups = [g for g in groups if len(g) > 1]
    if len(groups) < 2:
        return dict(F=None, p=None)
    F, p = sps.f_oneway(*groups)
    return dict(F=float(F), p=float(p))


def main():
    b = L.load()
    out = {"series": [s[1] for s in L.SERIES], "by_series": {}}

    # ---- block-window means per series (for state contrasts) -------------
    def win_lsl(label):
        s = b.mk.loc[b.mk.label == f"{label}_start", "t_lsl"]
        e = b.mk.loc[b.mk.label == f"{label}_end", "t_lsl"]
        return (float(s.iloc[0]), float(e.iloc[0])) if len(s) and len(e) else None

    def binned_means(col, win, bin_s=5.0):
        s, e = win
        edges = np.arange(s, e, bin_s)
        return L.window_means(b, col, edges[:-1], edges[1:])

    game = win_lsl("task04_game")
    medi = win_lsl("task04_meditation")

    # ---- RGB per-color window means --------------------------------------
    m = b.mk[b.mk.task == "task02"].sort_values("t_lsl").reset_index(drop=True)
    ev, tl = m["event"].to_numpy(), m["t_lsl"].to_numpy()
    rgb_wins = {"color_red": [], "color_green": [], "color_blue": []}
    for i, e in enumerate(ev):
        if e in rgb_wins:
            for j in range(i + 1, len(ev)):
                if ev[j] == "iti" or ev[j] in rgb_wins:
                    rgb_wins[e].append((tl[i], tl[j])); break

    # ---- oddball ERP peak diff (250-350 ms window, mean over series) -----
    dev = b.marker_times_lsl("tone_deviant", task="task01")
    std = b.marker_times_lsl("tone_standard", task="task01")
    # Matched-duration contrast: lock to FACE onset (face-present trials only,
    # SOA≈34 ms identical), split by trial outcome. This excludes catch-trial
    # "unseen" correct rejections, which have no face at all.
    import sentiometer_report_figs as F
    face_seen, face_unseen = F._faces_by_outcome(b)

    for col, slabel, *_ in L.SERIES:
        rec = {}
        # state: gameplay vs meditation (5 s bins)
        if game and medi:
            g = binned_means(col, game); md = binned_means(col, medi)
            rec["game_vs_med"] = welch(g, md)
        # RGB ANOVA across three colors (per-trial window means)
        rgb_vals = []
        for key in ("color_red", "color_green", "color_blue"):
            wins = rgb_wins[key]
            st = np.array([w[0] for w in wins]); en = np.array([w[1] for w in wins])
            rgb_vals.append(L.window_means(b, col, st, en))
        rec["rgb_anova"] = anova(rgb_vals)
        rec["rgb_means"] = {c: float(np.nanmean(v)) for c, v in
                            zip(("red", "green", "blue"), rgb_vals)}
        # oddball P300-window (250-350 ms) deviant vs standard, baseline-corr
        t_ms, Mdev = L.epoch(b, col, dev, 300, 300, baseline_ms=(-300, 0))
        _, Mstd = L.epoch(b, col, std, 300, 300, baseline_ms=(-300, 0))
        w = (t_ms >= 250) & (t_ms <= 350)
        if Mdev.shape[0] and Mstd.shape[0]:
            rec["oddball_p300"] = welch(Mdev[:, w].mean(1), Mstd[:, w].mean(1))
        # masking seen vs unseen, face-onset locked, 0-300 ms post-onset mean
        # (matched SOA, face-present only — the conscious-access contrast)
        t_ms2, Mse = L.epoch(b, col, face_seen, 300, 300, baseline_ms=(-300, 0))
        _, Mun = L.epoch(b, col, face_unseen, 300, 300, baseline_ms=(-300, 0))
        w2 = (t_ms2 >= 0) & (t_ms2 <= 300)
        if Mse.shape[0] and Mun.shape[0]:
            rec["masking_seen_unseen"] = welch(Mse[:, w2].mean(1), Mun[:, w2].mean(1))
        out["by_series"][slabel] = rec

    # ---- global descriptive facts ----------------------------------------
    out["meta"] = {
        "n_samples": int(b.ts.shape[0]),
        "fs_hz": round(b.fs, 3),
        "duration_hms": b.meta["duration_hms"],
        "pc1_evr": b.meta["pca"]["pc1_variance_explained"],
        "pc1_loadings": b.meta["pca"]["pc1_loadings"],
        "phases": [{"key": p.key, "title": p.title, "t0": p.t0, "t1": p.t1,
                    "dur": p.t1 - p.t0} for p in b.phases],
    }
    # data-quality: per-PD range & rail proximity
    dq = {}
    for col in ["PD1", "PD2", "PD3", "PD4", "PD5"]:
        x = b.ts[col].to_numpy()
        dq[col] = dict(min=float(x.min()), max=float(x.max()),
                       mean=float(x.mean()), std=float(x.std()),
                       frac_at_4095=float((x >= 4095).mean()))
    out["data_quality"] = dq

    (FIGDIR / "stats.json").write_text(json.dumps(out, indent=2))
    # console summary of the headline contrasts
    print("STATE (gameplay vs meditation), Mean & PC1:")
    for s in ("Mean", "PC1"):
        r = out["by_series"][s]["game_vs_med"]
        print(f"  {s}: game={r['mean_a']:.2f} med={r['mean_b']:.2f} "
              f"t={r['t']:.1f} p={r['p']:.2e} d={r['d']:.2f}")
    print("RGB ANOVA p (should be ~ns), Mean & PC1:")
    for s in ("Mean", "PC1"):
        print(f"  {s}: p={out['by_series'][s]['rgb_anova']['p']:.3f}")
    print("ODDBALL P300 (deviant vs standard 250-350ms), Mean & PC1:")
    for s in ("Mean", "PC1"):
        r = out["by_series"][s]["oddball_p300"]
        print(f"  {s}: t={r['t']:.2f} p={r['p']:.3f} d={r['d']:.3f}")
    print("PD3 frac at rail 4095:", round(out['data_quality']['PD3']['frac_at_4095'], 4))


if __name__ == "__main__":
    main()
