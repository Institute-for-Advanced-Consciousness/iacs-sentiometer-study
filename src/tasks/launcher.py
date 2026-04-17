"""Session launcher for the P013 task suite (terminal, Rich-formatted).

Run as:

    uv run python -m tasks.launcher --participant-id P001

This is the **only** supported entry point for a live session. It:

1. Loads ``config/session_defaults.yaml`` and prints a Rich summary table of
   every configurable parameter for all five tasks.
2. Offers to open the config in the system editor (``$EDITOR`` on Unix,
   ``notepad`` on Windows) so the RA can tweak parameters for this session.
3. Creates the ``P013_Task_Markers`` LSL outlet for the whole session.
4. Runs a pre-flight checklist: participant ID present, outlet created,
   LabRecorder confirmed (manual), EEG stream visible, Sentiometer stream
   visible, Vayl app reachable (for Task 05). Missing optional streams warn
   but do not block.
5. Writes ``data/{participant_id}/session_log.json`` with a config snapshot,
   per-task start/end timestamps, and system info.
6. Sends a ``session_start`` marker, runs Tasks 01 -> 05 in order, and sends
   ``session_end`` at the end. Each task emits its own ``task0N_start`` /
   ``task0N_end`` markers from inside its ``run()`` -- the launcher does
   **not** duplicate those.
7. Catches ``KeyboardInterrupt`` (Ctrl+C) and on abort sends a
   ``session_abort`` marker, records the in-progress task name, and persists
   the partial session log.

CLI flags:

* ``--participant-id P001``   Participant ID. Prompted interactively if omitted.
* ``--demo``                  Pass ``demo=True`` to every task and skip the
                              LSL-stream-presence checks in the pre-flight.
* ``--skip-to N``             Start from task ``N`` (1-5). Tasks 1..N-1 are
                              marked ``skipped`` in the session log. Useful
                              for recovery after a mid-session crash.
* ``--config PATH``           Override the session config YAML location.

The core :func:`run_session` function is exposed for tests with an
``interactive=False`` mode (no prompts, no Rich stdin reads) and an injectable
``task_runner`` so the full orchestration can be exercised headlessly
without actually invoking the five task modules.
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import platform
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import click
from pylsl import StreamOutlet, resolve_byprop
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from tasks.common.config import (
    DEFAULT_SESSION_CONFIG_PATH,
    get_task_config,
    load_session_config,
)
from tasks.common.lsl_markers import create_session_outlet, send_marker

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_ROOT = REPO_ROOT / "data"

# (config section key, importable module path). The launcher iterates over
# this list in order and feeds each entry into importlib.import_module, which
# correctly handles the digit-prefixed directory names.
TASK_ORDER: list[tuple[str, str]] = [
    ("task01_oddball", "tasks.01_oddball.task"),
    ("task02_rgb_illuminance", "tasks.02_rgb_illuminance.task"),
    ("task03_backward_masking", "tasks.03_backward_masking.task"),
    ("task04_mind_state", "tasks.04_mind_state.task"),
    ("task05_ssvep", "tasks.05_ssvep.task"),
]

TASK_DISPLAY_NAMES: dict[str, str] = {
    "task01_oddball": "Task 01 -- Auditory Oddball (P300)",
    "task02_rgb_illuminance": "Task 02 -- RGB Illuminance",
    "task03_backward_masking": "Task 03 -- Backward Masking",
    "task04_mind_state": "Task 04 -- Mind-State Switching",
    "task05_ssvep": "Task 05 -- SSVEP Ramp-Down",
}

console = Console()


# ----- Display helpers -------------------------------------------------------


def _display_config_summary(config: dict) -> None:
    """Render a Rich table per task showing every parameter + current value."""
    for section_key, section in config.items():
        if section_key == "session":
            continue
        display = TASK_DISPLAY_NAMES.get(section_key, section_key)
        table = Table(
            title=display, show_header=True, header_style="bold cyan", expand=False
        )
        table.add_column("Parameter", style="white")
        table.add_column("Value", style="yellow")
        for key, value in section.items():
            table.add_row(key, str(value))
        console.print(table)
        console.print()


def _open_editor(path: Path) -> None:
    """Open *path* in the system editor ($EDITOR on Unix, notepad on Windows)."""
    editor = os.environ.get("EDITOR")
    if editor is None:
        editor = "notepad" if platform.system() == "Windows" else "nano"
    try:
        subprocess.run([editor, str(path)], check=False)
    except FileNotFoundError:
        console.print(
            f"[red]Could not launch editor '{editor}'.[/red] "
            f"Edit {path} manually and press Enter to continue."
        )
        input()


# ----- Pre-flight checks -----------------------------------------------------


def _resolve_by_type(stream_type: str, timeout: float = 2.0) -> bool:
    """Return True if any LSL stream with *stream_type* is visible on the network."""
    try:
        streams = resolve_byprop("type", stream_type, minimum=1, timeout=timeout)
        return len(streams) > 0
    except Exception as exc:
        log.debug("LSL type resolve failed for %s: %s", stream_type, exc)
        return False


def _resolve_by_name(stream_name: str, timeout: float = 2.0) -> bool:
    """Return True if any LSL stream with *stream_name* is visible on the network."""
    try:
        streams = resolve_byprop("name", stream_name, minimum=1, timeout=timeout)
        return len(streams) > 0
    except Exception as exc:
        log.debug("LSL name resolve failed for %s: %s", stream_name, exc)
        return False


def _check_vayl_reachable(api_url: str = "http://127.0.0.1:9471") -> bool:
    """Return True if the Vayl desktop app's API is responding on localhost."""
    try:
        urllib.request.urlopen(f"{api_url}/api/status", timeout=2)
        return True
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


