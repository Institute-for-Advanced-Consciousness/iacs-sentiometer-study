"""Compute summary statistics + contrast tables for the cardiac report.

Writes outputs/<SUBJECT>/cardiac/report/figs/stats.json — consumed by the
HTML builder so no number is hand-transcribed.
"""

from __future__ import annotations

import json

import numpy as np
from scipy import stats as sps

import cardiac_report_lib as L


def welch(a, b):
    a = a[~np.isnan(a)]; b = b[~np.isnan(b)]
    if len(a) < 2 or len(b) < 2:
        return dict(mean_a=float(np.mean(a)) if len(a) else None,
                    mean_b=float(np.mean(b)) if len(b) else None,
                    t=None, p=None, d=None, n_a=int(len(a)), n_b=int(len(b)))
    t, p = sps.ttest_ind(a, b, equal_var=False)
    sp = np.sqrt(((len(a)-1)*a.var(ddof=1) + (len(b)-1)*b.var(ddof=1)) / (len(a)+len(b)-2))
    d = (a.mean()-b.mean())/sp if sp else 0.0
    return dict(mean_a=float(a.mean()), mean_b=float(b.mean()), t=float(t), p=float(p),
                d=float(d), n_a=int(len(a)), n_b=int(len(b)))


def anova(groups):
    groups = [g[~np.isnan(g)] for g in groups]
    groups = [g for g in groups if len(g) > 1]
    if len(groups) < 2:
        return dict(F=None, p=None)
    F, p = sps.f_oneway(*groups)
    return dict(F=float(F), p=float(p))


def block(b, prefix, sub_s=30.0):
    s = b.mk.loc[b.mk.label == f"{prefix}_start", "t_lsl"]
    e = b.mk.loc[b.mk.label == f"{prefix}_end", "t_lsl"]
    if not (len(s) and len(e)):
        return None
    # beat-based, non-overlapping sub-windows (no contamination, valid SEM)
    return L.block_metrics(b, float(s.iloc[0]), float(e.iloc[0]), sub_s=sub_s)


def main():
    b = L.load()
    out = {"metrics": L.METRIC_COLS, "by_metric": {}}

    game = block(b, "task04_game")
    medi = block(b, "task04_meditation")

    # NOTE: no per-color RGB cardiac contrast — colors interleave at ~1.6 s,
    # far faster than HRV can resolve. The RGB null test applies to the fast
    # optical signal, not the slow cardiac system. (Stated in the report.)

    # per-phase windowed values
    phase_keys = ["task00", "task01", "task02", "task03", "task04", "task05", "nap"]
    for c in L.METRIC_COLS:
        rec = {}
        if game and medi:
            rec["game_vs_med"] = welch(game[c], medi[c])
        rec["phase_means"] = {}
        for pk in phase_keys:
            sub = b.ts[b.ts.task == pk][c].to_numpy(dtype=float)
            sub = sub[~np.isnan(sub)]
            rec["phase_means"][pk] = dict(mean=float(sub.mean()) if len(sub) else None,
                                          sem=float(sub.std()/np.sqrt(len(sub))) if len(sub) else None,
                                          n=int(len(sub)))
        out["by_metric"][c] = rec

    valid = b.ts.dropna(subset=["HR"])
    out["meta"] = {
        "subject": b.meta["subject"], "fs_hz": round(b.meta["fs_hz"], 2),
        "n_beats": b.meta["n_beats"], "artifact_fraction": b.meta["artifact_fraction"],
        "median_hr_bpm": b.meta["median_hr_bpm"], "window_s": b.meta["window_s"],
        "step_s": b.meta["step_s"], "window_align": b.meta["window_align"],
        "lf_band_hz": b.meta["lf_band_hz"], "hf_band_hz": b.meta["hf_band_hz"],
        "n_windows": b.meta["n_windows"], "n_windows_valid": int(len(valid)),
        "recording_span_min": b.meta["recording_span_min"],
        "phases": [{"key": p.key, "title": p.title, "t0": p.t0, "t1": p.t1,
                    "dur": p.t1-p.t0} for p in b.phases],
        "metric_units": {c: L.METRIC_META[c][1] for c in L.METRIC_COLS},
        "metric_labels": {c: L.METRIC_META[c][0] for c in L.METRIC_COLS},
    }
    (L.FIGDIR / "stats.json").write_text(json.dumps(out, indent=2))

    print("STATE gameplay vs meditation (beat-based 30s sub-windows):")
    for c in ("HR", "SDNN", "LF_HF"):
        r = out["by_metric"][c]["game_vs_med"]
        print(f"  {c}: game={r['mean_a']:.2f} med={r['mean_b']:.2f} n={r['n_a']}/{r['n_b']} t={r['t']:.1f} p={r['p']:.2e} d={r['d']:.2f}")
    print("Nap vs task00 (HR/SDNN):")
    for c in ("HR", "SDNN"):
        nap = out["by_metric"][c]["phase_means"]["nap"]["mean"]
        q = out["by_metric"][c]["phase_means"]["task00"]["mean"]
        print(f"  {c}: nap={nap:.1f} quest={q:.1f}")


if __name__ == "__main__":
    main()
