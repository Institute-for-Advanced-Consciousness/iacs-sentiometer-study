"""Task 05: SSVEP Frequency Ramp-Down (orchestration only).

**Stimulation is delegated to the Vayl desktop app.** Vayl renders a
full-screen pattern-reversal checkerboard overlay directly on the GPU, so
our task code is a thin orchestrator that (a) shows participant-facing
instruction and completion screens in Pygame, (b) talks to Vayl over its
localhost HTTP API via :mod:`vayl_lsl_bridge`, and (c) emits coarse
boundary markers on the shared ``P013_Task_Markers`` stream. The
fine-grained frequency tracking lives in the two LSL streams Vayl's bridge
creates automatically:

* ``VaylStim`` — marker stream with JSON events (``ramp_start``,
  ``overlay_off``, including server-side ``wallTimeMs``).
* ``VaylStim_Freq`` — continuous float32 stream at 250 Hz reporting the
  current effective SSVEP frequency.

LabRecorder picks up both of those alongside ``P013_Task_Markers``, the
EEG stream, the CGX AIM-2 stream, and the Sentiometer stream into the
session's XDF file.

**Carrier vs. effective frequency.** Pattern reversal produces two visual
events per carrier cycle (black -> white and white -> black), so the
effective SSVEP frequency is ``2 * carrier_hz``. The config file stores
carriers (``20.0 -> 0.5`` by default); the bridge reports effective Hz on
its own streams; this task's P013 markers only emit coarse boundaries and
do not duplicate the fine-grained frequency data.

Like Tasks 01-04, side-effecting I/O (Pygame display, sleep) is bundled
into a ``TaskIO`` dataclass, and the Vayl bridge is passed in via
``run()``'s ``bridge`` parameter so tests can inject a mock implementation
without touching localhost HTTP or real LSL streams.
"""

from __future__ import annotations

import csv
import importlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pylsl import StreamOutlet, local_clock

from tasks.common.config import get_task_config, load_session_config
from tasks.common.lsl_markers import create_demo_outlet, send_marker

# The task directory name starts with a digit (``05_ssvep``), which is not
# a valid Python identifier, so we cannot use ``from . import ...`` for the
# sibling Vayl bridge module. importlib resolves it via the file system.
_vayl_mod = importlib.import_module("tasks.05_ssvep.vayl_lsl_bridge")
VaylBridge = _vayl_mod.VaylBridge

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = REPO_ROOT / "data"

TASK_NAME = "task05_ssvep"

INSTRUCTIONS_TEXT = (
    "In this task, you will see a flickering pattern on the screen. "
    "Please keep your eyes open and focused on the center of the screen "
    "at all times. The flickering will change speed throughout the task. "
    "You do not need to press any buttons.\n\n"
    "The task takes about 5 minutes.\n\n"
    "Press spacebar when you are ready to begin."
)

COMPLETION_TEXT = (
    "The flickering task is complete.\n\n"
    "Press spacebar to continue."
)


# ----- I/O bundle (mockable) -------------------------------------------------


@dataclass
class TaskIO:
    """Side-effecting callables for Task 05 (Pygame display + sleep)."""

    show_text_and_wait: Callable[[str, str], None]
    """Show *text* on a gray screen; block until *wait_key* is pressed."""

    iconify: Callable[[], None]
    """Minimize the Pygame window so the Vayl overlay is visible."""

    restore: Callable[[], None]
    """Re-present the Pygame window after the Vayl overlay fades out."""

    check_escape: Callable[[], None]
    """Raise :class:`EscapePressedError` if Escape was pressed."""

    wait: Callable[[float], None]
    """Sleep for *seconds* (used by the demo fallback when Vayl is down)."""


