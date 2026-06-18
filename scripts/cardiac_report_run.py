"""One-command driver for the cardiac/HRV phase-resolved report.

Cardiac analog of sentiometer_report_run.py. Runs the pipeline and renders
the PDF:
  1. cardiac_derive.py       XDF -> beats + windowed-metrics bundle
  2. cardiac_report_figs.py  bundle -> figures
  3. cardiac_report_stats.py bundle -> stats.json
  4. cardiac_report_html.py  stats + figs -> index.html
  5. headless Edge/Chrome    index.html -> PDF

Usage:
    uv run python scripts/cardiac_report_run.py sample-data/P013_S02.xdf --date "June 3, 2026"
    uv run python scripts/cardiac_report_run.py --subject P013_S01 --skip-derive
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
DERIVE_WITH = ["--with", "neurokit2", "--with", "pandas", "--with", "pyarrow",
               "--with", "scipy", "--with", "pyxdf"]
REPORT_WITH = ["--with", "pandas", "--with", "pyarrow", "--with", "matplotlib", "--with", "scipy"]
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


def uv_step(with_flags, script, env, *args):
    run(["uv", "run", *with_flags, "python", str(SCRIPTS / script), *args], env=env)


def find_browser():
    for p in EDGE_CANDIDATES:
        if p.exists():
            return p
    sys.exit("No headless Edge/Chrome found.")


def main():
    ap = argparse.ArgumentParser(description="Build the cardiac/HRV report for a session.")
    ap.add_argument("xdf", nargs="?")
    ap.add_argument("--subject")
    ap.add_argument("--date", default="")
    ap.add_argument("--skip-derive", action="store_true")
    args = ap.parse_args()

    if args.xdf:
        xdf = Path(args.xdf)
        if not xdf.exists():
            sys.exit(f"XDF not found: {xdf}")
        subject = args.subject or xdf.stem
    elif args.subject:
        subject = args.subject; xdf = None
    else:
        sys.exit("Provide an XDF path or --subject.")

    env = dict(os.environ, SENT_SUBJECT=subject)
    if args.date:
        env["SENT_REPORT_DATE"] = args.date
    print(f"=== Cardiac report · subject={subject} ===")

    if xdf and not args.skip_derive:
        uv_step(DERIVE_WITH, "cardiac_derive.py", env, str(xdf))
    else:
        print("(skipping derive — using existing bundle)")

    rpt = REPO / "outputs" / subject / "cardiac" / "report"
    (rpt / "figs").mkdir(parents=True, exist_ok=True)
    (rpt / "assets").mkdir(parents=True, exist_ok=True)
    src_reports = Path(r"D:/LocalDataAnalysis/iacs-reports")
    if (src_reports / "iacs-report.css").exists():
        (rpt / "iacs-report.css").write_bytes((src_reports / "iacs-report.css").read_bytes())
    for name in ("IACS_Logo.svg", "IACS_Mark.png"):
        s = src_reports / "assets" / name
        if s.exists():
            (rpt / "assets" / name).write_bytes(s.read_bytes())

    uv_step(REPORT_WITH, "cardiac_report_figs.py", env)
    uv_step(REPORT_WITH, "cardiac_report_stats.py", env)
    uv_step(REPORT_WITH, "cardiac_report_html.py", env)

    browser = find_browser()
    html = rpt / "index.html"
    pdf = rpt / f"{subject}_cardiac_report.pdf"
    html_uri = "file:///" + str(html).replace("\\", "/")
    run([str(browser), "--headless", "--disable-gpu", "--no-pdf-header-footer",
         f"--print-to-pdf={pdf}", html_uri])
    print(f"\nDONE -> {pdf}")


if __name__ == "__main__":
    main()
