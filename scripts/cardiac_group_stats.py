"""Group (multi-subject) cardiac statistics.

Recomputes each subject's gameplay-vs-meditation contrast with the SAME
beat-based code (cardiac_report_lib.block_metrics + cardiac_report_stats.welch),
plus per-phase windowed means, then summarises across subjects (mean d, SD,
random-effects t-test, sign agreement; pooled phase means).

Writes outputs/_group/cardiac/report/figs/stats.json.
"""

from __future__ import annotations

import json

import numpy as np
from scipy import stats as sps

import group_report_lib as G
import cardiac_report_lib as CL
from cardiac_report_stats import welch, block

FIGDIR = G.GROUPROOT / "cardiac" / "report" / "figs"
SUBJECTS = G.discover_subjects("cardiac")
METRICS = CL.METRIC_COLS
PHASES = ["task00", "task01", "task02", "task03", "task04", "task05", "nap"]


def random_effects(d_list):
    d = np.asarray([x for x in d_list if x is not None and not np.isnan(x)], float)
    n = d.size
    if n < 2:
        return dict(mean_d=float(d.mean()) if n else None, sd_d=None, t=None,
                    p=None, n=int(n), same_sign=(n == 1))
    mean_d = float(d.mean()); sd_d = float(d.std(ddof=1))
    if sd_d == 0:
        t, p = (np.inf, 0.0) if mean_d != 0 else (0.0, 1.0)
    else:
        t = mean_d / (sd_d / np.sqrt(n)); p = float(2 * sps.t.sf(abs(t), n - 1))
    return dict(mean_d=mean_d, sd_d=sd_d, t=float(t), p=float(p), n=int(n),
                same_sign=bool(np.all(d > 0) or np.all(d < 0)))


def main():
    per_subject = {}
    phase_vals = {c: {pk: {} for pk in PHASES} for c in METRICS}
    meta_sub = {}
    for s in SUBJECTS:
        CL.BUNDLE = G.OUTROOT / s / "cardiac"
        b = CL.load()
        game = block(b, "task04_game"); medi = block(b, "task04_meditation")
        rec = {}
        for c in METRICS:
            if game and medi:
                rec[c] = welch(game[c], medi[c])
        per_subject[s] = rec
        for c in METRICS:
            for pk in PHASES:
                sub = b.ts[b.ts.task == pk][c].to_numpy(dtype=float)
                sub = sub[~np.isnan(sub)]
                phase_vals[c][pk][s] = sub
        meta_sub[s] = {
            "median_hr_bpm": b.meta["median_hr_bpm"], "n_beats": b.meta["n_beats"],
            "artifact_fraction": b.meta["artifact_fraction"],
            "recording_span_min": b.meta["recording_span_min"],
            "window_s": b.meta["window_s"], "step_s": b.meta["step_s"],
        }

    # aggregate game_vs_med per metric
    agg = {}
    for c in METRICS:
        d_list, per = [], {}
        for s in SUBJECTS:
            r = per_subject[s].get(c); per[s] = r
            if r and r.get("d") is not None:
                d_list.append(r["d"])
        agg[c] = {"per_subject": per, **random_effects(d_list), "d_list": d_list}

    # pooled phase means (per metric per phase): grand mean of subject means + pooled SD
    phase_summary = {c: {} for c in METRICS}
    for c in METRICS:
        for pk in PHASES:
            arrs = [phase_vals[c][pk][s] for s in SUBJECTS]
            ps = G.pooled_stats(arrs)
            per = {s: (float(np.nanmean(phase_vals[c][pk][s]))
                       if phase_vals[c][pk][s].size else None) for s in SUBJECTS}
            phase_summary[c][pk] = {"group_mean": ps["grand_mean"],
                                    "pooled_sd": ps["pooled_sd"],
                                    "n_subj": ps["n_subj"], "per_subject": per}

    out = {
        "subjects": SUBJECTS, "metrics": METRICS,
        "metric_labels": {c: CL.METRIC_META[c][0] for c in METRICS},
        "metric_units": {c: CL.METRIC_META[c][1] for c in METRICS},
        "phases": PHASES,
        "game_vs_med": agg,
        "phase_means": phase_summary,
        "meta": {"subjects": meta_sub, "n_subjects": len(SUBJECTS)},
    }
    FIGDIR.mkdir(parents=True, exist_ok=True)
    (FIGDIR / "stats.json").write_text(json.dumps(out, indent=2))

    print(f"=== GROUP cardiac · {len(SUBJECTS)} subjects ===")
    for c in ("HR", "SDNN", "LF_HF", "RespRate"):
        a = agg[c]
        ds = ", ".join(f"{x:+.2f}" for x in a["d_list"])
        print(f"  game_vs_med {c}: d=[{ds}] mean_d={a['mean_d']:+.2f} "
              f"p={None if a['p'] is None else round(a['p'],3)} same_sign={a['same_sign']}")
    for c in ("HR", "SDNN"):
        nap = phase_summary[c]["nap"]["group_mean"]; q = phase_summary[c]["task00"]["group_mean"]
        print(f"  {c}: quest={q:.1f} nap={nap:.1f}")


if __name__ == "__main__":
    main()
