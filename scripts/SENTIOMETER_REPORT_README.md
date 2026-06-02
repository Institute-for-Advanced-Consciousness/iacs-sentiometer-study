# Sentiometer Phase-Resolved Report

Generates an IACS-styled PDF showing the 6-channel optical Sentiometer signal
across every phase of a P013 session, for all seven derived series
(PD1–PD5, the channel **Mean**, and **PC1** — the first principal component of
the photodiode array).

The report has three parts:

1. **Full-session overview** — one landscape page, all seven series z-scored,
   split into a task-suite band and a nap band, with each paradigm labeled.
2. **Per-phase zooms** — one landscape page per phase (questionnaire, oddball,
   RGB, masking, mind-state, SSVEP, nap) with that phase's event markers
   overlaid.
3. **Event-grouped analysis** — baseline-corrected ERPs (−300…+300 ms) for
   discrete events (deviant tones, color onsets, face onsets, collisions) and
   deviation-from-grand-mean bar charts for sustained conditions (RGB colors,
   gameplay vs meditation, SSVEP frequency bands).

## One-command run

Drop a session XDF in `sample-data/` (filename stem = subject id, e.g.
`P013_S02.xdf`) and run:

```bash
uv run python scripts/sentiometer_report_run.py sample-data/P013_S02.xdf --date "June 2, 2026"
```

Output lands in `outputs/<SUBJECT>/`:

```
outputs/P013_S02/
├── preprocessed/                       # derived signal bundle
│   ├── sentiometer_timeseries.parquet  # t_lsl, PD1-5, sent_mean, pc1, task
│   ├── sentiometer_markers.parquet
│   └── sentiometer_pca.json
└── report/
    ├── P013_S02_sentiometer_report.pdf  ← the deliverable
    ├── index.html, iacs-report.css, assets/, figs/*.png
```

Re-render an already-derived session without re-reading the 2 GB XDF:

```bash
uv run python scripts/sentiometer_report_run.py --subject P013_S01 --skip-derive
```

## Pipeline (what the runner calls, in order)

| Step | Script | In → Out |
|---|---|---|
| 1 | `sentiometer_derive.py <xdf>` | XDF → `preprocessed/` parquet bundle |
| 2 | `sentiometer_report_figs.py` | bundle → 16 figure PNGs |
| 3 | `sentiometer_report_stats.py` | bundle → `figs/stats.json` |
| 4 | `sentiometer_report_html.py` | stats + figs → `index.html` |
| 5 | headless Edge/Chrome | `index.html` → PDF |

`sentiometer_report_lib.py` is the shared library (palette, the 7-series
definition, epoching, window means, SSVEP frequency reconstruction).

## Notes

- **Dependencies** (`pandas`, `pyarrow`, `matplotlib`, `scipy`) are injected
  per-run via `uv run --with …` by the runner — they are *not* project deps,
  and `uv pip install` does **not** persist (uv re-syncs from the lockfile).
- **Subject knob**: every script honors `SENT_SUBJECT` (default `P013_S01`);
  the runner sets it from the XDF stem. `SENT_REPORT_DATE` fills the cover date.
- **Analysis conventions** (locked): overlays use a whole-recording z-score per
  series; ERPs are baseline-corrected over −300…0 ms with the raw baseline in
  the caption; the mind-state contrast is gameplay vs meditation only (break is
  a transition, excluded); the masking primary contrast is face-onset-locked,
  face-present only (excludes catch-trial correct rejections).
- Everything under `outputs/` and `sample-data/` is gitignored — no raw data or
  participant artifacts are committed.
```
