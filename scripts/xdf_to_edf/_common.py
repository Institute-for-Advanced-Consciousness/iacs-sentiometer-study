"""Shared helpers for the XDF → EDF+ / manifest pipeline.

Every step (01_inspect, 01b_spectral, 02_convert, 03_manifest) imports
from here so that changing the "which file are we running on?" /
"where do outputs go?" logic only requires one edit.

Key conventions:

* **XDF selection.** ``find_xdf()`` picks the **newest .xdf in
  ``sampledata/``** by mtime. Drop a new file in that folder and it
  becomes the active one on the next run. ``--xdf`` on the CLI
  overrides.
* **Subject ID.** ``subject_from_xdf()`` extracts a human-readable
  subject ID from the filename:
  - BIDS-style ``sub-XXX_...`` → ``XXX`` (e.g. ``sub-Yaya_*`` → ``Yaya``).
  - Otherwise falls back to the filename stem (sanitised: no spaces,
    only ``[A-Za-z0-9_-]``).
  Example inputs: ``sub-Yaya_ses-S001_...`` → ``Yaya``;
  ``Sam.xdf`` → ``Sam``; ``S001.xdf`` → ``S001``.
* **Output layout.** Everything for one subject lives inside
  ``outputs/<SUBJECT>/``. The bundle Paller receives is exactly that
  folder.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_DIR = REPO_ROOT / "sampledata"
REF_DIR = REPO_ROOT / "Reference"
OUTPUTS_ROOT = REPO_ROOT / "outputs"

# BrainVision reference files (shipped in Reference/, not sampledata/).
CFG_PATH = REF_DIR / "Brain Amp Series Connector Configuration File - USE THIS ONE FOR 64 - 2026.cfg"
RWKSP_PATH = REF_DIR / "64 Channel Default 2021 Workspace - USE THIS ONE FOR 64 - 2026.rwksp"

_SUB_BIDS_RE = re.compile(r"sub-([A-Za-z0-9]+)", re.ASCII)
_SAFE_CHARS_RE = re.compile(r"[^A-Za-z0-9_-]+")


def find_xdf(explicit: str | Path | None = None) -> Path:
    """Return the XDF to operate on.

    If *explicit* is given, use it. Else pick the newest .xdf in
    ``sampledata/`` by modification time. Raise with a clear message
    if nothing usable is found.
    """
    if explicit is not None:
        p = Path(explicit).expanduser()
        if not p.exists():
            raise SystemExit(f"XDF not found: {p}")
        return p
    if not SAMPLE_DIR.exists():
        raise SystemExit(f"No sampledata/ directory at {SAMPLE_DIR}")
    candidates = [p for p in SAMPLE_DIR.glob("*.xdf") if p.is_file()]
    if not candidates:
        raise SystemExit(f"No .xdf files found in {SAMPLE_DIR}")
    # Newest file first.
    return sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)[0]


def subject_from_xdf(xdf_path: Path) -> str:
    """Derive a subject ID from the XDF filename.

    Preferred: BIDS-style ``sub-<ID>`` prefix. Fallback: sanitised stem.
    """
    stem = xdf_path.stem
    m = _SUB_BIDS_RE.search(stem)
    if m:
        return m.group(1)
    # Fall back to the first underscore-delimited token of the stem so
    # names like "S001_night2" become "S001".
    first = stem.split("_", 1)[0]
    safe = _SAFE_CHARS_RE.sub("", first) or "SUBJECT"
    return safe


def output_dir_for(subject: str, *, create: bool = True) -> Path:
    """Return (and optionally create) ``outputs/<SUBJECT>/``.

    Diagnostic sub-artefacts go into ``outputs/<SUBJECT>/diagnostics/``.
    """
    d = OUTPUTS_ROOT / subject
    if create:
        d.mkdir(parents=True, exist_ok=True)
        (d / "diagnostics").mkdir(parents=True, exist_ok=True)
    return d


def edf_path_for(subject: str) -> Path:
    return output_dir_for(subject) / f"P013_{subject}_for_paller.edf"


def manifest_path_for(subject: str) -> Path:
    return output_dir_for(subject) / f"P013_{subject}_channel_manifest.pdf"


def readme_path_for(subject: str) -> Path:
    return output_dir_for(subject) / f"P013_{subject}_README.txt"


def log_path_for(subject: str) -> Path:
    return output_dir_for(subject) / f"P013_{subject}_conversion_log.txt"


def diag_dir_for(subject: str) -> Path:
    return output_dir_for(subject) / "diagnostics"


def banner(title: str, width: int = 78) -> str:
    line = "=" * width
    return f"\n{line}\n{title}\n{line}"
