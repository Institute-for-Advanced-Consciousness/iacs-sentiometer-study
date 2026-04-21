"""Task 01: Auditory Oddball (P300).

Two-tone oddball with an active button-press response on the deviant. Stimulus
and timing parameters are aligned with the ERP CORE standardized auditory
oddball protocol (Kappenman et al., 2021, NeuroImage).

Lifecycle:
1. Instructions screen.
2. Practice gate: 10-trial practice block (8 standard + 2 deviant). Participant
   must hit at least 75% of deviants and have no more than 50% false alarms on
   standards. If they fail, the block repeats. There is no cap on attempts.
3. Main task: ~250 trials (80% standard / 20% deviant), pseudorandom order
   subject to ``max_consecutive_standards``.
4. Behavioral CSV written to ``data/{participant_id}/task01_oddball_*.csv``.

All side-effecting I/O (display, audio, keyboard, sleep) is bundled into a
``TaskIO`` dataclass so the task can be driven headlessly by tests without
PsychoPy installed. ``run()`` builds a real PsychoPy-backed ``TaskIO`` only when
a caller does not provide one. PsychoPy is imported lazily inside the builder.
"""

from __future__ import annotations

import csv
import logging
import random
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pylsl import StreamOutlet, local_clock

from tasks.common.audio import DEVIANT_TONE_PATH, STANDARD_TONE_PATH
from tasks.common.config import get_task_config, load_session_config
from tasks.common.lsl_markers import create_demo_outlet, send_marker

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = REPO_ROOT / "data"

TASK_NAME = "task01_oddball"

STANDARD = "standard"
DEVIANT = "deviant"

INSTRUCTIONS_TEXT = (
    "Auditory Oddball Task\n\n"
    "You will hear two tones: a low tone and a high tone.\n"
    "Most of the tones will be the LOW tone (standard).\n"
    "Occasionally you will hear a HIGH tone (target).\n\n"
    "Press the SPACEBAR as quickly as possible whenever you hear the HIGH tone.\n"
    "Do NOT press anything when you hear the LOW tone.\n\n"
    "We will start with a short practice round.\n\n"
    "Press SPACEBAR when you are ready to begin."
)


# ----- Trial records ---------------------------------------------------------


@dataclass
class TrialRecord:
    """One row of the behavioral log."""

    trial_number: int
    phase: str  # "practice" or "main"
    practice_attempt: int | None
    tone_type: str  # "standard" or "deviant"
    tone_onset_time: float  # LSL local_clock at tone onset
    response_time: float | None  # LSL local_clock at button press, or None
    response_type: str  # "hit" / "miss" / "false_alarm" / "correct_rejection"
    rt_ms: float | None  # reaction time (ms) relative to tone onset


# ----- Pure helpers (testable without PsychoPy) ------------------------------


