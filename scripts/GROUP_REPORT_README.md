# P013 Group (multi-subject) reports

Pooled, IACS-styled PDF reports across **all** P013 subjects — one for the
Sentiometer arm and one for the cardiac/HRV arm. They are the group analogs of
the per-subject reports (`SENTIOMETER_REPORT_README.md` and the cardiac
pipeline), built from the **same derived bundles** with the **same analysis
code**, then aggregated three ways:

| Figure kind | Single-subject | Group |
|---|---|---|
| Time-series / ERPs | one line ± within-subject SEM | **one thin line per subject + a bold across-subject mean** (±1 between-subject SEM band) |
| Bar charts | condition means ± SEM across bins | **grand mean of the per-subject means ± pooled within-subject SD** (the *pooled variance*), with a dot per subject |
| Correlation matrices (cardiac only) | per-session Pearson r | **mean r across subjects** (Fisher-z averaged, back-transformed); ‡ = all subjects agree in sign |

Whole-session overviews are shown as **small multiples** (one per subject), not
a single overlaid line, because paradigm durations differ between subjects so
there is no shared time axis. Every event-locked and condition contrast *is*
aligned and overlaid.

## One command (the "we have more subjects" workflow)

1. Drop each new session XDF into `sample-data/`, named `P013_S<NN>.xdf`
   (e.g. `P013_S04.xdf`). Stream names must match the existing sessions:
   `Sentiometer`, `CGX AIM Phys. Mon. AIM-0106`, `P013_Task_Markers`.
2. Run:

   ```bash
   uv run python scripts/run_all_subjects.py --date "June 16, 2026"
   ```

That script:
1. discovers every `sample-data/P013_S*.xdf`;
2. builds the per-subject Sentiometer **and** cardiac reports for any subject
   whose derived bundle doesn't exist yet (already-built subjects are skipped —
   pass `--force` to rebuild everyone);
3. rebuilds **both group reports** over every subject found under `outputs/`.

Outputs:

```
outputs/<SUBJECT>/report/<SUBJECT>_sentiometer_report.pdf       per subject
outputs/<SUBJECT>/cardiac/report/<SUBJECT>_cardiac_report.pdf   per subject
outputs/_group/sentiometer/report/P013_group_sentiometer_report.pdf
outputs/_group/cardiac/report/P013_group_cardiac_report.pdf
```

`run_all_subjects.py` flags: `--date`, `--force` (rebuild per-subject too),
`--only-group` (skip per-subject, just rebuild group), `--subjects A,B`.

## Rebuild only the group reports (bundles already derived)

```bash
uv run python scripts/group_report_run.py --kind both --date "June 16, 2026"
# or  --kind sentiometer  /  --kind cardiac
```

Subjects are auto-discovered from `outputs/`. Pin an explicit set/order with
`GROUP_SUBJECTS="P013_S01,P013_S02,P013_S03"` in the environment.

## Files

```
group_report_lib.py        shared: subject discovery, IACS style, pooled_stats(),
                           fisher_mean_r(), draw_overlay(), to_common_grid()
sentiometer_group_figs.py  overview small-multiples, ERP overlays, pooled bars
sentiometer_group_stats.py per-subject contrasts (reuses single-subject welch/anova)
                           + across-subject mean d + random-effects t-test
sentiometer_group_html.py  IACS HTML -> PDF
cardiac_group_figs.py      overview, inst-HR ERP overlays, pooled bars,
                           Fisher-z mean-r matrices (whole + per block)
cardiac_group_stats.py     per-subject game-vs-med (beat-based) + pooled phase means
cardiac_group_html.py      IACS HTML -> PDF
group_report_run.py        figs -> stats -> html -> headless-Edge PDF, per modality
run_all_subjects.py        ONE COMMAND: per-subject + group for everyone
```

The group scripts reuse the single-subject libraries unchanged: they repoint
each library's module-level `BUNDLE` (and `cardiac_xcorr.SENT_PARQUET`) at each
subject in turn and call the same `load()/epoch()/window_means()/block_metrics()/
corr_matrix()` functions, so "same preprocessing, same analysis" is guaranteed by
construction rather than re-implemented.

## How the aggregation is defined (so the numbers are auditable)

* **Pooled-variance bars.** For each panel and subject, the condition means are
  centred by that subject's panel grand mean (removing the per-subject DC offset
  so different absolute levels are comparable). The bar is the mean across
  subjects of those centred condition means; the error bar is the pooled
  within-subject SD, `sqrt( Σ(n_i−1)s_i² / Σ(n_i−1) )`; each subject is also a dot.
  (`group_report_lib.pooled_stats`.)
* **Mean-r matrices.** `r_mean = tanh( mean_i arctanh(r_i) )` per cell across
  subjects, with a ‡ flag where every contributing subject's r has the same sign.
  (`group_report_lib.fisher_mean_r`.)
* **Effect-size tables.** Cohen's d per subject (identical to the single-subject
  report), plus the across-subject mean/SD of d and a random-effects one-sample
  t-test of those d's against zero (subject = unit of replication). With few
  subjects this is low-powered; read the per-subject d's and the sign column.

## Headline group findings (S01–S03)

* **Mind-state (Sentiometer)** is large within every subject but *sign-inconsistent*
  (d ≈ +2.1, +6.9, −2.5) → random-effects n.s. The optical signal tracks cognitive
  state but not a fixed-polarity axis shared across people.
* **RGB** is null in all subjects (predicted).
* **Backward masking** seen−unseen (Sentiometer) is tiny but negative in all 3
  subjects (the most directionally consistent stimulus-locked optical effect).
* **Cardiac:** meditation raises LF/HF in all 3 subjects (random-effects p ≈ 0.04)
  with a universal respiration slowdown; the nap drops HR and raises SDNN as
  expected. Cross-signal: higher Sentiometer ↔ higher HF/SDNN, lower LF/HF
  (sign-consistent across subjects); the single-subject HR coupling washes out.
