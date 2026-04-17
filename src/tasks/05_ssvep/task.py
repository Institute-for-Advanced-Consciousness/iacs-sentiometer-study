"""Task 05: SSVEP Frequency Ramp-Down (orchestration only).

**Stimulation is delegated to the Vayl desktop app.** Vayl renders a
full-screen pattern-reversal checkerboard overlay directly on the GPU, so
our task code is a thin orchestrator that (a) shows participant-facing
instruction and completion screens in Pygame, (b) talks to Vayl over its
localhost HTTP API via :mod:`vayl_lsl_bridge`, and (c) emits coarse
boundary markers on the shared ``P013_Task_Markers`` stream.

**Marker routing (updated bridge).** The new :mod:`vayl_lsl_bridge`
accepts an externally-created marker outlet, so Vayl's
``ramp_start`` / ``ramp_stop`` / ``overlay_off`` JSON events are pushed
directly into the same shared ``P013_Task_Markers`` outlet used by every
other task — no separate ``VaylStim`` stream is advertised. The bridge's
250 Hz effective-frequency stream (``VaylStim_Freq``) is a float channel
incompatible with a string marker outlet, so it is only created when
``vayl_emit_frequency_stream: true`` is set in the session config;
otherwise analysis reconstructs effective Hz analytically from the
``ramp_start`` JSON payload (which carries ``stimFreqHz``,
``stimFreqEndHz``, ``durationSeconds``, and the sub-ms ``wallTimeMs``).

**LAP protocol flags.** Each ramp is now configured at the HTTP level
with the three flags Vayl's Labelled Amplitude Protocol expects:
``labOpaque`` (unit-amplitude LAP bypass, required for pattern-reversal
SSVEP), ``checkerboardEnabled`` (force the checkerboard visual), and
``checkerSize`` (pixels per square). Defaults live under the
``task05_ssvep:`` block of ``config/session_defaults.yaml`` and are
passed through the bridge's ``start_ramp`` kwargs.

**Carrier vs. effective frequency.** Pattern reversal produces two
visual events per carrier cycle (black -> white and white -> black),
so the effective SSVEP frequency is ``2 * carrier_hz``. The config
file stores carriers (``20.0 -> 0.5`` by default); the bridge pushes
effective values on the marker payloads.

Like Tasks 01-04, side-effecting I/O (Pygame display, sleep) is bundled
into a ``TaskIO`` dataclass, and the Vayl bridge is passed in via
``run()``'s ``bridge`` parameter so tests can inject a mock implementation
without touching localhost HTTP or real LSL streams.
"""

from __future__ import annotations

import csv
import importlib
import logging
import platform
import subprocess
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


# ----- macOS Dock + menu-bar auto-hide helpers -----------------------------
#
# Vayl's overlay is a normal window that sits below Apple's Dock and menu
# bar, so those stay visible during the ramp. We flip on auto-hide for the
# duration of Task 05 (only) and restore whatever the RA had configured
# before. No-ops on non-macOS.


