"""One-shot runner for the XDF → EDF+ / Paller bundle pipeline.

Runs, in order:

    1. 01_inspect.py       (read-only inventory + forensic on first 120 s)
    2. 01b_spectral.py     (PSD + line-noise report)
    3. 02_convert.py       (EDF+ write + spot-check)
    4. 03_manifest.py      (PDF + README write)

Every step autodetects the newest .xdf in ``sampledata/`` and writes
its artefacts to ``outputs/<SUBJECT>/`` (``<SUBJECT>/diagnostics/`` for
PNGs/CSVs). Drop a new XDF into ``sampledata/`` and run this again —
the bundle for that subject regenerates without touching older ones.
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

from _common import find_xdf, output_dir_for, subject_from_xdf

STEPS = [
    "01_inspect.py",
    "01b_spectral.py",
    "02_convert.py",
    "03_manifest.py",
]


def main() -> int:
    xdf = find_xdf()
    subject = subject_from_xdf(xdf)
    bundle = output_dir_for(subject)

    print("=" * 78)
    print(f"Running Paller pipeline on:  {xdf}")
    print(f"Subject auto-detected:       {subject}")
    print(f"Output bundle will be:       {bundle}")
    print("=" * 78)

    here = Path(__file__).resolve().parent
    # Ensure sibling-script imports (``from _common import …``) work when
    # runpy exec'd them.
    sys.path.insert(0, str(here))

    for step in STEPS:
        path = here / step
        if not path.exists():
            print(f"SKIP {step}: not found at {path}", file=sys.stderr)
            continue
        print(f"\n----- STEP {step} -----")
        try:
            runpy.run_path(str(path), run_name="__main__")
        except SystemExit as exc:
            code = getattr(exc, "code", 0) or 0
            if code != 0:
                print(f"\n{step} exited with code {code}; pipeline stopped.",
                      file=sys.stderr)
                return int(code)

    print("\n" + "=" * 78)
    print(f"Pipeline complete. Bundle at: {bundle}")
    print("=" * 78)
    # List the produced bundle tree for convenience.
    for p in sorted(bundle.rglob("*")):
        if p.is_file():
            rel = p.relative_to(bundle)
            size_kb = p.stat().st_size / 1024
            print(f"  {rel}  ({size_kb:,.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
