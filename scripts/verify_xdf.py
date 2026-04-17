"""Verify that a recorded XDF file contains everything needed for EEG analysis.

Reads the single .xdf file in ``sampledata/`` (or a path passed on the CLI)
and checks, stream by stream:

* the core P013 task-marker stream exists and has session-level bookends;
* every task the launcher claims to have run contains the markers we expect
  (per-paradigm: practice gate, stimulus types, responses, etc.);
* Vayl's own ``VaylStim`` and ``VaylStim_Freq`` streams are present, aligned
  to the task's ``task05_ramp_begin`` / ``task05_ramp_end`` boundaries, and
  expose enough data to reconstruct effective SSVEP frequency at any point;
* hardware recording streams (EEG, Sentiometer, CGX-like streams) are there.

The terminal output is a Rich table of check/status/detail rows plus a
"Suggested edits" list at the bottom so you can paste actionable items back
to Claude Code.

Usage::

    uv run python scripts/verify_xdf.py
    uv run python scripts/verify_xdf.py path/to/other.xdf

"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pyxdf
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SAMPLE_DIR = REPO_ROOT / "sampledata"

# ----- Expected markers per task ---------------------------------------------
# "required" = must appear at least once; "per_trial" = expect many (no
# hard lower bound — we warn if 0). Sourced from the live send_marker()
# calls across src/tasks/**.

SESSION_MARKERS: tuple[str, ...] = ("session_start", "session_end")

TASK_SPECS: dict[str, dict[str, Any]] = {
    "task01_oddball": {
        "display": "Task 01 — Auditory Oddball",
        "required": (
            "task01_start",
            "task01_end",
            "task01_instructions_start",
            "task01_instructions_end",
            "task01_practice_start",
            "task01_practice_end",
            "task01_practice_passed",
        ),
        "per_trial": (
            "task01_tone_standard",
            "task01_tone_deviant",
        ),
        "optional": (
            "task01_response_hit",
            "task01_response_miss",
            "task01_response_false_alarm",
            "task01_practice_tone_standard",
            "task01_practice_tone_deviant",
        ),
        "dynamic_prefixes": ("task01_practice_attempt_",),
    },
    "task02_rgb_illuminance": {
        "display": "Task 02 — RGB Illuminance",
        "required": (
            "task02_start",
            "task02_end",
            "task02_instructions_start",
            "task02_instructions_end",
        ),
        "per_trial": (
            "task02_color_red",
            "task02_color_green",
            "task02_color_blue",
            "task02_iti",
        ),
        "optional": ("task02_break_start", "task02_break_end"),
    },
    "task03_backward_masking": {
        "display": "Task 03 — Backward Masking",
        "required": (
            "task03_start",
            "task03_end",
            "task03_instructions_start",
            "task03_instructions_end",
            "task03_practice_start",
            "task03_practice_end",
        ),
        "per_trial": (
            "task03_fixation_onset",
            "task03_mask_onset",
        ),
        "optional": (
            "task03_face_onset",
            "task03_catch_trial",
            "task03_response_seen",
            "task03_response_unseen",
            "task03_response_unsure",
            "task03_response_timeout",
            "task03_practice_face_onset",
            "task03_practice_catch",
            "task03_practice_mask_onset",
        ),
        "dynamic_prefixes": ("task03_soa_value_",),
    },
    "task04_mind_state": {
        "display": "Task 04 — Mind-State Switching",
        "required": (
            "task04_start",
            "task04_end",
            "task04_instructions_start",
            "task04_instructions_end",
            "task04_game_start",
            "task04_game_end",
            "task04_break_start",
            "task04_break_end",
            "task04_meditation_start",
            "task04_meditation_end",
        ),
        "per_trial": (
            "task04_obstacle_appear",
            "task04_jump_start",
        ),
        "optional": (
            "task04_jump_end",
            "task04_collision",
            "task04_speed_increase",
            "task04_meditation_instructions_start",
            "task04_meditation_instructions_end",
            "task04_meditation_gong_start",
            "task04_meditation_gong_end",
        ),
    },
    "task05_ssvep": {
        "display": "Task 05 — SSVEP Ramp",
        "required": (
            "task05_start",
            "task05_end",
            "task05_instructions_start",
            "task05_instructions_end",
            "task05_ramp_begin",
            "task05_ramp_end",
            "task05_overlay_off",
        ),
        "per_trial": (),
        "optional": (),
    },
}

# Streams we expect to see in the XDF, categorised so we can give tailored
# messages when one is missing.
EXPECTED_STREAMS = {
    "P013_Task_Markers": {"type": "Markers", "role": "task markers (required)"},
    "VaylStim": {"type": "Markers", "role": "Vayl event markers (Task 05)"},
    "VaylStim_Freq": {"type": "Stimulus", "role": "Vayl continuous freq (Task 05)"},
}

# Generic hardware stream detection — matched by type rather than name
# because the stream name is lab-specific (e.g., "BrainVision RDA" /
# "AIM-2"). We warn, not fail, if these aren't there — the XDF might
# intentionally only capture markers for quick checks.
HARDWARE_STREAM_TYPES = {
    "EEG": "EEG / CGX AIM (type=EEG)",
}

SENTIOMETER_STREAM_NAMES = {"IACS_Sentiometer", "Sentiometer"}


# ----- Result dataclasses ----------------------------------------------------


@dataclass
class CheckRow:
    category: str
    name: str
    status: str  # "OK", "WARN", "FAIL"
    detail: str


@dataclass
class VerificationReport:
    rows: list[CheckRow] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)

    def ok(self, category: str, name: str, detail: str = "") -> None:
        self.rows.append(CheckRow(category, name, "OK", detail))

    def warn(self, category: str, name: str, detail: str) -> None:
        self.rows.append(CheckRow(category, name, "WARN", detail))

    def fail(self, category: str, name: str, detail: str) -> None:
        self.rows.append(CheckRow(category, name, "FAIL", detail))

    def suggest(self, line: str) -> None:
        if line not in self.suggestions:
            self.suggestions.append(line)


# ----- Stream helpers --------------------------------------------------------


def _get(stream: dict, *keys: str, default: Any = "") -> Any:
    """Dig into a pyxdf stream dict, returning default if any key missing."""
    cursor: Any = stream
    for k in keys:
        if isinstance(cursor, list) and cursor and isinstance(cursor[0], dict):
            cursor = cursor[0]
        if not isinstance(cursor, dict):
            return default
        cursor = cursor.get(k, default)
    if isinstance(cursor, list) and cursor and isinstance(cursor[0], str):
        return cursor[0]
    return cursor


def _stream_name(stream: dict) -> str:
    return str(_get(stream, "info", "name")) or "<unnamed>"


def _stream_type(stream: dict) -> str:
    return str(_get(stream, "info", "type")) or ""


def _time_series(stream: dict) -> list[Any]:
    return list(stream.get("time_series") or [])


def _timestamps(stream: dict) -> np.ndarray:
    ts = stream.get("time_stamps")
    if ts is None:
        return np.array([])
    return np.asarray(ts, dtype=float)


def _marker_strings(stream: dict) -> list[str]:
    """Return a flat list of marker strings from a marker stream."""
    samples = _time_series(stream)
    out: list[str] = []
    for s in samples:
        if isinstance(s, (list, tuple)) and s:
            out.append(str(s[0]))
        else:
            out.append(str(s))
    return out


# ----- Individual checks -----------------------------------------------------


def _check_task_markers(
    markers: list[str], spec: dict[str, Any], report: VerificationReport
) -> None:
    """Verify per-task marker coverage against a spec."""
    display = spec["display"]
    required = spec.get("required", ())
    per_trial = spec.get("per_trial", ())
    optional = spec.get("optional", ())
    dynamic_prefixes = spec.get("dynamic_prefixes", ())

    # Was the task run at all? If neither start nor end fired, mark the whole
    # block as WARN (task skipped) rather than FAILing every required marker.
    start_marker = required[0] if required else ""
    end_marker = required[1] if len(required) >= 2 else ""
    task_ran = any(m in markers for m in (start_marker, end_marker))
    if not task_ran:
        report.warn(display, "task_run", "No start/end markers — task probably skipped")
        return

    for m in required:
        count = markers.count(m)
        if count == 0:
            report.fail(display, m, "required marker absent")
            report.suggest(
                f"{display}: emit '{m}' — grep for send_marker(outlet, \"{m}\") "
                "in src/tasks/"
            )
        elif count == 1:
            report.ok(display, m, "1 event")
        else:
            report.ok(display, m, f"{count} events")

    for m in per_trial:
        count = markers.count(m)
        if count == 0:
            report.warn(display, m, "0 trials of this kind (expected many)")
            report.suggest(
                f"{display}: zero '{m}' markers; confirm participants didn't abort "
                "this task early"
            )
        else:
            report.ok(display, m, f"{count} trials")

    for m in optional:
        count = markers.count(m)
        if count:
            report.ok(display, m, f"{count} events (optional)")

    for prefix in dynamic_prefixes:
        matches = sorted(
            {m for m in markers if m.startswith(prefix)}
        )
        total = sum(markers.count(m) for m in matches)
        if matches:
            sample = ", ".join(matches[:3])
            suffix = "…" if len(matches) > 3 else ""
            report.ok(
                display,
                f"{prefix}*",
                f"{total} events across {len(matches)} unique values ({sample}{suffix})",
            )
        else:
            report.warn(display, f"{prefix}*", "no dynamic markers emitted")


def _check_session_bookends(
    markers: list[str], report: VerificationReport
) -> None:
    for m in SESSION_MARKERS:
        count = markers.count(m)
        if count == 1:
            report.ok("Session", m, "1 event")
        elif count == 0:
            report.fail("Session", m, "missing — session was not closed cleanly?")
            report.suggest(
                f"Session: emit '{m}' — gui_launcher should bracket each session "
                "with this marker"
            )
        else:
            report.warn("Session", m, f"{count} events (expected 1)")
    abort_count = markers.count("session_abort")
    if abort_count:
        report.warn("Session", "session_abort", f"{abort_count} aborts recorded")


def _check_vayl_streams(
    streams: list[dict],
    task_markers: list[str],
    task_timestamps: np.ndarray,
    report: VerificationReport,
) -> None:
    vayl_freq = next(
        (s for s in streams if _stream_name(s) == "VaylStim_Freq"), None
    )

    # Vayl JSON events are now routed onto P013_Task_Markers — we look for
    # them there first, falling back to a standalone VaylStim stream for
    # backward compatibility with older recordings.
    vayl_markers = next(
        (s for s in streams if _stream_name(s) == "VaylStim"), None
    )
    if vayl_markers is not None:
        source = "VaylStim (separate stream)"
        messages = _marker_strings(vayl_markers)
    else:
        source = "P013_Task_Markers"
        # Any task marker that parses as JSON with an "event" key is almost
        # certainly a Vayl event coming via the redirected bridge.outlet.
        messages = [m for m in task_markers if m.startswith("{") and '"event"' in m]

    events = []
    for s in messages:
        try:
            events.append(json.loads(s))
        except Exception:  # noqa: BLE001
            events.append({"event": s})
    event_types = [e.get("event", "?") for e in events]
    for needed in ("ramp_start", "overlay_off"):
        count = event_types.count(needed)
        if count:
            report.ok(
                "Task 05 — Vayl",
                f"Vayl:{needed}",
                f"{count} events (via {source})",
            )
        else:
            report.fail(
                "Task 05 — Vayl",
                f"Vayl:{needed}",
                f"event missing from {source} — ramp alignment will be impossible",
            )
    # Spot-check wallTimeMs presence — lets us reconcile Vayl server time
    # with LSL time to sub-ms precision.
    with_wall = [e for e in events if "wallTimeMs" in e]
    if with_wall:
        report.ok(
            "Task 05 — Vayl",
            "Vayl:wallTimeMs",
            f"{len(with_wall)}/{len(events)} events carry server wallTimeMs (via {source})",
        )
    elif events:
        report.warn(
            "Task 05 — Vayl",
            "Vayl:wallTimeMs",
            f"no events carry wallTimeMs (via {source}) — Vayl bridge may be older",
        )

    if vayl_freq is None:
        # VaylStim_Freq is optional now — the default P013 config sets
        # `vayl_emit_frequency_stream: false` and we reconstruct freq
        # analytically from the `ramp_start` JSON payload (stimFreqHz,
        # stimFreqEndHz, durationSeconds). Only treat it as a FAIL if
        # NO Vayl ramp_start event ever made it onto P013_Task_Markers,
        # which means we have no way to reconstruct freq either.
        if any(e.get("event") == "ramp_start" for e in events):
            report.ok(
                "Task 05 — Vayl",
                "VaylStim_Freq stream",
                "absent (expected — freq reconstructable from ramp_start JSON)",
            )
        else:
            report.fail(
                "Task 05 — Vayl",
                "VaylStim_Freq stream",
                "continuous Hz stream absent AND no ramp_start event — "
                "no way to recover frequency-at-time",
            )
            report.suggest(
                "No Vayl ramp_start event on P013_Task_Markers and no "
                "VaylStim_Freq stream — Task 05 either didn't run or the "
                "bridge failed to push markers. Re-check before handoff."
            )
        return

    freq_ts = _timestamps(vayl_freq)
    freq_vals = np.asarray(vayl_freq.get("time_series") or []).flatten()
    n_samples = len(freq_vals)
    srate_nominal = float(_get(vayl_freq, "info", "nominal_srate") or 0)
    if n_samples == 0:
        report.fail(
            "Task 05 — Vayl",
            "VaylStim_Freq samples",
            "stream exists but is empty",
        )
        return
    duration_s = float(freq_ts[-1] - freq_ts[0]) if n_samples > 1 else 0.0
    effective_rate = n_samples / duration_s if duration_s else 0.0
    report.ok(
        "Task 05 — Vayl",
        "VaylStim_Freq samples",
        f"{n_samples} samples over {duration_s:.1f} s "
        f"(~{effective_rate:.1f} Hz; nominal {srate_nominal:.0f})",
    )

    # Frequency range sanity: for our 20->0.5 Hz carrier ramp the effective
    # SSVEP (what VaylStim_Freq reports) should sweep from 40 to 1 Hz. If
    # min/max don't cover that range we likely truncated the recording.
    non_zero = freq_vals[freq_vals > 0]
    if non_zero.size:
        fmin = float(non_zero.min())
        fmax = float(non_zero.max())
        report.ok(
            "Task 05 — Vayl",
            "VaylStim_Freq range",
            f"{fmin:.2f} Hz -> {fmax:.2f} Hz (effective SSVEP)",
        )
    else:
        report.warn(
            "Task 05 — Vayl",
            "VaylStim_Freq range",
            "all samples are 0 — overlay never turned on during recording",
        )

    # Bracket check: do the freq samples actually fall inside
    # [task05_ramp_begin, task05_ramp_end]?
    task_marker_map = dict(zip(task_markers, task_timestamps.tolist()))
    ramp_begin = task_marker_map.get("task05_ramp_begin")
    ramp_end = task_marker_map.get("task05_ramp_end")
    if ramp_begin is not None and ramp_end is not None and n_samples >= 2:
        inside = (freq_ts >= ramp_begin) & (freq_ts <= ramp_end)
        pct = 100.0 * inside.sum() / n_samples
        if pct >= 50:
            report.ok(
                "Task 05 — Vayl",
                "Freq-vs-ramp alignment",
                f"{pct:.0f}% of freq samples fall inside "
                f"[task05_ramp_begin, task05_ramp_end]",
            )
        else:
            report.warn(
                "Task 05 — Vayl",
                "Freq-vs-ramp alignment",
                f"only {pct:.0f}% of freq samples fall inside the ramp window — "
                "LSL clock sync may be off",
            )
            report.suggest(
                "Freq stream timestamps are mostly outside the ramp window — "
                "check whether LabRecorder was started before the Vayl bridge "
                "had finished handshaking; consider re-syncing clocks."
            )


def _check_hardware_streams(
    streams: list[dict], report: VerificationReport
) -> None:
    # Pass: list every stream in the file with type / srate / sample count so
    # the RA can scan the full inventory at a glance.
    for s in streams:
        name = _stream_name(s)
        stype = _stream_type(s) or "?"
        srate = float(_get(s, "info", "nominal_srate") or 0)
        ts = s.get("time_stamps")
        n_samples = len(ts) if ts is not None else 0
        report.ok(
            "Hardware",
            name,
            f"type={stype}, {n_samples} samples, nominal {srate:.0f} Hz",
        )

    # Warn if there's no EEG-type stream at all.
    if not any(_stream_type(s).lower() == "eeg" for s in streams):
        report.warn(
            "Hardware",
            "type=EEG",
            "no EEG-type stream in file — EEG / CGX was not recorded",
        )
        report.suggest(
            "No EEG-type stream found. LabRecorder must have the BrainVision "
            "(or CGX AIM-2) stream ticked before recording."
        )

    # Sentiometer: match by stream name (brand is stable).
    sent = next(
        (s for s in streams if _stream_name(s) in SENTIOMETER_STREAM_NAMES),
        None,
    )
    if sent is None:
        report.warn(
            "Hardware",
            "Sentiometer",
            "Sentiometer stream not recorded",
        )
        report.suggest(
            "Sentiometer stream missing — confirm its laptop is online, tick "
            "the stream in LabRecorder, and make sure src/sentiometer/ is "
            "streaming before recording starts."
        )


# ----- Glue ------------------------------------------------------------------


def _find_xdf(path_arg: str | None) -> Path:
    if path_arg:
        p = Path(path_arg).expanduser()
        if not p.exists():
            raise SystemExit(f"XDF file not found: {p}")
        return p
    if not DEFAULT_SAMPLE_DIR.exists():
        raise SystemExit(
            f"No sampledata/ directory at {DEFAULT_SAMPLE_DIR}. "
            "Pass a path explicitly or create sampledata/ and drop an .xdf in it."
        )
    candidates = sorted(DEFAULT_SAMPLE_DIR.glob("*.xdf"))
    if not candidates:
        raise SystemExit(
            f"No .xdf files in {DEFAULT_SAMPLE_DIR}. Pass a path explicitly."
        )
    if len(candidates) > 1:
        names = "\n  ".join(str(c.name) for c in candidates)
        raise SystemExit(
            f"Multiple .xdf files in {DEFAULT_SAMPLE_DIR}:\n  {names}\n"
            "Pass the one you want on the CLI to disambiguate."
        )
    return candidates[0]


def _render(path: Path, report: VerificationReport) -> int:
    """Print the report as Rich tables. Returns an exit code."""
    console = Console()
    console.print(
        Panel(f"[bold cyan]XDF verification[/bold cyan]\n{path}", expand=False)
    )

    # Group rows by category in insertion order.
    groups: dict[str, list[CheckRow]] = {}
    for row in report.rows:
        groups.setdefault(row.category, []).append(row)

    for cat, rows in groups.items():
        table = Table(title=cat, show_lines=False, expand=False)
        table.add_column("Check", style="white", no_wrap=True)
        table.add_column("Status", justify="center")
        table.add_column("Detail", style="dim")
        for r in rows:
            style = {"OK": "green", "WARN": "yellow", "FAIL": "red"}[r.status]
            table.add_row(
                r.name, f"[{style}]{r.status}[/{style}]", r.detail
            )
        console.print(table)
        console.print()

    # Summary
    total = len(report.rows)
    n_ok = sum(1 for r in report.rows if r.status == "OK")
    n_warn = sum(1 for r in report.rows if r.status == "WARN")
    n_fail = sum(1 for r in report.rows if r.status == "FAIL")
    summary = (
        f"[green]{n_ok} OK[/green]  [yellow]{n_warn} WARN[/yellow]  "
        f"[red]{n_fail} FAIL[/red]  (of {total} checks)"
    )
    console.print(Panel(summary, title="Summary", expand=False))

    if report.suggestions:
        console.print(
            Panel(
                "\n".join(f"- {s}" for s in report.suggestions),
                title="Suggested edits for Claude Code",
                expand=False,
                border_style="yellow",
            )
        )

    return 0 if n_fail == 0 else 1


def verify(
    path: Path, streams: list[dict] | None = None, header: dict | None = None
) -> VerificationReport:
    """Load *path* and run every check. Returns the populated report.

    If *streams* and *header* are provided (e.g. by a caller that already
    parsed the XDF), they're reused instead of re-parsing the file.
    """
    report = VerificationReport()

    if streams is None or header is None:
        streams, header = pyxdf.load_xdf(str(path))
    report.ok(
        "File",
        "pyxdf.load_xdf",
        f"{len(streams)} streams, XDF version "
        f"{_get(header, 'info', 'version')}",
    )

    task_marker_stream = next(
        (s for s in streams if _stream_name(s) == "P013_Task_Markers"),
        None,
    )
    if task_marker_stream is None:
        report.fail(
            "File",
            "P013_Task_Markers stream",
            "core task marker stream absent — cannot verify any task coverage",
        )
        report.suggest(
            "LabRecorder did not capture P013_Task_Markers. Confirm the stream "
            "is visible in LabRecorder's list and ticked before pressing "
            "Start recording."
        )
        return report

    markers = _marker_strings(task_marker_stream)
    timestamps = _timestamps(task_marker_stream)
    report.ok(
        "File",
        "P013_Task_Markers stream",
        f"{len(markers)} markers over "
        f"{float(timestamps[-1] - timestamps[0]):.1f} s"
        if len(markers) > 1
        else f"{len(markers)} markers",
    )

    _check_session_bookends(markers, report)
    for key, spec in TASK_SPECS.items():
        _check_task_markers(markers, spec, report)
    _check_vayl_streams(streams, markers, timestamps, report)
    _check_hardware_streams(streams, report)

    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "path",
        nargs="?",
        help="Path to .xdf (defaults to the single .xdf in sampledata/)",
    )
    parser.add_argument(
        "--no-timeline",
        action="store_true",
        help="Skip the chronological marker timeline at the bottom.",
    )
    parser.add_argument(
        "--timeline-summary-only",
        action="store_true",
        help="Show only the phase summary, not the per-marker rows.",
    )
    args = parser.parse_args(argv)

    try:
        xdf_path = _find_xdf(args.path)
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 2

    # Parse the XDF once and share it between the verifier and timeline.
    streams, header = pyxdf.load_xdf(str(xdf_path))
    report = verify(xdf_path, streams=streams, header=header)
    exit_code = _render(xdf_path, report)

    if not args.no_timeline:
        # Late import so verify_xdf.py remains usable even if
        # timeline_xdf.py is removed / renamed in the future.
        from timeline_xdf import render_timeline  # noqa: PLC0415

        console = Console()
        console.print()
        console.print(
            Panel(
                f"[bold cyan]Session timeline (Pacific)[/bold cyan]\n{xdf_path}",
                expand=False,
            )
        )
        render_timeline(
            streams,
            xdf_path,
            console=console,
            show_details=not args.timeline_summary_only,
        )

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