def build_trial_sequence(
    n_total: int,
    deviant_probability: float,
    max_consecutive_standards: int,
    min_consecutive_standards: int = 1,
    rng: random.Random | None = None,
) -> list[str]:
    """Build a pseudorandom oddball trial sequence with varied gap sizes.

    Splits the standards into ``n_deviants + 1`` buckets (gaps before,
    between, and after deviants). Each bucket starts at
    ``min_consecutive_standards`` and the remaining standards are added
    one at a time to a uniformly-chosen bucket that still has headroom
    under ``max_consecutive_standards``. Buckets are then shuffled. The
    result is a random gap distribution that respects both bounds, rather
    than the near-uniform gaps that stratified placement produces.

    If the bounds are infeasible for the requested counts (``min * n_buckets
    > n_standards`` or ``max * n_buckets < n_standards``), logs a warning
    and relaxes the offending bound to the nearest feasible value.

    Returns a list of length *n_total* containing ``"standard"`` /
    ``"deviant"`` strings.
    """
    rng = rng or random.Random()
    n_deviants = round(n_total * deviant_probability)
    n_standards = n_total - n_deviants

    if n_deviants == 0:
        return [STANDARD] * n_standards

    n_buckets = n_deviants + 1

    min_floor = max(0, min_consecutive_standards)
    if min_floor * n_buckets > n_standards:
        new_min = n_standards // n_buckets
        log.warning(
            "min_consecutive_standards=%d infeasible with %d standards / "
            "%d deviants. Relaxing to %d.",
            min_consecutive_standards, n_standards, n_deviants, new_min,
        )
        min_floor = new_min

    max_ceil = max_consecutive_standards
    min_feasible_max = -(-n_standards // n_buckets)  # ceil
    if max_ceil < min_feasible_max:
        log.warning(
            "max_consecutive_standards=%d infeasible with %d standards / "
            "%d deviants (min feasible = %d). Relaxing constraint.",
            max_consecutive_standards, n_standards, n_deviants, min_feasible_max,
        )
        max_ceil = min_feasible_max

    buckets = [min_floor] * n_buckets
    remaining = n_standards - min_floor * n_buckets
    while remaining > 0:
        candidates = [i for i, b in enumerate(buckets) if b < max_ceil]
        if not candidates:
            break
        i = rng.choice(candidates)
        buckets[i] += 1
        remaining -= 1

    rng.shuffle(buckets)

    seq: list[str] = []
    for i, size in enumerate(buckets):
        seq.extend([STANDARD] * size)
        if i < n_buckets - 1:
            seq.append(DEVIANT)
    return seq


def build_practice_sequence(
    gap_options: tuple[int, ...] = (1, 2, 3, 4),
    rng: random.Random | None = None,
) -> list[str]:
    """Build the practice trial order: one deviant per gap, gaps shuffled.

    For each ``n`` in a random permutation of *gap_options*, emits ``n``
    standards followed by a single deviant. With the default gaps
    ``(1, 2, 3, 4)`` the result is always 10 standards + 4 deviants = 14
    trials; the position of the deviants varies trial-to-trial.

    Unlike :func:`build_trial_sequence` (used for the main block's stratified
    placement), this structure guarantees each deviant is preceded by a
    distinct, small number of standards — enough to demonstrate the target
    without boring the RA during repeated practice attempts.
    """
    rng = rng or random.Random()
    gaps = list(gap_options)
    rng.shuffle(gaps)
    seq: list[str] = []
    for gap in gaps:
        seq.extend([STANDARD] * gap)
        seq.append(DEVIANT)
    return seq


def compute_practice_metrics(records: list[TrialRecord]) -> tuple[float, float]:
    """Return ``(hit_rate, false_alarm_rate)`` over a list of practice trials."""
    deviants = [r for r in records if r.tone_type == DEVIANT]
    standards = [r for r in records if r.tone_type == STANDARD]

    hits = sum(1 for r in deviants if r.response_type == "hit")
    fas = sum(1 for r in standards if r.response_type == "false_alarm")

    hit_rate = hits / len(deviants) if deviants else 0.0
    fa_rate = fas / len(standards) if standards else 0.0
    return hit_rate, fa_rate


# ----- I/O bundle (mockable) -------------------------------------------------


@dataclass
class TaskIO:
    """Side-effecting callables that the task uses to interact with the world.

    Production: backed by PsychoPy + the audio module via :func:`_build_psychopy_io`.
    Tests: backed by a mock implementation that drives the task headlessly.
    """

    play_tone: Callable[[str], float]
    """Play *tone_type* and return the LSL ``local_clock()`` at onset."""

    wait_for_response: Callable[[float, float], float | None]
    """Wait up to *window_s* for a response after *onset_lsl_time*.

    Returns reaction time in milliseconds, or ``None`` if no response.
    May raise :class:`tasks.common.display.EscapePressedError`.
    """

    show_screen: Callable[[str, str | None], None]
    """Show *text*. If *wait_key* is given, block until that key is pressed."""

    show_fixation: Callable[[], None]
    """Draw a fixation cross on a black background and flip.

    Called at the start of practice / main blocks so the participant has a
    stable fixation point to look at rather than lingering instructions text.
    Nothing else redraws the display during a block, so the fixation stays
    visible through every tone and ISI.
    """

    check_escape: Callable[[], None]
    """Raise :class:`EscapePressedError` if Escape was pressed since last check."""

    wait: Callable[[float], None]
    """Sleep for *seconds*."""


def _build_psychopy_io(demo: bool) -> tuple[TaskIO, Callable[[], None]]:
    """Construct a real PsychoPy-backed ``TaskIO``.

    Imports PsychoPy lazily so the module is importable on machines without
    PsychoPy installed (e.g. the Windows dev box).

    Returns
    -------
    (TaskIO, cleanup)
        A bundle plus a cleanup callable that closes the window.
    """
    from psychopy import core, event, visual  # noqa: PLC0415

    from tasks.common.audio import load_tone  # noqa: PLC0415
    from tasks.common.display import (  # noqa: PLC0415
        EscapePressedError,
        create_window,
        draw_fixation,
    )
    from tasks.common.display import (
        check_escape as display_check_escape,
    )
    from tasks.common.instructions import show_instructions  # noqa: PLC0415

    # Demo mode keeps the full-screen display so the RA can sanity-check
    # exactly what the participant sees. Only trial counts / practice gates
    # are abbreviated in demo, not the visual layout.
    win = create_window(fullscreen=True)
    # Force focus so keyboard events reach this window when the Tk launcher
    # is still alive in the same process.
    try:
        win.winHandle.activate()
    except Exception:  # noqa: BLE001
        pass
    sounds = {
        STANDARD: load_tone(STANDARD_TONE_PATH),
        DEVIANT: load_tone(DEVIANT_TONE_PATH),
    }

    def play_tone(tone_type: str) -> float:
        # Stamp local_clock as close to the play() call as we can. PsychoPy's
        # Sound.play() returns immediately after handing the buffer to the
        # backend.
        #
        # PsychoPy's pygame Sound backend has a broken `isPlaying` guard:
        # it sets `_isPlaying = True` on first play but never resets it when
        # the sound finishes, so every subsequent play() silently no-ops.
        # Calling stop() first (or forcing `_isPlaying = False`) is the
        # documented workaround — we go with the internal flag because
        # stop() has its own guard that short-circuits when `_isPlaying` is
        # unreliable on top of a finished channel.
        sound = sounds[tone_type]
        try:
            sound._isPlaying = False
        except AttributeError:
            pass
        sound.play()
        return local_clock()

    def wait_for_response(onset_lsl_time: float, window_s: float) -> float | None:
        # Poll with explicit pyglet event dispatch. Relying on
        # ``event.waitKeys(maxWait=...)`` can hang on macOS when Tk is still
        # alive in the same process because pyglet's event loop isn't being
        # pumped. Dispatching events from the window each tick guarantees
        # keystrokes get into the psychopy event queue and the timeout
        # actually fires.
        deadline = local_clock() + window_s
        event.clearEvents(eventType="keyboard")
        while local_clock() < deadline:
            try:
                win.winHandle.dispatch_events()
            except Exception:  # noqa: BLE001
                pass
            keys = event.getKeys(keyList=["space", "escape"])
            if keys:
                if "escape" in keys:
                    raise EscapePressedError
                return (local_clock() - onset_lsl_time) * 1000.0
            core.wait(0.005, hogCPUperiod=0.005)
        return None

    def show_screen(text: str, wait_key: str | None) -> None:
        if wait_key:
            show_instructions(win, text, wait_key=wait_key)
        else:
            stim = visual.TextStim(
                win, text=text, color="white", height=0.04, wrapWidth=1.5
            )
            stim.draw()
            win.flip()

    def show_fixation() -> None:
        draw_fixation(win)
        win.flip()

    def check_escape() -> None:
        display_check_escape(win)

    def wait(seconds: float) -> None:
        core.wait(seconds)

    def cleanup() -> None:
        win.close()

    io = TaskIO(
        play_tone=play_tone,
        wait_for_response=wait_for_response,
        show_screen=show_screen,
        show_fixation=show_fixation,
        check_escape=check_escape,
        wait=wait,
    )
    return io, cleanup


# ----- Block runner ----------------------------------------------------------


def _run_block(
    *,
    outlet: StreamOutlet,
    io: TaskIO,
    config: dict,
    phase: str,
    practice_attempt: int | None,
    n_trials: int,
    n_deviants: int,
    rng: random.Random,
    trial_offset: int,
) -> list[TrialRecord]:
    """Run one block of oddball trials and return the records.

    Emits tone markers on every trial (with a ``practice_`` infix during the
    practice phase) and main-task response markers (hit / miss / false alarm)
    only during the main phase. Practice response classification is recorded
    in the returned records but not emitted as markers, so the marker stream
    cleanly distinguishes practice-phase events from main-task events.
    """
    # Practice has its own optional timing overrides so the block can run
    # quickly (~15 s) without affecting the main ERP CORE timing. Missing
    # keys fall back to the main values. Practice also uses a distinct
    # sequence (one deviant per gap size in {1,2,3,4}, shuffled) rather than
    # the stratified placement used for the main block.
    if phase == "practice":
        sequence = build_practice_sequence(rng=rng)
        isi_min_s = config.get("practice_isi_min_ms", config["isi_min_ms"]) / 1000.0
        isi_max_s = config.get("practice_isi_max_ms", config["isi_max_ms"]) / 1000.0
        response_window_s = (
            config.get("practice_response_window_ms", config["response_window_ms"])
            / 1000.0
        )
        tone_marker = {
            STANDARD: "task01_practice_tone_standard",
            DEVIANT: "task01_practice_tone_deviant",
        }
    else:
        deviant_probability = n_deviants / n_trials if n_trials else 0.0
        sequence = build_trial_sequence(
            n_total=n_trials,
            deviant_probability=deviant_probability,
            max_consecutive_standards=config["max_consecutive_standards"],
            min_consecutive_standards=config.get("min_consecutive_standards", 1),
            rng=rng,
        )
        isi_min_s = config["isi_min_ms"] / 1000.0
        isi_max_s = config["isi_max_ms"] / 1000.0
        response_window_s = config["response_window_ms"] / 1000.0
        tone_marker = {
            STANDARD: "task01_tone_standard",
            DEVIANT: "task01_tone_deviant",
        }

    records: list[TrialRecord] = []

    for i, tone_type in enumerate(sequence, start=1):
        io.check_escape()

        onset_ts = io.play_tone(tone_type)
        send_marker(outlet, tone_marker[tone_type])

        rt_ms = io.wait_for_response(onset_ts, response_window_s)

        if tone_type == DEVIANT:
            if rt_ms is not None:
                response_type = "hit"
                if phase == "main":
                    send_marker(outlet, "task01_response_hit")
            else:
                response_type = "miss"
                if phase == "main":
                    send_marker(outlet, "task01_response_miss")
        else:  # standard
            if rt_ms is not None:
                response_type = "false_alarm"
                if phase == "main":
                    send_marker(outlet, "task01_response_false_alarm")
            else:
                response_type = "correct_rejection"
                # No marker for correct rejection: it is the silent default.

        response_time = onset_ts + (rt_ms / 1000.0) if rt_ms is not None else None
        records.append(
            TrialRecord(
                trial_number=trial_offset + i,
                phase=phase,
                practice_attempt=practice_attempt,
                tone_type=tone_type,
                tone_onset_time=onset_ts,
                response_time=response_time,
                response_type=response_type,
                rt_ms=rt_ms,
            )
        )

        # ISI: target uniform in [isi_min, isi_max]. Subtract time already spent
        # in the response window so total inter-onset interval is honored.
        isi_total_s = rng.uniform(isi_min_s, isi_max_s)
        time_spent_s = (rt_ms / 1000.0) if rt_ms is not None else response_window_s
        io.wait(max(0.0, isi_total_s - time_spent_s))

    return records


# ----- Behavioral log --------------------------------------------------------


_LOG_FIELDS = (
    "trial_number",
    "phase",
    "practice_attempt",
    "tone_type",
    "tone_onset_time",
    "response_time",
    "response_type",
    "rt_ms",
)


def _save_behavioral_log(
    records: list[TrialRecord],
    participant_id: str,
    output_dir: Path,
) -> Path:
    """Write *records* to ``output_dir/{participant_id}/task01_oddball_*.csv``."""
    out = output_dir / participant_id
    out.mkdir(parents=True, exist_ok=True)
    log_path = out / f"task01_oddball_{int(local_clock() * 1000)}.csv"
    with open(log_path, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(_LOG_FIELDS)
        for r in records:
            writer.writerow(
                [
                    r.trial_number,
                    r.phase,
                    "" if r.practice_attempt is None else r.practice_attempt,
                    r.tone_type,
                    f"{r.tone_onset_time:.6f}",
                    "" if r.response_time is None else f"{r.response_time:.6f}",
                    r.response_type,
                    "" if r.rt_ms is None else f"{r.rt_ms:.2f}",
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
    """Run the Auditory Oddball task end-to-end.

    Parameters
    ----------
    outlet:
        Shared session marker outlet from the launcher. If ``None``, a
        temporary demo outlet is created and torn down at the end.
    config:
        Per-task config dict (the ``task01_oddball`` section of
        ``session_defaults.yaml``). If ``None``, loaded from the shipped
        defaults via :func:`load_session_config`.
    participant_id:
        Used to name the behavioral-log subdirectory. Defaults to ``"DEMO"``.
    demo:
        If ``True``, run an abbreviated session: 1 practice block that
        always passes after one attempt and a 20-trial main block.
    io:
        Bundle of side-effecting callables. If ``None``, a PsychoPy-backed
        bundle is constructed (requires PsychoPy installed). Tests pass a
        mock :class:`TaskIO` to drive the task headlessly.
    rng_seed:
        Optional seed for the trial-order RNG (deterministic tests).
    output_dir:
        Root directory for behavioral logs. Defaults to ``data/`` at the
        repo root.

    Returns
    -------
    Path
        Path to the behavioral-log CSV.
    """
    if config is None:
        config = get_task_config(load_session_config(), TASK_NAME)
    else:
        config = dict(config)

    # Demo mode shortens only the MAIN block. Practice keeps its full fixed
    # 14-trial structure (built by build_practice_sequence) so the RA can
    # actually exercise the pass/fail logic in demo runs. Demo also skips
    # the practice-accuracy gate (`passed = demo or …`) so the RA never
    # gets stuck retrying.
    if demo:
        config["total_trials"] = 20

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
    all_records: list[TrialRecord] = []

    try:
        send_marker(outlet, "task01_start")

        # ----- Instructions -----
        send_marker(outlet, "task01_instructions_start")
        io.show_screen(INSTRUCTIONS_TEXT, wait_key="space")
        send_marker(outlet, "task01_instructions_end")

        # ----- Practice gate (repeats until passed; demo passes on attempt 1) -----
        practice_attempt = 0
        while True:
            practice_attempt += 1
            send_marker(outlet, "task01_practice_start")
            send_marker(outlet, f"task01_practice_attempt_{practice_attempt}")

            # Clear the instruction/feedback text and put up a stable
            # fixation cross for the duration of this practice block.
            io.show_fixation()

            # `_run_block` ignores n_trials/n_deviants in the practice phase
            # and builds a fixed 14-trial sequence via build_practice_sequence.
            # Pass zeros to make that explicit.
            practice_records = _run_block(
                outlet=outlet,
                io=io,
                config=config,
                phase="practice",
                practice_attempt=practice_attempt,
                n_trials=0,
                n_deviants=0,
                rng=rng,
                trial_offset=len(all_records),
            )
            all_records.extend(practice_records)

            send_marker(outlet, "task01_practice_end")

            hit_rate, fa_rate = compute_practice_metrics(practice_records)
            n_dev = sum(1 for r in practice_records if r.tone_type == DEVIANT)
            n_hits = sum(1 for r in practice_records if r.response_type == "hit")

            passed = demo or (
                hit_rate >= config["practice_hit_threshold"]
                and fa_rate <= config["practice_fa_ceiling"]
            )

            if passed:
                send_marker(outlet, "task01_practice_passed")
                io.show_screen(
                    f"Great job! You detected {n_hits} of {n_dev} target tones "
                    f"({100 * hit_rate:.0f}%).\n\n"
                    "The main task will now begin.\n\n"
                    "Press SPACEBAR when ready.",
                    wait_key="space",
                )
                break

            io.show_screen(
                f"You detected {n_hits} of {n_dev} target tones "
                f"({100 * hit_rate:.0f}%). You need at least 75% to continue.\n\n"
                "Let's try the practice again. Remember: press the button ONLY "
                "when you hear the higher-pitched tone.\n\n"
                "Press SPACEBAR to continue.",
                wait_key="space",
            )

        # ----- Main task -----
        # Clear the "main task starting" message and put up the fixation
        # cross. It stays on screen throughout the ~5 min block since the
        # trial loop never redraws.
        io.show_fixation()

        n_main = config["total_trials"]
        n_main_deviants = round(n_main * config["deviant_probability"])
        main_records = _run_block(
            outlet=outlet,
            io=io,
            config=config,
            phase="main",
            practice_attempt=None,
            n_trials=n_main,
            n_deviants=n_main_deviants,
            rng=rng,
            trial_offset=len(all_records),
        )
        all_records.extend(main_records)

        log_path = _save_behavioral_log(all_records, participant_id, output_dir)

        send_marker(outlet, "task01_end")
        log.info("Task 01 complete: %s", log_path)
        return log_path
    finally:
        cleanup()
        if own_outlet:
            del outlet