def _vayl_stop_overlay(api_url: str = "http://127.0.0.1:9471") -> bool:
    """Best-effort POST to Vayl's overlay-off endpoint.

    Used by the GUI to make absolutely sure the desktop isn't strobing
    before the session starts (Task 01 / 02 / 03 / 04 should never see a
    live Vayl overlay behind them). Returns True on HTTP 2xx, False
    otherwise. Idempotent on the Vayl side.
    """
    req = urllib.request.Request(f"{api_url}/api/overlay/off", method="POST")
    try:
        with urllib.request.urlopen(req, timeout=2) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


def _symbol(ok: bool, warn_only: bool = False) -> str:
    if ok:
        return "[green]OK[/green]"
    return "[yellow]WARN[/yellow]" if warn_only else "[red]FAIL[/red]"


def _preflight_checklist(
    participant_id: str,
    outlet_created: bool,
    demo: bool = False,
) -> None:
    """Run the pre-flight checklist. Warnings are informational only."""
    table = Table(
        title="Pre-Flight Checklist",
        show_header=True,
        header_style="bold magenta",
        expand=False,
    )
    table.add_column("Check", style="white")
    table.add_column("Status", justify="center")
    table.add_column("Notes", style="dim")

    table.add_row(
        "Participant ID",
        _symbol(bool(participant_id)),
        participant_id or "(none)",
    )
    table.add_row(
        "P013 marker outlet",
        _symbol(outlet_created),
        f"source_id=P013_{participant_id}" if outlet_created else "",
    )

    if demo:
        table.add_row("EEG stream", "[dim]skipped[/dim]", "demo mode")
        table.add_row("Sentiometer stream", "[dim]skipped[/dim]", "demo mode")
        table.add_row("Vayl app", "[dim]skipped[/dim]", "demo mode")
    else:
        console.print("[dim]Scanning network for EEG, Sentiometer, Vayl...[/dim]")
        eeg_ok = _resolve_by_type("EEG", timeout=1.5)
        table.add_row(
            "EEG stream (type=EEG)",
            _symbol(eeg_ok, warn_only=True),
            "optional, does not block",
        )
        sent_ok = _resolve_by_name("IACS_Sentiometer", timeout=1.5)
        table.add_row(
            "Sentiometer stream",
            _symbol(sent_ok, warn_only=True),
            "IACS_Sentiometer; optional, does not block",
        )
        vayl_ok = _check_vayl_reachable()
        table.add_row(
            "Vayl app (localhost:9471)",
            _symbol(vayl_ok, warn_only=True),
            "needed for Task 05 only",
        )

    console.print(table)


# ----- Session log -----------------------------------------------------------


def _get_system_info() -> dict:
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "system": platform.system(),
    }