def _read_dock_autohide() -> bool | None:
    """Return the current `autohide` state of the Dock, or None if unknown."""
    try:
        out = subprocess.run(
            ["/usr/bin/defaults", "read", "com.apple.dock", "autohide"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("defaults read com.apple.dock autohide failed: %s", exc)
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() in {"1", "true", "YES"}


def _set_dock_autohide(value: bool) -> None:
    """Set the Dock's autohide state and restart the Dock to apply."""
    try:
        subprocess.run(
            ["/usr/bin/defaults", "write", "com.apple.dock",
             "autohide", "-bool", "true" if value else "false"],
            check=False, timeout=2,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            ["/usr/bin/killall", "Dock"],
            check=False, timeout=2,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("Could not toggle Dock autohide=%s: %s", value, exc)


def _set_menu_bar_autohide(value: bool) -> None:
    """Set the menu bar's autohide state via AppleScript.

    On macOS 13+ the menu bar autohide toggle lives under Dock preferences.
    Uses AppleScript so we don't have to chase the underlying defaults
    domain (which has changed across OS versions).
    """
    script = (
        f'tell application "System Events" to tell dock preferences to set '
        f'autohide menu bar to {"true" if value else "false"}'
    )
    try:
        subprocess.run(
            ["/usr/bin/osascript", "-e", script],
            check=False, timeout=2,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("Could not toggle menu bar autohide=%s: %s", value, exc)


def _read_menu_bar_autohide() -> bool | None:
    """Return the current menu-bar autohide state, or None if unknown."""
    script = (
        'tell application "System Events" to tell dock preferences to get '
        'autohide menu bar'
    )
    try:
        out = subprocess.run(
            ["/usr/bin/osascript", "-e", script],
            check=False, capture_output=True, text=True, timeout=2,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("osascript read menu bar autohide failed: %s", exc)
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip().lower() == "true"


def _hide_cursor_system_wide() -> None:
    """Hide the mouse cursor via CoreGraphics (system-wide, ref-counted).

    Unlike ``NSCursor.hide`` which only applies while our app is frontmost,
    ``CGDisplayHideCursor`` is ref-counted on the display and persists
    across the app handoff to Vayl. No-op on non-macOS.
    """
    if platform.system() != "Darwin":
        return
    try:
        from Quartz import (  # noqa: PLC0415
            CGDisplayHideCursor,
            CGMainDisplayID,
        )

        CGDisplayHideCursor(CGMainDisplayID())
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not hide cursor: %s", exc)


def _show_cursor_system_wide() -> None:
    """Undo :func:`_hide_cursor_system_wide`."""
    if platform.system() != "Darwin":
        return
    try:
        from Quartz import (  # noqa: PLC0415
            CGDisplayShowCursor,
            CGMainDisplayID,
        )

        CGDisplayShowCursor(CGMainDisplayID())
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not show cursor: %s", exc)


class _ChromeHide:
    """Context manager that hides the Dock, menu bar, and cursor for a block.

    Remembers whatever Dock / menu-bar state was set beforehand and restores
    it on exit. Silent no-op on non-macOS or when the underlying commands
    fail.
    """

    def __init__(self) -> None:
        self._prev_dock: bool | None = None
        self._prev_menubar: bool | None = None
        self._active = False
        self._cursor_hidden = False

    def __enter__(self) -> "_ChromeHide":
        if platform.system() != "Darwin":
            return self
        self._prev_dock = _read_dock_autohide()
        self._prev_menubar = _read_menu_bar_autohide()
        _set_dock_autohide(True)
        _set_menu_bar_autohide(True)
        _hide_cursor_system_wide()
        self._cursor_hidden = True
        self._active = True
        return self

    def __exit__(self, *_exc_info: object) -> None:
        if not self._active:
            return
        if self._cursor_hidden:
            _show_cursor_system_wide()
            self._cursor_hidden = False
        if self._prev_dock is not None:
            _set_dock_autohide(self._prev_dock)
        if self._prev_menubar is not None:
            _set_menu_bar_autohide(self._prev_menubar)



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

    show_solid: Callable[[tuple[int, int, int]], None]
    """Fill the entire Pygame surface with *rgb* and flip."""

    iconify: Callable[[], None]
    """Minimize the Pygame window so the Vayl overlay owns the screen."""

    restore: Callable[[], None]
    """Re-present the Pygame window after the Vayl overlay fades out."""

    check_escape: Callable[[], None]
    """Raise :class:`EscapePressedError` if Escape was pressed."""

    wait: Callable[[float], None]
    """Sleep for *seconds* (used by the demo fallback when Vayl is down)."""


def _build_pygame_io(demo: bool) -> tuple[TaskIO, Callable[[], None]]:
    """Construct a Pygame-backed ``TaskIO`` for the participant-facing screens.

    We intentionally only use Pygame (not PsychoPy) for the instruction and
    background screens so there is exactly one display-framework stack
    alongside Vayl -- mixing Pygame, PsychoPy, and Vayl's overlay would be
    asking for window-focus bugs. In demo mode we use a windowed surface so
    the RA can verify the flow without taking over the display; a live
    session goes fullscreen.
    """
    import pygame  # noqa: PLC0415

    pygame.init()
    # NOFRAME borderless at desktop resolution — visually fullscreen, but
    # it's a regular window so `pygame.display.iconify()` actually works
    # during the Vayl ramp. Using `pygame.FULLSCREEN` on macOS puts the
    # window in its own Mission Control Space where iconify is a no-op,
    # which left the RA stuck on a white screen until Vayl finished.
    # Demo keeps the same fullscreen-sized NOFRAME window as production;
    # only the ramp duration / trial counts are abbreviated for demo runs
    # elsewhere. The RA sees exactly what the participant will see.
    try:
        mode_size: tuple[int, int] = pygame.display.get_desktop_sizes()[0]
    except (AttributeError, IndexError):
        info = pygame.display.Info()
        mode_size = (info.current_w or 1920, info.current_h or 1080)
    flags = pygame.NOFRAME
    screen = pygame.display.set_mode(mode_size, flags)
    surf_w, surf_h = screen.get_size()
    pygame.display.set_caption("IACS Task 05 -- SSVEP Frequency Ramp")
    font = pygame.font.SysFont(None, 48)
    clock = pygame.time.Clock()

    def show_text_and_wait(text: str, wait_key: str) -> None:
        waiting = True
        while waiting:
            screen.fill((40, 40, 40))
            y = surf_h // 2 - 200
            for line in text.split("\n"):
                surf = font.render(line, True, (230, 230, 230))
                rect = surf.get_rect(center=(surf_w // 2, y))
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

    def show_solid(rgb: tuple[int, int, int]) -> None:
        screen.fill(rgb)
        pygame.display.flip()
        pygame.event.pump()

    def iconify() -> None:
        pygame.display.iconify()

    def restore() -> None:
        # Re-setting the display mode brings the window back to the front
        # on most platforms after an iconify.
        nonlocal screen
        screen = pygame.display.set_mode(mode_size, flags)

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
        show_solid=show_solid,
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

    # Demo no longer shortens the ramp — RAs want to verify the full 5 min
    # (300 s by default, sourced from config) to match the real protocol.

    if output_dir is None:
        output_dir = DATA_DIR

    own_outlet = False
    if outlet is None:
        outlet = create_demo_outlet()
        own_outlet = True

    cleanup: Callable[[], None] = lambda: None  # noqa: E731
    if io is None:
        io, cleanup = _build_pygame_io(demo=demo)

    if bridge is None:
        bridge = VaylBridge(
            api_url=config["vayl_api_url"],
            lsl_stream_name=config["vayl_lsl_stream_name"],
            marker_outlet=outlet,  # reuse P013_Task_Markers — no VaylStim outlet
            emit_frequency_stream=config.get(
                "vayl_emit_frequency_stream", False
            ),
        )

    entries: list[dict] = []
    # Declared here so the `finally` block can always restore chrome, even
    # if the RA aborts during the ramp.
    chrome_hider = _ChromeHide()

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
            # Idempotent safety: make sure the overlay is OFF before we
            # begin. If a previous run (this process or another) left
            # Vayl strobing the desktop, we don't want the participant
            # seeing it during instructions.
            try:
                bridge.turn_off()
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "Pre-run bridge.turn_off() failed: %s "
                    "(non-fatal; continuing)", exc
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

        # Brief white flash, then hand the screen to Vayl. On macOS the
        # Pygame window otherwise sits above Vayl's overlay and the
        # stroboscope is invisible. Iconify is the pragmatic fix — the
        # participant sees the checkerboard, not our backdrop.
        #
        # ChromeHide flips on Dock + menu-bar auto-hide so Vayl's overlay
        # isn't framed by them while it runs. The RA's previous prefs are
        # restored in the `finally` block (covers abort + error paths too).
        chrome_hider.__enter__()
        io.show_solid((255, 255, 255))
        io.wait(0.5)
        io.iconify()

        # ----- Ramp -----
        start_hz = float(config["carrier_start_hz"])
        end_hz = float(config["carrier_end_hz"])
        duration_s = float(config["ramp_duration_s"])

        send_marker(outlet, "task05_ramp_begin")
        if vayl_available:
            ramp_result = bridge.start_ramp(
                start_hz,
                end_hz,
                duration_s,
                lab_opaque=config.get("vayl_lab_opaque", True),
                checkerboard_enabled=config.get("vayl_checkerboard_enabled", True),
                checker_size=config.get("vayl_checker_size", 100),
            )
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

        # ----- Restore Pygame window and show black -----
        # chrome_hider is restored in the `finally` block below; doing it
        # here too would be redundant.
        io.restore()
        io.show_solid((0, 0, 0))
        io.wait(1.5)

        log_path = _save_session_log(entries, participant_id, output_dir)

        send_marker(outlet, "task05_end")
        entries.append({"event": "task_end", "timestamp": local_clock()})

        log.info("Task 05 complete: %s", log_path)
        return log_path
    finally:
        # Always restore Dock/menu-bar preferences, even if the RA aborted
        # mid-ramp. No-op if we never hid them.
        chrome_hider.__exit__(None, None, None)
        # Always try to stop Vayl strobing on the way out, even on abort
        # or an exception mid-ramp. turn_off is idempotent so calling it
        # when the overlay is already down is a cheap no-op.
        if bridge is not None:
            try:
                bridge.turn_off()
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "finally bridge.turn_off() failed: %s "
                    "(desktop may still be strobing — check Vayl)", exc
                )
        cleanup()
        if own_outlet:
            del outlet
