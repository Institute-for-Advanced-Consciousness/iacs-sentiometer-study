"""Group (multi-subject) Sentiometer statistics.

For every contrast in the single-subject report, recompute each subject's
result with the SAME code (sentiometer_report_stats.welch / anova on
sentiometer_report_lib epochs/windows), then summarise across subjects:

  * per-subject effect size (Cohen's d) for each series
  * across-subject mean d and SD(d)
  * a random-effects one-sample t-test of the per-subject d's against 0
    (the subject is the unit of replication) — flagged as N-small
  * sign agreement across subjects

Writes outputs/_group/sentiometer/report/figs/stats.json.
"""

from __future__ import annotations

import json

import numpy as np
from scipy import stats as sps

import group_report_lib as G
import sentiometer_report_lib as L
import sentiometer_report_figs as F
from sentiometer_report_stats import welch, anova

FIGDIR = G.GROUPROOT / "sentiometer" / "report" / "figs"
SUBJECTS = G.discover_subjects("sentiometer")


def subject_contrasts(b) -> dict:
    """Replicate sentiometer_report_stats.main's per-series contrasts for one
    subject. Returns {series_label: {contrast: result}}."""
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

    m = b.mk[b.mk.task == "task02"].sort_values("t_lsl").reset_index(drop=True)
    ev, tl = m["event"].to_numpy(), m["t_lsl"].to_numpy()
    rgb_wins = {"color_red": [], "color_green": [], "color_blue": []}
    for i, e in enumerate(ev):
        if e in rgb_wins:
            for j in range(i + 1, len(ev)):
                if ev[j] == "iti" or ev[j] in rgb_wins:
                    rgb_wins[e].append((tl[i], tl[j])); break

    dev = b.marker_times_lsl("tone_deviant", task="task01")
    std = b.marker_times_lsl("tone_standard", task="task01")
    face_seen, face_unseen = F._faces_by_outcome(b)

    out = {}
    for col, slabel, *_ in L.SERIES:
        rec = {}
        if game and medi:
            rec["game_vs_med"] = welch(binned_means(col, game), binned_means(col, medi))
        rgb_vals = []
        for key in ("color_red", "color_green", "color_blue"):
            wins = rgb_wins[key]
            st = np.array([w[0] for w in wins]); en = np.array([w[1] for w in wins])
            rgb_vals.append(L.window_means(b, col, st, en))
        rec["rgb_anova"] = anova(rgb_vals)
        rec["rgb_means"] = {c: float(np.nanmean(v)) for c, v in
                            zip(("red", "green", "blue"), rgb_vals)}
        t_ms, Mdev = L.epoch(b, col, dev, 300, 300, baseline_ms=(-300, 0))
        _, Mstd = L.epoch(b, col, std, 300, 300, baseline_ms=(-300, 0))
        w = (t_ms >= 250) & (t_ms <= 350)
        if Mdev.shape[0] and Mstd.shape[0]:
            rec["oddball_p300"] = welch(Mdev[:, w].mean(1), Mstd[:, w].mean(1))
        t2, Mse = L.epoch(b, col, face_seen, 300, 300, baseline_ms=(-300, 0))
        _, Mun = L.epoch(b, col, face_unseen, 300, 300, baseline_ms=(-300, 0))
        w2 = (t2 >= 0) & (t2 <= 300)
        if Mse.shape[0] and Mun.shape[0]:
            rec["masking_seen_unseen"] = welch(Mse[:, w2].mean(1), Mun[:, w2].mean(1))
        out[slabel] = rec
    return out


def random_effects(d_list: list[float]) -> dict:
    """One-sample t-test of per-subject d's against 0 (random effects)."""
    d = np.asarray([x for x in d_list if x is not None and not np.isnan(x)], float)
    n = d.size
    if n < 2:
        return dict(mean_d=float(d.mean()) if n else None, sd_d=None,
                    t=None, p=None, n=int(n), same_sign=(n == 1))
    mean_d = float(d.mean()); sd_d = float(d.std(ddof=1))
    if sd_d == 0:
        t = np.inf if mean_d != 0 else 0.0
        p = 0.0 if mean_d != 0 else 1.0
    else:
        t = mean_d / (sd_d / np.sqrt(n))
        p = float(2 * sps.t.sf(abs(t), n - 1))
    same_sign = bool(np.all(d > 0) or np.all(d < 0))
    return dict(mean_d=mean_d, sd_d=sd_d, t=float(t), p=float(p), n=int(n),
                same_sign=same_sign)


def main():
    series_labels = [s[1] for s in L.SERIES]
    per_subject = {}
    meta_subjects = {}
    dq = {pd: {} for pd in ["PD1", "PD2", "PD3", "PD4", "PD5"]}
    for s in SUBJECTS:
        L.BUNDLE = G.OUTROOT / s / "preprocessed"
        b = L.load()
        per_subject[s] = subject_contrasts(b)
        meta_subjects[s] = {
            "duration_hms": b.meta["duration_hms"],
            "n_samples": int(b.ts.shape[0]),
            "fs_hz": round(b.fs, 3),
            "pc1_evr": b.meta["pca"]["pc1_variance_explained"],
        }
        for pd in dq:
            x = b.ts[pd].to_numpy()
            dq[pd][s] = dict(min=float(x.min()), max=float(x.max()),
                             mean=float(x.mean()), std=float(x.std()),
                             frac_at_4095=float((x >= 4095).mean()))

    # ---- aggregate per contrast / series ---------------------------------
    contrasts = ["game_vs_med", "oddball_p300", "masking_seen_unseen"]
    agg = {ct: {} for ct in contrasts}
    for ct in contrasts:
        for sl in series_labels:
            d_list, per = [], {}
            for s in SUBJECTS:
                r = per_subject[s][sl].get(ct)
                per[s] = r
                if r and r.get("d") is not None:
                    d_list.append(r["d"])
            agg[ct][sl] = {"per_subject": per, **random_effects(d_list),
                           "d_list": d_list}

    # RGB ANOVA per subject (all should be n.s.)
    rgb = {}
    for sl in series_labels:
        rgb[sl] = {"per_subject": {s: per_subject[s][sl]["rgb_anova"] for s in SUBJECTS},
                   "means": {s: per_subject[s][sl]["rgb_means"] for s in SUBJECTS}}

    out = {
        "subjects": SUBJECTS,
        "series": series_labels,
        "contrasts": agg,
        "rgb": rgb,
        "data_quality": dq,
        "meta": {"subjects": meta_subjects, "n_subjects": len(SUBJECTS)},
    }
    FIGDIR.mkdir(parents=True, exist_ok=True)
    (FIGDIR / "stats.json").write_text(json.dumps(out, indent=2))

    # console headline
    print(f"=== GROUP Sentiometer · {len(SUBJECTS)} subjects: {', '.join(SUBJECTS)} ===")
    for ct in contrasts:
        print(f"\n{ct}:")
        for sl in ("Mean", "PC1"):
            a = agg[ct][sl]
            ds = ", ".join(f"{x:+.2f}" for x in a["d_list"])
            print(f"  {sl}: d per subj=[{ds}] mean_d={a['mean_d']:+.2f} "
                  f"p={a['p'] if a['p'] is None else round(a['p'],3)} same_sign={a['same_sign']}")


if __name__ == "__main__":
    main()