def _save_session_log(log_data: dict, log_path: Path) -> Path:
    """Write the session log dict to disk as JSON."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "w") as fh:
        json.dump(log_data, fh, indent=2, default=str)
    return log_path


# ----- Task execution --------------------------------------------------------


def _run_task(
    task_name: str,
    module_path: str,
    outlet: StreamOutlet,
    session_config: dict,
    participant_id: str,
    demo: bool,
    data_root: Path,
) -> tuple[str, str | None]:
    """Load a task module via importlib and call its ``run()``.

    Returns ``(status, error_message)`` where status is ``"completed"`` or
    ``"failed"``. KeyboardInterrupt propagates up to the orchestrator so the
    session-abort path can fire.
    """
    task_module = importlib.import_module(module_path)
    task_config = get_task_config(session_config, task_name)
    try:
        task_module.run(
            outlet=outlet,
            config=task_config,
            participant_id=participant_id,
            demo=demo,
            output_dir=data_root,
        )
        return ("completed", None)
    except KeyboardInterrupt:
        raise
    except Exception as exc:
        log.exception("Task %s failed", task_name)
        return ("failed", f"{type(exc).__name__}: {exc}")


TaskRunner = Callable[
    [str, str, StreamOutlet, dict, str, bool, Path],
    tuple[str, str | None],
]


# ----- Core session orchestrator --------------------------------------------


def run_session(
    participant_id: str,
    *,
    demo: bool = False,
    skip_to: int = 1,
    config_path: Path | None = None,
    data_root: Path | None = None,
    interactive: bool = True,
    task_runner: TaskRunner | None = None,
    outlet: StreamOutlet | None = None,
) -> dict:
    """Run a full session from pre-flight through post-task cleanup.

    Parameters
    ----------
    participant_id:
        Participant identifier (e.g. ``"P001"``). Used as the source ID
        suffix on the marker stream and as the data-directory name.
    demo:
        If ``True``, pass ``demo=True`` into every task and skip the LSL
        stream-presence checks.
    skip_to:
        Start from task number ``skip_to`` (1-indexed; default 1). Tasks
        1..skip_to-1 are marked ``"skipped"`` in the session log.
    config_path:
        Override for the session config YAML path. Defaults to the shipped
        ``config/session_defaults.yaml``.
    data_root:
        Root data directory. Defaults to ``data/`` at the repo root. The
        session log is written to ``data_root/{participant_id}/session_log.json``
        and each task's behavioral CSV goes under the same directory.
    interactive:
        If ``True`` (CLI default), show prompts, Rich tables, and wait for
        Enter between tasks. If ``False`` (tests), no stdin is read and no
        editor is launched.
    task_runner:
        Optional callable with the signature ``(task_name, module_path,
        outlet, session_config, participant_id, demo, data_root) ->
        (status, error)``. Defaults to :func:`_run_task`. Tests inject a
        mock so the launcher can be exercised without actually importing
        and running the five task modules.
    outlet:
        Optional pre-created LSL outlet. If ``None``, the launcher creates
        its own via :func:`create_session_outlet` and releases it at the end.

    Returns
    -------
    dict
        The final session log, whether the session completed or aborted.
    """
    if task_runner is None:
        task_runner = _run_task
    if config_path is None:
        config_path = DEFAULT_SESSION_CONFIG_PATH
    if data_root is None:
        data_root = DEFAULT_DATA_ROOT
    if not 1 <= skip_to <= len(TASK_ORDER):
        raise ValueError(
            f"skip_to must be in [1, {len(TASK_ORDER)}]; got {skip_to}"
        )

    session_config = load_session_config(config_path)

    if interactive:
        console.print(
            Panel(
                f"[bold cyan]P013 Session -- {participant_id}[/bold cyan]",
                expand=False,
            )
        )
        _display_config_summary(session_config)
        if Confirm.ask("Edit any parameters?", default=False):
            _open_editor(config_path)
            session_config = load_session_config(config_path)
            console.print("[green]Config reloaded.[/green]\n")

    own_outlet = False
    if outlet is None:
        outlet = create_session_outlet(participant_id)
        own_outlet = True
    if interactive:
        console.print(
            f"[green]Created LSL outlet:[/green] P013_Task_Markers "
            f"[dim](source_id=P013_{participant_id})[/dim]"
        )

    if interactive:
        _preflight_checklist(participant_id, outlet_created=True, demo=demo)
        if not demo:
            Prompt.ask(
                "\nIs LabRecorder running and recording? "
                "[dim](press Enter to confirm)[/dim]",
                default="",
                show_default=False,
            )
        Prompt.ask(
            "\n[bold green]Press Enter to begin session[/bold green]",
            default="",
            show_default=False,
        )

    data_dir = data_root / participant_id
    data_dir.mkdir(parents=True, exist_ok=True)
    session_log_path = data_dir / "session_log.json"

    now = datetime.now()
    session_log: dict[str, Any] = {
        "participant_id": participant_id,
        "session_date": now.date().isoformat(),
        "start_time": now.isoformat(timespec="seconds"),
        "end_time": None,
        "status": "running",
        "demo": demo,
        "skip_to": skip_to,
        "config_snapshot": session_config,
        "tasks": {},
        "abort_reason": None,
        "aborted_during": None,
        "system_info": _get_system_info(),
    }
    _save_session_log(session_log, session_log_path)

    send_marker(outlet, "session_start")

    current_task: str | None = None
    try:
        for i, (task_name, module_path) in enumerate(TASK_ORDER, start=1):
            if i < skip_to:
                session_log["tasks"][task_name] = {
                    "status": "skipped",
                    "reason": f"skip_to={skip_to}",
                }
                _save_session_log(session_log, session_log_path)
                continue

            current_task = task_name
            display = TASK_DISPLAY_NAMES[task_name]
            if interactive:
                console.print(Panel(f"[bold]{display}[/bold]", expand=False))

            task_start = datetime.now().isoformat(timespec="seconds")
            session_log["tasks"][task_name] = {
                "status": "running",
                "start": task_start,
                "end": None,
            }
            _save_session_log(session_log, session_log_path)

            status, error = task_runner(
                task_name,
                module_path,
                outlet,
                session_config,
                participant_id,
                demo,
                data_root,
            )

            task_end = datetime.now().isoformat(timespec="seconds")
            session_log["tasks"][task_name].update(
                {"status": status, "end": task_end}
            )
            if error:
                session_log["tasks"][task_name]["error"] = error
            _save_session_log(session_log, session_log_path)

            if interactive and i < len(TASK_ORDER):
                completed = i - skip_to + 1
                remaining = len(TASK_ORDER) - i
                Prompt.ask(
                    f"\n[bold green]{display} complete.[/bold green] "
                    f"[dim]({completed} done, {remaining} remaining.)[/dim] "
                    "Press Enter to continue",
                    default="",
                    show_default=False,
                )

        send_marker(outlet, "session_end")
        session_log["status"] = "completed"
        session_log["end_time"] = datetime.now().isoformat(timespec="seconds")
        if interactive:
            console.print(
                Panel(
                    "[bold green]Session complete.[/bold green]",
                    expand=False,
                )
            )
    except KeyboardInterrupt:
        send_marker(outlet, "session_abort")
        session_log["status"] = "aborted"
        session_log["end_time"] = datetime.now().isoformat(timespec="seconds")
        session_log["abort_reason"] = "KeyboardInterrupt (Ctrl+C)"
        session_log["aborted_during"] = current_task
        if current_task and current_task in session_log["tasks"]:
            session_log["tasks"][current_task]["status"] = "aborted"
            session_log["tasks"][current_task]["end"] = session_log["end_time"]
        if interactive:
            console.print(
                f"\n[red]Session aborted during {current_task}.[/red]"
            )
    finally:
        _save_session_log(session_log, session_log_path)
        if own_outlet:
            del outlet

    return session_log


# ----- CLI entry point -------------------------------------------------------


@click.command()
@click.option(
    "--participant-id",
    "-p",
    default=None,
    help="Participant ID (e.g., P001). Prompted interactively if omitted.",
)
@click.option(
    "--demo",
    is_flag=True,
    default=False,
    help="Run every task in demo mode and skip stream presence checks.",
)
@click.option(
    "--skip-to",
    type=int,
    default=1,
    help="Start from task N (1-5). Tasks 1..N-1 are marked skipped. Crash recovery.",
)
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Override session config YAML path.",
)
def main(
    participant_id: str | None,
    demo: bool,
    skip_to: int,
    config_path: Path | None,
) -> None:
    """IACS P013 session launcher."""
    if not 1 <= skip_to <= len(TASK_ORDER):
        console.print(
            f"[red]--skip-to must be in [1, {len(TASK_ORDER)}], got {skip_to}[/red]"
        )
        sys.exit(1)

    if participant_id is None:
        participant_id = Prompt.ask("Participant ID", default="P001")

    run_session(
        participant_id=participant_id,
        demo=demo,
        skip_to=skip_to,
        config_path=config_path,
        interactive=True,
    )


if __name__ == "__main__":
    main()
