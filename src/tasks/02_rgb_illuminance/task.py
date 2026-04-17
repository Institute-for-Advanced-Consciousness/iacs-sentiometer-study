"""Task 02: RGB Illuminance Test (Visual Qualia Decoding null hypothesis).

Passive fixation while the screen cycles through full-screen pure red, green,
and blue with a small white fixation cross overlay. Each color is shown for a
jittered 1.2-2.0 s; a 200 ms medium-gray + fixation inter-trial interval (ITI)
follows each color to suppress contrast flashes and afterimages. Two 30 s rest
breaks (after trial 100 and trial 200) split the 300 trials into three blocks
of ~100 colors each.

Scientifically this is a **null hypothesis** test: the Sentiometer should NOT
be able to decode color identity from the optical signal. EEG decoding is the
positive control.

The task follows the same architectural pattern as Task 01: side-effecting I/O
(display, sleep) is bundled into a ``TaskIO`` dataclass so the task can be
driven headlessly by tests, and PsychoPy is imported lazily inside the
production builder so the module imports cleanly on machines without the full
``tasks`` extra installed.
"""

from __future__ import annotations

import csv
import logging
import random
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pylsl import StreamOutlet, local_clock

from tasks.common.config import get_task_config, load_session_config
from tasks.common.lsl_markers import create_demo_outlet, send_marker

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = REPO_ROOT / "data"

TASK_NAME = "task02_rgb_illuminance"

# Pure RGB stimuli. These exact integer triples are written to the behavioral
# CSV so analysis code can confirm what was rendered. Display gamma and panel
# fidelity must still be verified with a colorimeter in Phase 3.3.
COLORS_RGB255: dict[str, tuple[int, int, int]] = {
    "red": (255, 0, 0),
    "green": (0, 255, 0),
    "blue": (0, 0, 255),
}
GRAY_RGB255: tuple[int, int, int] = (128, 128, 128)

INSTRUCTIONS_TEXT = (
    "RGB Illuminance Task\n\n"
    "In this task, the screen will change between different colors.\n"
    "Please keep your eyes open and focused on the small cross in the center "
    "of the screen at all times.\n"
    "Try to blink naturally and avoid looking away.\n"
    "You do not need to press any buttons.\n\n"
    "The task takes about 10 minutes with two short rest breaks.\n\n"
    "Press SPACEBAR when you are ready to begin."
)


# ----- Trial records ---------------------------------------------------------


@dataclass
class TrialRecord:
    """One row of the behavioral log."""

    trial_number: int
    color: str
    color_rgb: tuple[int, int, int]
    onset_time: float  # LSL local_clock at color flip
    offset_time: float  # LSL local_clock at the following ITI flip (true end)
    duration_s: float  # requested jittered duration (uniform [min, max])
    iti_onset_time: float  # LSL local_clock at the gray ITI flip


# ----- Pure helpers (testable without PsychoPy) ------------------------------


def build_color_sequence(
    trials_per_color: int,
    colors: list[str],
    rng: random.Random | None = None,
) -> list[str]:
    """Build a pseudorandom color sequence with no two consecutive same color.

    Uses a "max remaining" greedy algorithm: at each step pick uniformly at
    random from the colors that have the largest remaining count, excluding
    the previous color. This guarantees a valid sequence whenever no single
    color exceeds ``(total + 1) / 2`` of the trials (Hall's theorem) and
    keeps the resulting permutation well mixed.
    """
    rng = rng or random.Random()
    counts = {c: trials_per_color for c in colors}
    sequence: list[str] = []
    prev: str | None = None
    total = trials_per_color * len(colors)

    for _ in range(total):
        candidates = [c for c in colors if c != prev and counts[c] > 0]
        if not candidates:
            raise RuntimeError(
                "Cannot satisfy no-consecutive-color constraint -- does any "
                "single color exceed (n+1)/2 of the total trials?"
            )
        max_remaining = max(counts[c] for c in candidates)
        best = [c for c in candidates if counts[c] == max_remaining]
        choice = rng.choice(best)
        sequence.append(choice)
        counts[choice] -= 1
        prev = choice

    return sequence


def is_break_trial(trial_num: int, total_trials: int, config: dict) -> bool:
    """Return ``True`` if a break should fire after *trial_num*.

    Two ways to specify breaks:

    * ``break_after_trials: [int, ...]`` (explicit list) -- used by demo and
      tests to fire breaks at specific points without modular math.
    * ``break_interval_trials: int`` (modular) -- used in production. Fires
      after every Nth trial except the very last.
    """
    explicit = config.get("break_after_trials")
    if explicit is not None:
        return trial_num in explicit
    interval = config.get("break_interval_trials", 0)
    return interval > 0 and trial_num % interval == 0 and trial_num < total_trials


