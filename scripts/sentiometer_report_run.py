"""One-command driver for the Sentiometer phase-resolved report.

Runs the full pipeline for a session and renders the PDF:
  1. sentiometer_derive.py     XDF -> derived parquet bundle
  2. sentiometer_report_figs.py  bundle -> 16 figures
  3. sentiometer_report_stats.py bundle -> stats.json
  4. sentiometer_report_html.py  stats + figs -> index.html
  5. headless Edge/Chrome        index.html -> PDF

The subject/session is the XDF filename stem (e.g. "P013_S01"). Drop the
session XDF in sample-data/ and run:

    uv run python scripts/sentiometer_report_run.py sample-data/P013_S02.xdf

or, to re-render an already-derived session:

    uv run python scripts/sentiometer_report_run.py --subject P013_S01

Everything lands in outputs/<SUBJECT>/ (gitignored). pandas/pyarrow/
matplotlib/scipy are injected per-run via `uv run --with ...` so they need
not be project dependencies.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
WITH = ["--with", "pandas", "--with", "pyarrow", "--with", "matplotlib", "--with", "scipy"]

EDGE_CANDIDATES = [
    Path(r"C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
    Path(r"C:/Program Files/Microsoft/Edge/Application/msedge.exe"),
    Path(r"C:/Program Files/Google/Chrome/Application/chrome.exe"),
    Path(r"C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
]


def run(cmd, env=None):
    print(f"\n$ {' '.join(str(c) for c in cmd)}")
    r = subprocess.run(cmd, env=env, cwd=REPO)
    if r.returncode != 0:
        sys.exit(f"step failed (exit {r.returncode}): {cmd}")


def uv_step(script, env, *args):
    run(["uv", "run", *WITH, "python", str(SCRIPTS / script), *args], env=env)


def find_browser() -> Path:
    for p in EDGE_CANDIDATES:
        if p.exists():
            return p
    sys.exit("No headless Edge/Chrome found; install one or render the HTML manually.")


def main():
    ap = argparse.ArgumentParser(description="Build the Sentiometer report for a session.")
    ap.add_argument("xdf", nargs="?", help="Path to the session XDF (omit with --subject to skip derive).")
    ap.add_argument("--subject", help="Subject/session id (defaults to XDF stem).")
    ap.add_argument("--date", default="", help="Report date string for the cover (e.g. 'June 2, 2026').")
    ap.add_argument("--skip-derive", action="store_true", help="Reuse an existing derived bundle.")
    args = ap.parse_args()

    if args.xdf:
        xdf = Path(args.xdf)
        if not xdf.exists():
            sys.exit(f"XDF not found: {xdf}")
        subject = args.subject or xdf.stem
    elif args.subject:
        subject = args.subject
        xdf = None
    else:
        sys.exit("Provide an XDF path or --subject.")

    env = dict(os.environ, SENT_SUBJECT=subject)
    if args.date:
        env["SENT_REPORT_DATE"] = args.date

    print(f"=== Sentiometer report · subject={subject} ===")

    # 1. derive (unless skipping / no xdf)
    if xdf and not args.skip_derive:
        uv_step("sentiometer_derive.py", env, str(xdf))
    else:
        print("(skipping derive — using existing bundle)")

    # ensure brand assets are in place next to the HTML
    rpt = REPO / "outputs" / subject / "report"
    (rpt / "figs").mkdir(parents=True, exist_ok=True)
    (rpt / "assets").mkdir(parents=True, exist_ok=True)
    src_reports = Path(r"D:/LocalDataAnalysis/iacs-reports")
    for name, dst in [("iacs-report.css", rpt / "iacs-report.css")]:
        s = src_reports / name
        if s.exists():
            dst.write_bytes(s.read_bytes())
    for name in ("IACS_Logo.svg", "IACS_Mark.png"):
        s = src_reports / "assets" / name
        if s.exists():
            (rpt / "assets" / name).write_bytes(s.read_bytes())

    # 2-4. figures, stats, html
    uv_step("sentiometer_report_figs.py", env)
    uv_step("sentiometer_report_stats.py", env)
    uv_step("sentiometer_report_html.py", env)

    # 5. render PDF
    browser = find_browser()
    html = rpt / "index.html"
    pdf = rpt / f"{subject}_sentiometer_report.pdf"
    html_uri = "file:///" + str(html).replace("\\", "/")
    run([str(browser), "--headless", "--disable-gpu", "--no-pdf-header-footer",
         f"--print-to-pdf={pdf}", html_uri])
    print(f"\nDONE -> {pdf}")


if __name__ == "__main__":
    main()
