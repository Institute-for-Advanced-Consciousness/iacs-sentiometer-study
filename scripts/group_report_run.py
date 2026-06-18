"""One-command driver for the GROUP (multi-subject) reports.

Builds the pooled report for one or both modalities and renders the PDF:
  1. <kind>_group_figs.py    bundles -> overlay/pooled figures + manifest
  2. <kind>_group_stats.py   bundles -> stats.json
  3. <kind>_group_html.py    stats + figs -> index.html
  4. headless Edge/Chrome    index.html -> PDF

Subjects are auto-discovered from outputs/ (override with GROUP_SUBJECTS).

Usage:
    uv run python scripts/group_report_run.py                 # both modalities
    uv run python scripts/group_report_run.py --kind sentiometer
    uv run python scripts/group_report_run.py --date "June 16, 2026"
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

# kind -> (report dir relative to outputs/_group, pdf stem)
KINDS = {
    "sentiometer": ("sentiometer", "P013_group_sentiometer_report"),
    "cardiac": ("cardiac", "P013_group_cardiac_report"),
}


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
    sys.exit("No headless Edge/Chrome found.")


def copy_assets(rpt: Path):
    (rpt / "figs").mkdir(parents=True, exist_ok=True)
    (rpt / "assets").mkdir(parents=True, exist_ok=True)
    src_reports = Path(r"D:/LocalDataAnalysis/iacs-reports")
    css = src_reports / "iacs-report.css"
    if css.exists():
        (rpt / "iacs-report.css").write_bytes(css.read_bytes())
    for name in ("IACS_Logo.svg", "IACS_Mark.png"):
        s = src_reports / "assets" / name
        if s.exists():
            (rpt / "assets" / name).write_bytes(s.read_bytes())


def build_kind(kind: str, env: dict):
    subdir, pdf_stem = KINDS[kind]
    rpt = REPO / "outputs" / "_group" / subdir / "report"
    copy_assets(rpt)
    uv_step(f"{kind}_group_figs.py", env)
    uv_step(f"{kind}_group_stats.py", env)
    uv_step(f"{kind}_group_html.py", env)
    browser = find_browser()
    html = rpt / "index.html"
    pdf = rpt / f"{pdf_stem}.pdf"
    html_uri = "file:///" + str(html).replace("\\", "/")
    run([str(browser), "--headless", "--disable-gpu", "--no-pdf-header-footer",
         f"--print-to-pdf={pdf}", html_uri])
    print(f"\nDONE -> {pdf}")


def main():
    ap = argparse.ArgumentParser(description="Build the group (multi-subject) report(s).")
    ap.add_argument("--kind", choices=["sentiometer", "cardiac", "both"], default="both")
    ap.add_argument("--date", default="")
    args = ap.parse_args()

    env = dict(os.environ)
    if args.date:
        env["SENT_REPORT_DATE"] = args.date

    kinds = ["sentiometer", "cardiac"] if args.kind == "both" else [args.kind]
    for k in kinds:
        print(f"\n=== GROUP {k} report ===")
        build_kind(k, env)


if __name__ == "__main__":
    main()