def _build_pygame_io() -> tuple[TaskIO, Callable[[], None]]:
    """Construct a Pygame-backed ``TaskIO`` for the participant-facing screens.

    We intentionally only use Pygame (not PsychoPy) for the instruction and
    completion screens so there is exactly one display-framework stack
    alongside Vayl -- mixing Pygame, PsychoPy, and Vayl's overlay would be
    asking for window-focus bugs.
    """
    import pygame  # noqa: PLC0415

    pygame.init()
    screen = pygame.display.set_mode((1280, 720))
    pygame.display.set_caption("IACS Task 05 -- SSVEP Frequency Ramp")
    font = pygame.font.SysFont(None, 48)
    clock = pygame.time.Clock()

    def show_text_and_wait(text: str, wait_key: str) -> None:
        waiting = True
        while waiting:
            screen.fill((40, 40, 40))
            y = 720 // 2 - 200
            for line in text.split("\n"):
                surf = font.render(line, True, (230, 230, 230))
                rect = surf.get_rect(center=(1280 // 2, y))
                screen.blit(surf, rect)
                y += 56
            pygame.display.flip()
            for ev in pygame.event.get():
                if ev.type == pygame.KEYDOWN:
                    if wait_key == "space" and ev.key == pygame.K_SPACE:
                        waiting = False
                    elif ev.key == pygame.K_ESCAPE:
                        from tasks.common.display import (  # noqa: PLC0415
                            EscapePressedError,
                        )

                        raise EscapePressedError
            clock.tick(30)

    def iconify() -> None:
        pygame.display.iconify()

    def restore() -> None:
        # Re-setting the display mode brings the window back to the front
        # on most platforms after an iconify.
        pygame.display.set_mode((1280, 720))

    def check_escape() -> None:
        for ev in pygame.event.get():
            if ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE:
                from tasks.common.display import EscapePressedError  # noqa: PLC0415

                raise EscapePressedError

    def wait(seconds: float) -> None:
        pygame.time.wait(int(seconds * 1000))

    def cleanup() -> None:
        pygame.quit()

    io = TaskIO(
        show_text_and_wait=show_text_and_wait,
        iconify=iconify,
        restore=restore,
        check_escape=check_escape,
        wait=wait,
    )
    return io, cleanup


# ----- Behavioral log --------------------------------------------------------


_LOG_FIELDS = ("event", "timestamp", "details")


def _save_session_log(
    entries: list[dict],
    participant_id: str,
    output_dir: Path,
) -> Path:
    """Write *entries* to ``output_dir/{participant_id}/task05_session_log.csv``.

    Minimal three-column schema (event, timestamp, details). ``details``
    is a free-form ``key=value; ...`` string so we can record the Vayl API
    response's ``wallTimeMs`` without introducing per-event columns.
    """
    out = output_dir / participant_id
    out.mkdir(parents=True, exist_ok=True)
    log_path = out / "task05_session_log.csv"
    with open(log_path, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(_LOG_FIELDS)
        for e in entries:
            details_parts = [
                f"{k}={v}"
                for k, v in e.items()
                if k not in ("event", "timestamp")
            ]
            writer.writerow(
                [
                    e.get("event", ""),
                    f"{e.get('timestamp', 0.0):.6f}",
                    "; ".join(details_parts),
                ]
            )
    return log_path


# ----- Main entry point ------------------------------------------------------


def run(
    outlet: StreamOutlet | None = None,
    *,
    config: dict | None = None,
    participant_id: str = "DEMO",
    demo: bool = False,
    io: TaskIO | None = None,
    bridge: Any = None,
    output_dir: Path | None = None,
) -> Path:
    """Run the SSVEP ramp-down task end-to-end.

    Parameters
    ----------
    outlet:
        Shared session marker outlet. If ``None`` a temporary demo outlet
        is created and torn down at the end.
    config:
        Per-task config dict (the ``task05_ssvep`` section of
        ``session_defaults.yaml``). If ``None``, loaded from the shipped
        defaults.
    participant_id:
        Used to name the behavioral-log subdirectory.
    demo:
        If ``True``: 10 s ramp (carrier 20 -> 0.5 Hz, effective 40 -> 1 Hz).
        If Vayl is not reachable in demo mode the ramp is skipped and the
        timing is simulated with ``io.wait`` so the task still exercises
        all seven boundary markers.
    io:
        Headless ``TaskIO``. If ``None``, a Pygame-backed bundle is built.
    bridge:
        Injected :class:`VaylBridge` (or compatible mock). If ``None`` a
        real one is constructed from the config's ``vayl_api_url`` and
        ``vayl_lsl_stream_name``. Tests pass a mock so no HTTP or real
        LSL outlet is touched.
    output_dir:
        Root directory for the behavioral log. Defaults to ``data/``.
    """
    if config is None:
        config = get_task_config(load_session_config(), TASK_NAME)
    else:
        config = dict(config)

    if demo:
        config["ramp_duration_s"] = 10

    if output_dir is None:
        output_dir = DATA_DIR

    own_outlet = False
    if outlet is None:
        outlet = create_demo_outlet()
        own_outlet = True

    cleanup: Callable[[], None] = lambda: None  # noqa: E731
    if io is None:
        io, cleanup = _build_pygame_io()

    if bridge is None:
        bridge = VaylBridge(
            api_url=config["vayl_api_url"],
            lsl_stream_name=config["vayl_lsl_stream_name"],
        )

    entries: list[dict] = []

    try:
        send_marker(outlet, "task05_start")
        entries.append(
            {"event": "task_start", "timestamp": local_clock()}
        )

        # ----- Instructions -----
        send_marker(outlet, "task05_instructions_start")
        io.show_text_and_wait(INSTRUCTIONS_TEXT, "space")
        send_marker(outlet, "task05_instructions_end")

        # ----- Vayl connectivity check -----
        vayl_available = False
        try:
            status = bridge.status()
            vayl_available = True
            entries.append(
                {
                    "event": "vayl_status_ok",
                    "timestamp": local_clock(),
                    "version": str(status.get("version", "?")),
                }
            )
        except (ConnectionError, RuntimeError) as exc:
            if not demo:
                raise RuntimeError(
                    f"Vayl desktop app is not reachable at "
                    f"{config['vayl_api_url']}. Start the Vayl app on the "
                    f"stimulus computer and try again. Underlying error: {exc}"
                ) from exc
            print("DEMO MODE: Vayl not connected, simulating ramp timing.")
            entries.append(
                {
                    "event": "vayl_status_failed_demo_fallback",
                    "timestamp": local_clock(),
                    "error": str(exc),
                }
            )

        # Minimize the Pygame window so the Vayl overlay (rendered on the
        # GPU above everything) is fully visible to the participant.
        io.iconify()

        # ----- Ramp -----
        start_hz = float(config["carrier_start_hz"])
        end_hz = float(config["carrier_end_hz"])
        duration_s = float(config["ramp_duration_s"])

        send_marker(outlet, "task05_ramp_begin")
        if vayl_available:
            ramp_result = bridge.start_ramp(start_hz, end_hz, duration_s)
            timing = ramp_result.get("timing", {}) if isinstance(ramp_result, dict) else {}
            entries.append(
                {
                    "event": "ramp_start",
                    "timestamp": local_clock(),
                    "carrier_start_hz": start_hz,
                    "carrier_end_hz": end_hz,
                    "effective_start_hz": start_hz * 2,
                    "effective_end_hz": end_hz * 2,
                    "duration_s": duration_s,
                    "wall_time_ms": timing.get("wallTimeMs", ""),
                }
            )
            bridge.wait_for_ramp(duration_s)
        else:
            io.wait(duration_s)

        send_marker(outlet, "task05_ramp_end")
        entries.append({"event": "ramp_end", "timestamp": local_clock()})

        # ----- Fade out -----
        if vayl_available:
            off_result = bridge.turn_off()
            off_timing = off_result.get("timing", {}) if isinstance(off_result, dict) else {}
            entries.append(
                {
                    "event": "overlay_off",
                    "timestamp": local_clock(),
                    "wall_time_ms": off_timing.get("wallTimeMs", ""),
                }
            )

        send_marker(outlet, "task05_overlay_off")

        # ----- Restore Pygame window and show completion -----
        io.restore()
        io.show_text_and_wait(COMPLETION_TEXT, "space")

        log_path = _save_session_log(entries, participant_id, output_dir)

        send_marker(outlet, "task05_end")
        entries.append({"event": "task_end", "timestamp": local_clock()})

        log.info("Task 05 complete: %s", log_path)
        return log_path
    finally:
        cleanup()
        if own_outlet:
            del outlet
