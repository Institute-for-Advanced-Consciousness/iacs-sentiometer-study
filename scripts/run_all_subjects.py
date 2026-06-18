"""ONE COMMAND to (re)build every P013 report — per-subject + group.

This is the entry point for "we have more subjects now". Drop each new
session XDF into ``sample-data/`` named ``P013_S<NN>.xdf`` and run:

    uv run python scripts/run_all_subjects.py --date "June 16, 2026"

What it does, in order:
  1. Discover every ``sample-data/P013_S*.xdf``.
  2. For each subject, build the per-subject Sentiometer report and the
     per-subject cardiac report (derive -> figs -> stats -> html -> PDF),
     UNLESS that subject's derived bundle already exists (skip for speed;
     use --force to rebuild everyone).
  3. Build the GROUP Sentiometer report and the GROUP cardiac report over
     every subject discovered under outputs/ (overlays + mean lines, pooled-
     variance bars, Fisher-z mean-r matrices).

Outputs:
  outputs/<SUBJECT>/report/<SUBJECT>_sentiometer_report.pdf      (per subject)
  outputs/<SUBJECT>/cardiac/report/<SUBJECT>_cardiac_report.pdf  (per subject)
  outputs/_group/sentiometer/report/P013_group_sentiometer_report.pdf
  outputs/_group/cardiac/report/P013_group_cardiac_report.pdf

Flags:
  --date "June 16, 2026"   cover date string for every report
  --force                  rebuild per-subject reports even if a bundle exists
  --only-group             skip per-subject builds; just rebuild the group reports
  --subjects A,B           restrict to these subject stems (default: all in sample-data)
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
SAMPLE = REPO / "sample-data"
OUT = REPO / "outputs"


def run(cmd):
    print(f"\n$ {' '.join(str(c) for c in cmd)}", flush=True)
    r = subprocess.run(cmd, cwd=REPO)
    if r.returncode != 0:
        sys.exit(f"step failed (exit {r.returncode}): {cmd}")


def have_sentiometer(subj: str) -> bool:
    return (OUT / subj / "preprocessed" / "sentiometer_timeseries.parquet").exists()


def have_cardiac(subj: str) -> bool:
    return (OUT / subj / "cardiac" / "cardiac_timeseries.parquet").exists()


def main():
    ap = argparse.ArgumentParser(description="Build all P013 per-subject + group reports.")
    ap.add_argument("--date", default="")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--only-group", action="store_true")
    ap.add_argument("--subjects", default="")
    args = ap.parse_args()

    date_args = (["--date", args.date] if args.date else [])

    xdfs = sorted(SAMPLE.glob("P013_S*.xdf"))
    if args.subjects:
        want = {s.strip() for s in args.subjects.split(",") if s.strip()}
        xdfs = [x for x in xdfs if x.stem in want]
    if not xdfs:
        sys.exit(f"No P013_S*.xdf found in {SAMPLE}")
    print(f"Subjects discovered: {[x.stem for x in xdfs]}")

    if not args.only_group:
        for xdf in xdfs:
            subj = xdf.stem
            # Sentiometer per-subject
            if args.force or not have_sentiometer(subj):
                run(["uv", "run", "python", str(SCRIPTS / "sentiometer_report_run.py"),
                     str(xdf), *date_args])
            else:
                print(f"[skip] {subj} sentiometer bundle exists (use --force to rebuild)")
            # Cardiac per-subject
            if args.force or not have_cardiac(subj):
                run(["uv", "run", "python", str(SCRIPTS / "cardiac_report_run.py"),
                     str(xdf), *date_args])
            else:
                print(f"[skip] {subj} cardiac bundle exists (use --force to rebuild)")

    # Group reports (always rebuilt so they include every subject)
    run(["uv", "run", "python", str(SCRIPTS / "group_report_run.py"),
         "--kind", "both", *date_args])

    print("\n=== ALL DONE ===")
    print("Per-subject PDFs:  outputs/<SUBJECT>/report/  and  outputs/<SUBJECT>/cardiac/report/")
    print("Group PDFs:        outputs/_group/sentiometer/report/  and  outputs/_group/cardiac/report/")


if __name__ == "__main__":
    main()
