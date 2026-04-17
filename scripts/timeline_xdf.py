"""Print a chronological timeline of every marker in a recorded XDF.

Reads the single .xdf in ``sampledata/`` (or a path on CLI) and prints one
line per marker: Pacific-time wall clock, elapsed seconds from session
start, and the marker string. Vayl JSON markers are expanded inline so
you can see the carrier frequencies and `wallTimeMs` at a glance.

Usage::

    uv run python scripts/timeline_xdf.py
    uv run python scripts/timeline_xdf.py path/to/other.xdf
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pyxdf
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SAMPLE_DIR = REPO_ROOT / "sampledata"
PT = ZoneInfo("America/Los_Angeles")

# Task phase colours (terminal)
TASK_STYLE = {
    "session": "bold magenta",
    "task01": "cyan",
    "task02": "green",
    "task03": "yellow",
    "task04": "blue",
    "task05": "red",
    "vayl": "red",
    "other": "white",
}


def _find_xdf(path_arg: str | None) -> Path:
    if path_arg:
        p = Path(path_arg).expanduser()
        if not p.exists():
            raise SystemExit(f"XDF file not found: {p}")
        return p
    if not DEFAULT_SAMPLE_DIR.exists():
        raise SystemExit(f"No sampledata/ directory at {DEFAULT_SAMPLE_DIR}.")
    candidates = sorted(DEFAULT_SAMPLE_DIR.glob("*.xdf"))
    if not candidates:
        raise SystemExit(f"No .xdf in {DEFAULT_SAMPLE_DIR}.")
    if len(candidates) > 1:
        names = "\n  ".join(str(c.name) for c in candidates)
        raise SystemExit(
            f"Multiple .xdf files in {DEFAULT_SAMPLE_DIR}:\n  {names}\n"
            "Pass one on the CLI."
        )
    return candidates[0]


def _marker_stream(streams: list[dict]) -> dict | None:
    return next(
        (
            s
            for s in streams
            if (s.get("info") or {}).get("name", [""])[0] == "P013_Task_Markers"
        ),
        None,
    )


def _phase_for(marker: str) -> str:
    if marker.startswith("task01"):
        return "task01"
    if marker.startswith("task02"):
        return "task02"
    if marker.startswith("task03"):
        return "task03"
    if marker.startswith("task04"):
        return "task04"
    if marker.startswith("task05"):
        return "task05"
    if marker.startswith("{"):
        # Vayl JSON events land here now
        return "vayl"
    if marker.startswith("session") or marker.startswith("participant_id"):
        return "session"
    return "other"


def _pretty_marker(marker: str) -> str:
    """Expand JSON Vayl events inline with key fields, leave others intact."""
    if not marker.startswith("{"):
        return marker
    try:
        obj = json.loads(marker)
    except Exception:  # noqa: BLE001
        return marker
    event = obj.get("event", "vayl")
    parts = [f"vayl:{event}"]
    for k in ("stimFreqHz", "stimFreqEndHz", "carrierHz", "carrierEndHz",
              "durationSeconds", "wallTimeMs"):
        if k in obj:
            v = obj[k]
            if isinstance(v, float):
                parts.append(f"{k}={v:g}")
            else:
                parts.append(f"{k}={v}")
    return " ".join(parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?")
    parser.add_argument(
        "--no-details",
        action="store_true",
        help="Hide the per-marker rows; print only phase summaries.",
    )
    args = parser.parse_args(argv)

    try:
        xdf_path = _find_xdf(args.path)
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 2

    console = Console()
    console.print(
        Panel(f"[bold cyan]XDF timeline[/bold cyan]\n{xdf_path}", expand=False)
    )

    streams, _ = pyxdf.load_xdf(str(xdf_path))
    marker_stream = _marker_stream(streams)
    if marker_stream is None:
        console.print("[red]No P013_Task_Markers stream in file.[/red]")
        return 1

    samples = marker_stream.get("time_series")
    if samples is None:
        samples = []
    ts_raw = marker_stream.get("time_stamps")
    ts = np.asarray(ts_raw if ts_raw is not None else [], dtype=float)
    if len(samples) == 0:
        console.print("[yellow]P013_Task_Markers is empty.[/yellow]")
        return 0

    markers = [
        (s[0] if isinstance(s, (list, tuple)) and s else str(s))
        for s in samples
    ]

    # Wall-clock reference: anchor the last marker to the file's mtime
    # (LabRecorder closes the file ~immediately after the last sample is
    # written). Errors are at most a few hundred ms.
    lsl_min = float(ts.min())
    lsl_max = float(ts.max())
    duration_s = lsl_max - lsl_min
    file_utc = datetime.fromtimestamp(os.path.getmtime(xdf_path), tz=timezone.utc)
    session_start_utc = file_utc - timedelta(seconds=duration_s)
    session_start_pt = session_start_utc.astimezone(PT)

    # ----- phase summary table ------------------------------------------
    phases: dict[str, dict] = {}
    for m, t in zip(markers, ts):
        phase = _phase_for(m)
        first_last = phases.setdefault(phase, {"count": 0, "first": t, "last": t})
        first_last["count"] += 1
        first_last["first"] = min(first_last["first"], t)
        first_last["last"] = max(first_last["last"], t)

    summary = Table(title="Phase summary", show_lines=False, expand=False)
    summary.add_column("Phase", style="white")
    summary.add_column("Markers", justify="right")
    summary.add_column("Start (PT)", style="dim")
    summary.add_column("Start (+s)", justify="right", style="dim")
    summary.add_column("Duration (s)", justify="right")

    ordered = [
        "session",
        "task01",
        "task02",
        "task03",
        "task04",
        "task05",
        "vayl",
        "other",
    ]
    for name in ordered:
        if name not in phases:
            continue
        info = phases[name]
        first_wall = session_start_pt + timedelta(
            seconds=(info["first"] - lsl_min)
        )
        elapsed_to_first = info["first"] - lsl_min
        span = info["last"] - info["first"]
        style = TASK_STYLE.get(name, "white")
        summary.add_row(
            f"[{style}]{name}[/{style}]",
            str(info["count"]),
            first_wall.strftime("%H:%M:%S.%f")[:-3],
            f"{elapsed_to_first:+.3f}",
            f"{span:.1f}",
        )
    console.print(summary)
    console.print()

    if args.no_details:
        return 0

    # ----- full chronological listing -----------------------------------
    detail = Table(
        title="Chronological marker list",
        show_lines=False,
        expand=False,
    )
    detail.add_column("PT wall clock", style="dim")
    detail.add_column("Elapsed (s)", justify="right", style="dim")
    detail.add_column("Phase", justify="center")
    detail.add_column("Marker", overflow="fold")

    for m, t in zip(markers, ts):
        wall = session_start_pt + timedelta(seconds=(t - lsl_min))
        phase = _phase_for(m)
        style = TASK_STYLE.get(phase, "white")
        detail.add_row(
            wall.strftime("%H:%M:%S.%f")[:-3],
            f"{t - lsl_min:7.3f}",
            f"[{style}]{phase}[/{style}]",
            _pretty_marker(m),
        )

    console.print(detail)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