# ----- I/O bundle (mockable) -------------------------------------------------


@dataclass
class TaskIO:
    """Side-effecting callables that the task uses to interact with the world."""

    show_color: Callable[[str], float]
    """Show *color_name* full-screen with fixation overlay. Returns LSL onset."""

    show_gray_fixation: Callable[[], float]
    """Show medium-gray screen with fixation overlay. Returns LSL onset."""

    show_instructions: Callable[[str, str], None]
    """Show *text* and block until *wait_key* is pressed."""

    show_break_frame: Callable[[int], None]
    """Render one frame of the break countdown (gray + fixation + remaining text)."""

    check_escape: Callable[[], None]
    """Raise :class:`EscapePressedError` if Escape was pressed."""

    wait: Callable[[float], None]
    """Sleep for *seconds*."""


def _build_psychopy_io(demo: bool) -> tuple[TaskIO, Callable[[], None]]:
    """Construct a real PsychoPy-backed ``TaskIO``.

    Imports PsychoPy lazily so this module is usable on machines without
    PsychoPy installed (e.g. the Windows dev box).
    """
    from psychopy import core, visual  # noqa: PLC0415

    from tasks.common.display import (  # noqa: PLC0415
        check_escape as display_check_escape,
    )
    from tasks.common.display import (
        create_window,
        draw_fixation,
    )
    from tasks.common.instructions import (  # noqa: PLC0415
        show_instructions as draw_instructions,
    )

    # Demo keeps fullscreen; only trial counts / durations are abbreviated.
    win = create_window(fullscreen=True)

    # Pre-build full-screen rects in normalized coordinates so they always
    # cover the full window regardless of aspect ratio.
    color_rects = {
        name: visual.Rect(
            win,
            width=2,
            height=2,
            units="norm",
            fillColor=rgb,
            colorSpace="rgb255",
            lineColor=rgb,
            lineColorSpace="rgb255",
        )
        for name, rgb in COLORS_RGB255.items()
    }
    gray_rect = visual.Rect(
        win,
        width=2,
        height=2,
        units="norm",
        fillColor=GRAY_RGB255,
        colorSpace="rgb255",
        lineColor=GRAY_RGB255,
        lineColorSpace="rgb255",
    )

    def show_color(color_name: str) -> float:
        color_rects[color_name].draw()
        draw_fixation(win, color="white")
        win.flip()
        return local_clock()

    def show_gray_fixation() -> float:
        gray_rect.draw()
        draw_fixation(win, color="white")
        win.flip()
        return local_clock()

    def show_instructions_local(text: str, wait_key: str) -> None:
        draw_instructions(win, text, wait_key=wait_key)

    def show_break_frame(remaining_seconds: int) -> None:
        gray_rect.draw()
        draw_fixation(win, color="white")
        text = (
            "Take a moment to rest your eyes.\n"
            f"The task will continue in {remaining_seconds} seconds."
        )
        stim = visual.TextStim(
            win,
            text=text,
            color="white",
            height=0.04,
            pos=(0, -0.3),
            wrapWidth=1.5,
        )
        stim.draw()
        win.flip()

    def check_escape() -> None:
        display_check_escape(win)

    def wait(seconds: float) -> None:
        core.wait(seconds)

    def cleanup() -> None:
        win.close()

    io = TaskIO(
        show_color=show_color,
        show_gray_fixation=show_gray_fixation,
        show_instructions=show_instructions_local,
        show_break_frame=show_break_frame,
        check_escape=check_escape,
        wait=wait,
    )
    return io, cleanup


# ----- Break runner ----------------------------------------------------------


def _run_break(outlet: StreamOutlet, io: TaskIO, break_duration_s: int) -> None:
    """Run a single break: emit boundary markers and tick a 1 Hz countdown."""
    send_marker(outlet, "task02_break_start")
    for remaining in range(break_duration_s, 0, -1):
        io.show_break_frame(remaining)
        io.wait(1.0)
    send_marker(outlet, "task02_break_end")


# ----- Behavioral log --------------------------------------------------------


_LOG_FIELDS = (
    "trial_number",
    "color",
    "color_rgb",
    "onset_time",
    "offset_time",
    "duration_s",
    "iti_onset_time",
)


