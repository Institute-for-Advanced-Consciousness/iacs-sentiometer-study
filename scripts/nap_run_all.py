"""ONE COMMAND to build the full Paller nap handoff bundle.

Drop each session XDF into sample-data/ named P013_S<NN>.xdf and run:

    uv run python scripts/nap_run_all.py

For every P013_S*.xdf it:
  1. extracts the clean motion-bookended nap epoch as EDF+ (nap_export.py),
     skipping subjects whose EDF already exists (--force to rebuild);
  2. rebuilds the shared one-page channel manifest PDF (nap_channel_manifest.py).

Everything lands in outputs/_paller_nap/ — that folder is the bundle for Ken:
  P013_S<NN>_nap.edf            one continuous nap epoch per participant
  P013_channel_manifest.pdf     shared one-pager (same montage for all)
  diagnostics/                  per-subject segmentation PNG + JSON sidecar
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
SAMPLE = REPO / "sample-data"
OUT = REPO / "outputs" / "_paller_nap"
EXPORT_WITH = ["--with", "pyxdf", "--with", "pyedflib", "--with", "numpy", "--with", "matplotlib"]


def run(cmd):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    print(f"\n$ {' '.join(str(c) for c in cmd)}", flush=True)
    r = subprocess.run(cmd, cwd=REPO, env=env)
    if r.returncode != 0:
        sys.exit(f"step failed (exit {r.returncode}): {cmd}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="rebuild EDFs even if present")
    ap.add_argument("--subjects", default="", help="comma list of stems to limit to")
    args = ap.parse_args()

    xdfs = sorted(SAMPLE.glob("P013_S*.xdf"))
    if args.subjects:
        want = {s.strip() for s in args.subjects.split(",") if s.strip()}
        xdfs = [x for x in xdfs if x.stem in want]
    if not xdfs:
        sys.exit(f"No P013_S*.xdf in {SAMPLE}")
    print("Subjects:", [x.stem for x in xdfs])

    for xdf in xdfs:
        edf = OUT / f"{xdf.stem}_nap.edf"
        if edf.exists() and not args.force:
            print(f"[skip] {xdf.stem} nap EDF exists (use --force)")
            continue
        run(["uv", "run", *EXPORT_WITH, "python", str(SCRIPTS / "nap_export.py"), str(xdf)])

    run(["uv", "run", "--with", "pyedflib", "python", str(SCRIPTS / "nap_channel_manifest.py")])
    print(f"\n=== DONE === bundle: {OUT}")
    for f in sorted(OUT.glob("*.edf")) + sorted(OUT.glob("*.pdf")):
        print(f"  {f.name}  ({f.stat().st_size/1e6:.0f} MB)" if f.suffix == ".edf"
              else f"  {f.name}")


if __name__ == "__main__":
    main()