def _save_behavioral_log(
    records: list[TrialRecord],
    participant_id: str,
    output_dir: Path,
) -> Path:
    """Write *records* to ``output_dir/{participant_id}/task02_rgb_illuminance_*.csv``."""
    out = output_dir / participant_id
    out.mkdir(parents=True, exist_ok=True)
    log_path = out / f"task02_rgb_illuminance_{int(local_clock() * 1000)}.csv"
    with open(log_path, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(_LOG_FIELDS)
        for r in records:
            rgb_str = f"{r.color_rgb[0]},{r.color_rgb[1]},{r.color_rgb[2]}"
            writer.writerow(
                [
                    r.trial_number,
                    r.color,
                    rgb_str,
                    f"{r.onset_time:.6f}",
                    f"{r.offset_time:.6f}",
                    f"{r.duration_s:.6f}",
                    f"{r.iti_onset_time:.6f}",
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
    rng_seed: int | None = None,
    output_dir: Path | None = None,
) -> Path:
    """Run the RGB Illuminance task end-to-end.

    Parameters
    ----------
    outlet:
        Shared session marker outlet from the launcher. If ``None``, a
        temporary demo outlet is created and torn down at the end.
    config:
        Per-task config dict (the ``task02_rgb_illuminance`` section of
        ``session_defaults.yaml``). If ``None``, loaded from the shipped
        defaults.
    participant_id:
        Used to name the behavioral-log subdirectory.
    demo:
        If ``True``, run an abbreviated session: 5 trials per color (15
        total), one break after trial 5, faster trial durations. Completes
        in well under 30 seconds.
    io:
        Bundle of side-effecting callables. If ``None``, a PsychoPy-backed
        bundle is constructed (requires PsychoPy installed). Tests pass a
        mock :class:`TaskIO` to drive the task headlessly.
    rng_seed:
        Optional seed for the trial-order / jitter RNG.
    output_dir:
        Root directory for behavioral logs. Defaults to ``data/`` at the
        repo root.
    """
    if config is None:
        config = get_task_config(load_session_config(), TASK_NAME)
    else:
        config = dict(config)

    if demo:
        config["trials_per_color"] = 5
        config["break_after_trials"] = [5]
        config["break_duration_s"] = 3
        config["trial_duration_min_s"] = 0.5
        config["trial_duration_max_s"] = 0.8

    if output_dir is None:
        output_dir = DATA_DIR

    own_outlet = False
    if outlet is None:
        outlet = create_demo_outlet()
        own_outlet = True

    cleanup: Callable[[], None] = lambda: None  # noqa: E731
    if io is None:
        io, cleanup = _build_psychopy_io(demo=demo)

    rng = random.Random(rng_seed)

    colors = list(config["colors"])
    sequence = build_color_sequence(
        trials_per_color=config["trials_per_color"],
        colors=colors,
        rng=rng,
    )
    total_trials = len(sequence)

    duration_min = config["trial_duration_min_s"]
    duration_max = config["trial_duration_max_s"]
    iti_s = config["iti_duration_ms"] / 1000.0
    break_duration_s = int(config["break_duration_s"])

    records: list[TrialRecord] = []

    try:
        send_marker(outlet, "task02_start")

        send_marker(outlet, "task02_instructions_start")
        io.show_instructions(INSTRUCTIONS_TEXT, "space")
        send_marker(outlet, "task02_instructions_end")

        for i, color in enumerate(sequence, start=1):
            io.check_escape()

            duration_s = rng.uniform(duration_min, duration_max)

            color_onset = io.show_color(color)
            send_marker(outlet, f"task02_color_{color}")
            io.wait(duration_s)

            iti_onset = io.show_gray_fixation()
            send_marker(outlet, "task02_iti")
            io.wait(iti_s)

            records.append(
                TrialRecord(
                    trial_number=i,
                    color=color,
                    color_rgb=COLORS_RGB255[color],
                    onset_time=color_onset,
                    offset_time=iti_onset,
                    duration_s=duration_s,
                    iti_onset_time=iti_onset,
                )
            )

            if is_break_trial(i, total_trials, config):
                _run_break(outlet, io, break_duration_s)

        log_path = _save_behavioral_log(records, participant_id, output_dir)

        send_marker(outlet, "task02_end")
        log.info("Task 02 complete: %s", log_path)
        return log_path
    finally:
        cleanup()
        if own_outlet:
            del outlet
