"""Task 03: Backward Masking / Face Detection.

Adaptive backward masking procedure that drives the stimulus-onset asynchrony
(SOA) between a briefly flashed KDEF neutral face and a Mondrian mask toward
each participant's individual ~50% detection threshold. The dependent measure
is a 3-alternative response (Seen / Unsure / Not Seen) collected after every
trial, with ~17% mask-only catch trials to bound the false-alarm rate.

Lifecycle:

1. Instructions (3-key response mapping: F=Seen, J=Not Seen, Space=Unsure).
2. Practice (familiarization only, no performance gate): 6 face-present
   trials at a clearly-visible SOA + 2 catch trials, shuffled.
3. "Practice complete" screen.
4. Main task: 275 trials by default. Each trial structure is
   ``fixation (500 ms) -> face (1 frame) | gray (gap = SOA - 17 ms) -> mask
   (200 ms) -> response screen (up to 1500 ms)``. SOA is driven by a
   ``psychopy.data.QuestHandler`` targeting 50% detection. Catch trials do
   NOT update the staircase.
5. Behavioral CSV written to ``data/{participant_id}/``.

Side-effecting I/O is bundled into a ``TaskIO`` dataclass and the QUEST
staircase is behind a thin ``Staircase`` protocol; tests inject a mock IO
and a fixed-SOA staircase so the full task can be driven headlessly without
PsychoPy installed. ``demo=True`` also uses a fixed-SOA staircase and
placeholder face stimuli so it runs without the KDEF images on disk.
"""

from __future__ import annotations

import csv
import logging
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from pylsl import StreamOutlet, local_clock

from tasks.common.config import get_task_config, load_session_config
from tasks.common.lsl_markers import create_demo_outlet, send_marker

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = REPO_ROOT / "data"

TASK_NAME = "task03_backward_masking"

TASK_DIR = Path(__file__).resolve().parent
DEFAULT_FACES_DIR = TASK_DIR / "stimuli" / "faces"
DEFAULT_MASKS_DIR = TASK_DIR / "stimuli" / "masks"

# Per-frame refresh at 60 Hz. Used to convert the "1 frame" face duration
# into a millisecond budget when computing the gap between face offset and
# mask onset (gap = SOA - FACE_FRAME_MS).
FACE_FRAME_MS = 17

INSTRUCTIONS_TEXT = (
    "In this task, you will see a brief flash followed by a colorful pattern. "
    "Your job is to tell us whether you saw a face in the brief flash.\n\n"
    "After each pattern, you will be asked: 'Did you see a face?'\n\n"
    "Press F for YES\n"
    "Press J for NO\n"
    "Press SPACEBAR if you are UNSURE\n\n"
    "Sometimes the face will be easy to see, sometimes very difficult, and "
    "sometimes there will be no face at all. Just do your best -- there are "
    "no wrong answers.\n\n"
    "Keep your eyes on the cross in the center of the screen at all times.\n\n"
    "Press spacebar to begin the practice."
)

POST_PRACTICE_TEXT = (
    "Practice complete. In the real task, the faces will sometimes be very "
    "brief and hard to see. Just do your best.\n\n"
    "Press spacebar to begin."
)

RESPONSE_PROMPT_TEXT = (
    "Did you see a face?\n\n"
    "F = Yes    |    Spacebar = Unsure    |    J = No"
)


# ----- Trial records ---------------------------------------------------------


@dataclass
class TrialRecord:
    """One row of the behavioral log."""

    trial_number: int
    phase: str  # "practice" or "main"
    trial_type: str  # "face" or "catch"
    face_id: str  # filename stem or "none"
    mask_id: str  # filename stem
    soa_ms: int
    response: str  # "seen" | "unseen" | "unsure" | "timeout"
    rt_ms: float | None
    quest_threshold_estimate: float | None


# ----- Pure helpers ----------------------------------------------------------


def scan_face_directory(faces_dir: Path, min_identities: int) -> list[str]:
    """Return sorted face IDs (filename stems) for KDEF neutral images.

    Filters ``faces_dir`` to PNG files whose name contains ``"NE"`` (the
    KDEF expression code for Neutral). Raises :class:`RuntimeError` if
    fewer than *min_identities* are found, so the task refuses to run with
    a stimulus set too small to avoid per-identity learning effects.
    """
    if not faces_dir.exists():
        raise RuntimeError(
            f"Face stimulus directory not found: {faces_dir}. "
            f"See src/tasks/03_backward_masking/stimuli/README.md."
        )
    ids = sorted(
        p.stem for p in faces_dir.glob("*.png") if "NE" in p.name
    )
    if len(ids) < min_identities:
        raise RuntimeError(
            f"Only {len(ids)} neutral face(s) found in {faces_dir}; "
            f"need at least {min_identities}. Check the KDEF-cropped "
            f"stimulus set — neutral files contain 'NE' in the filename."
        )
    log.info("Loaded %d neutral face identities from %s", len(ids), faces_dir)
    return ids


def scan_mask_directory(masks_dir: Path) -> list[str]:
    """Return sorted mask IDs (filename stems) from *masks_dir*."""
    if not masks_dir.exists():
        raise RuntimeError(
            f"Mask stimulus directory not found: {masks_dir}. "
            f"Run scripts/generate_mondrians.py to regenerate."
        )
    ids = sorted(p.stem for p in masks_dir.glob("*.png"))
    if not ids:
        raise RuntimeError(f"No PNG masks found in {masks_dir}.")
    return ids


def build_trial_types(
    n_total: int,
    catch_proportion: float,
    rng: random.Random | None = None,
) -> list[str]:
    """Return a shuffled list of ``"face"`` / ``"catch"`` of length *n_total*.

    Catch proportion is honored via ``round(n_total * catch_proportion)``;
    the remainder is face-present trials. No additional constraint is
    applied — catches are shuffled in freely.
    """
    rng = rng or random.Random()
    n_catch = round(n_total * catch_proportion)
    n_face = n_total - n_catch
    seq = ["face"] * n_face + ["catch"] * n_catch
    rng.shuffle(seq)
    return seq


def build_face_schedule(
    face_ids: list[str],
    n_face_trials: int,
    rng: random.Random | None = None,
) -> list[str]:
    """Return a list of *n_face_trials* face IDs.

    Cycles through shuffled copies of *face_ids* so every identity is used
    roughly equally. With 27 faces and 228 face-present trials this gives
    each face ~8.4 presentations.
    """
    rng = rng or random.Random()
    schedule: list[str] = []
    while len(schedule) < n_face_trials:
        cycle = list(face_ids)
        rng.shuffle(cycle)
        schedule.extend(cycle)
    return schedule[:n_face_trials]


# ----- Staircase protocol ----------------------------------------------------


class Staircase(Protocol):
    """Minimal interface the task needs from a threshold staircase."""

    def next_soa(self) -> int: ...

    def update(self, seen: bool) -> None: ...

    @property
    def threshold_estimate(self) -> float: ...


class FixedSoaStaircase:
    """Non-adaptive staircase that cycles through a fixed list of SOAs.

    Used in ``demo=True`` and in tests to bypass QUEST while still
    exercising the task's marker/logging code paths.
    """

    def __init__(self, soas: list[int]) -> None:
        if not soas:
            raise ValueError("FixedSoaStaircase requires a non-empty list of SOAs")
        self._soas = list(soas)
        self._idx = 0
        self._last_response: bool | None = None

    def next_soa(self) -> int:
        soa = self._soas[self._idx % len(self._soas)]
        return soa

    def update(self, seen: bool) -> None:
        self._last_response = seen
        self._idx += 1

    @property
    def threshold_estimate(self) -> float:
        return float(sum(self._soas) / len(self._soas))


def _build_quest_staircase(config: dict) -> Staircase:
    """Construct a real :class:`psychopy.data.QuestHandler` wrapper.

    Imports PsychoPy lazily so the module is usable on dev machines without
    PsychoPy installed.
    """
    from psychopy.data import QuestHandler  # noqa: PLC0415

    class _QuestWrapper:
        def __init__(self) -> None:
            self._q = QuestHandler(
                startVal=config["soa_start_ms"],
                startValSd=50,
                pThreshold=0.50,
                beta=config["quest_beta"],
                delta=config["quest_delta"],
                gamma=config["quest_gamma"],
                grain=config["quest_grain"],
                nTrials=10000,
                minVal=config["soa_min_ms"],
                maxVal=config["soa_max_ms"],
            )
            self._current: int = int(config["soa_start_ms"])

        def next_soa(self) -> int:
            val = next(self._q)
            clamped = max(
                config["soa_min_ms"],
                min(config["soa_max_ms"], int(round(val))),
            )
            self._current = clamped
            return clamped

        def update(self, seen: bool) -> None:
            self._q.addResponse(1 if seen else 0)

        @property
        def threshold_estimate(self) -> float:
            return float(self._q.mean())

    return _QuestWrapper()


# ----- I/O bundle (mockable) -------------------------------------------------


@dataclass
class TaskIO:
    """Side-effecting callables for Task 03."""

    show_instructions: Callable[[str, str], None]
    """Show *text*; block until *wait_key* is pressed."""

    show_fixation: Callable[[], float]
    """Show gray bg + fixation cross. Returns LSL onset."""

    show_face: Callable[[str | None], float]
    """Show a face stimulus for one frame.

    If *face_id* is ``None`` this is a catch trial and only the gray bg +
    fixation is drawn. Returns LSL onset (the face-or-catch flip time).
    """

    show_mask: Callable[[str], float]
    """Show the Mondrian mask *mask_id*. Returns LSL onset."""

    show_response_prompt: Callable[[], None]
    """Draw the response prompt (non-blocking)."""

    wait_for_response: Callable[[float, dict], tuple[str | None, float | None]]
    """Wait up to *window_s* for one of the configured keys.

    Takes a dict mapping semantic label ('seen'/'unseen'/'unsure') to the
    actual key string. Returns ``(label, rt_ms)`` or ``(None, None)`` on
    timeout. May raise :class:`EscapePressedError`.
    """

    check_escape: Callable[[], None]

    wait: Callable[[float], None]


def _build_psychopy_io(
    demo: bool,
    face_ids: list[str],
    mask_ids: list[str],
    faces_dir: Path,
    masks_dir: Path,
    face_size_px: int,
) -> tuple[TaskIO, Callable[[], None]]:
    """Build a PsychoPy-backed ``TaskIO``, lazy-importing PsychoPy.

    Pre-loads face and mask image stims so trial flips are fast and jitter-
    free at runtime. In demo mode the face stims are replaced with plain
    gray squares so the task can run without KDEF files on disk.
    """
    from psychopy import core, event, visual  # noqa: PLC0415

    from tasks.common.display import (  # noqa: PLC0415
        check_escape as display_check_escape,
    )
    from tasks.common.display import create_window, draw_fixation
    from tasks.common.instructions import (  # noqa: PLC0415
        show_instructions as draw_instructions,
    )

    # Demo keeps fullscreen so the RA can verify participant-view geometry;
    # only trial counts are abbreviated.
    win = create_window(fullscreen=True)

    # Full-screen gray background rect
    gray_rect = visual.Rect(
        win,
        width=2,
        height=2,
        units="norm",
        fillColor=(128, 128, 128),
        colorSpace="rgb255",
        lineColor=(128, 128, 128),
        lineColorSpace="rgb255",
    )

    # Pre-load face stims. The KDEF images are committed to the repo so we
    # always use the real faces — including in demo mode where the RA wants
    # to see exactly what the participant would see.
    face_stims: dict[str, object] = {}
    for fid in face_ids:
        face_stims[fid] = visual.ImageStim(
            win,
            image=str(faces_dir / f"{fid}.png"),
            size=(face_size_px / 1080, face_size_px / 1080),
            units="height",
        )

    mask_stims: dict[str, object] = {}
    for mid in mask_ids:
        mask_stims[mid] = visual.ImageStim(
            win,
            image=str(masks_dir / f"{mid}.png"),
            size=(face_size_px / 1080, face_size_px / 1080),
            units="height",
        )

    def show_instructions_fn(text: str, wait_key: str) -> None:
        draw_instructions(win, text, wait_key=wait_key)

    def show_fixation() -> float:
        gray_rect.draw()
        draw_fixation(win, color="white")
        win.flip()
        return local_clock()

    def show_face(face_id: str | None) -> float:
        gray_rect.draw()
        if face_id is not None:
            face_stims[face_id].draw()  # type: ignore[attr-defined]
        draw_fixation(win, color="white")
        win.flip()
        return local_clock()

    def show_mask(mask_id: str) -> float:
        gray_rect.draw()
        mask_stims[mask_id].draw()  # type: ignore[attr-defined]
        win.flip()
        return local_clock()

    def show_response_prompt() -> None:
        gray_rect.draw()
        stim = visual.TextStim(
            win,
            text=RESPONSE_PROMPT_TEXT,
            color="white",
            height=0.05,
            wrapWidth=1.5,
        )
        stim.draw()
        win.flip()

    def wait_for_response(
        window_s: float, key_map: dict
    ) -> tuple[str | None, float | None]:
        from tasks.common.display import EscapePressedError  # noqa: PLC0415

        clock = core.Clock()
        keys = event.waitKeys(
            maxWait=window_s,
            keyList=list(key_map.values()) + ["escape"],
            timeStamped=clock,
        )
        if not keys:
            return None, None
        key, t = keys[0]
        if key == "escape":
            raise EscapePressedError
        label = next((lbl for lbl, k in key_map.items() if k == key), None)
        return label, t * 1000.0

    def check_escape_fn() -> None:
        display_check_escape(win)

    def wait_fn(seconds: float) -> None:
        core.wait(seconds)

    def cleanup() -> None:
        win.close()

    io = TaskIO(
        show_instructions=show_instructions_fn,
        show_fixation=show_fixation,
        show_face=show_face,
        show_mask=show_mask,
        show_response_prompt=show_response_prompt,
        wait_for_response=wait_for_response,
        check_escape=check_escape_fn,
        wait=wait_fn,
    )
    return io, cleanup


# ----- Block runner ----------------------------------------------------------


@dataclass
class _BlockResult:
    records: list[TrialRecord] = field(default_factory=list)


def _run_one_trial(
    *,
    outlet: StreamOutlet,
    io: TaskIO,
    config: dict,
    trial_number: int,
    phase: str,
    trial_type: str,
    face_id: str | None,
    mask_id: str,
    soa_ms: int,
    key_map: dict,
    staircase: Staircase | None,
) -> TrialRecord:
    """Run one backward-masking trial (practice or main) and return the record.

    Marker emission differs between practice and main: practice emits
    ``task03_practice_face_onset`` / ``task03_practice_catch`` /
    ``task03_practice_mask_onset`` and DOES NOT emit response or SOA markers;
    main emits the unprefixed stimulus markers plus response markers and, on
    face-present trials only, ``task03_soa_value_XXX``.
    """
    fixation_s = config["fixation_duration_ms"] / 1000.0
    mask_s = config["mask_duration_ms"] / 1000.0
    response_window_s = config["response_window_ms"] / 1000.0

    io.check_escape()

    # 1. Fixation
    io.show_fixation()
    if phase == "main":
        send_marker(outlet, "task03_fixation_onset")
    io.wait(fixation_s)

    # 2. Face (or gray for catch) for 1 frame, then gap until mask onset
    face_onset_ts = io.show_face(face_id if trial_type == "face" else None)
    if phase == "practice":
        if trial_type == "face":
            send_marker(outlet, "task03_practice_face_onset")
        else:
            send_marker(outlet, "task03_practice_catch")
    elif trial_type == "face":
        send_marker(outlet, "task03_face_onset")
    else:
        send_marker(outlet, "task03_catch_trial")

    gap_ms = max(0, soa_ms - FACE_FRAME_MS)
    io.wait(gap_ms / 1000.0)

    # 3. Mask
    mask_onset_ts = io.show_mask(mask_id)
    if phase == "practice":
        send_marker(outlet, "task03_practice_mask_onset")
    else:
        send_marker(outlet, "task03_mask_onset")
    io.wait(mask_s)

    # 4. Response screen
    io.show_response_prompt()
    label, rt_ms = io.wait_for_response(response_window_s, key_map)

    if phase == "main":
        response_marker = {
            "seen": "task03_response_seen",
            "unseen": "task03_response_unseen",
            "unsure": "task03_response_unsure",
        }.get(label or "", "task03_response_timeout")
        send_marker(outlet, response_marker)

    response_label = label if label is not None else "timeout"

    # 5. Staircase update + SOA marker (face-present main trials only)
    quest_est: float | None = None
    if phase == "main" and trial_type == "face" and staircase is not None:
        seen = response_label == "seen"
        staircase.update(seen)
        quest_est = staircase.threshold_estimate
        send_marker(outlet, f"task03_soa_value_{soa_ms:03d}")

    # Unused — kept for possible future timing auditing
    _ = (face_onset_ts, mask_onset_ts)

    return TrialRecord(
        trial_number=trial_number,
        phase=phase,
        trial_type=trial_type,
        face_id=face_id or "none",
        mask_id=mask_id,
        soa_ms=soa_ms,
        response=response_label,
        rt_ms=rt_ms,
        quest_threshold_estimate=quest_est,
    )


# ----- Behavioral log --------------------------------------------------------


_LOG_FIELDS = (
    "trial_number",
    "phase",
    "trial_type",
    "face_id",
    "mask_id",
    "soa_ms",
    "response",
    "rt_ms",
    "quest_threshold_estimate",
)


def _save_behavioral_log(
    records: list[TrialRecord],
    participant_id: str,
    output_dir: Path,
) -> Path:
    """Write *records* to ``output_dir/{participant_id}/task03_backward_masking_*.csv``."""
    out = output_dir / participant_id
    out.mkdir(parents=True, exist_ok=True)
    log_path = out / f"task03_backward_masking_{int(local_clock() * 1000)}.csv"
    with open(log_path, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(_LOG_FIELDS)
        for r in records:
            writer.writerow(
                [
                    r.trial_number,
                    r.phase,
                    r.trial_type,
                    r.face_id,
                    r.mask_id,
                    r.soa_ms,
                    r.response,
                    "" if r.rt_ms is None else f"{r.rt_ms:.2f}",
                    ""
                    if r.quest_threshold_estimate is None
                    else f"{r.quest_threshold_estimate:.2f}",
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
    staircase: Staircase | None = None,
    rng_seed: int | None = None,
    output_dir: Path | None = None,
    faces_dir: Path | None = None,
    masks_dir: Path | None = None,
) -> Path:
    """Run the Backward Masking task end-to-end.

    Parameters
    ----------
    outlet:
        Shared session marker outlet. If ``None`` a temporary demo outlet is
        created and torn down at the end.
    config:
        Per-task config dict (the ``task03_backward_masking`` section of
        ``session_defaults.yaml``). If ``None``, loaded from the shipped
        defaults.
    participant_id:
        Used to name the behavioral-log subdirectory.
    demo:
        If ``True``: 5 practice + 20 main trials (17 face + 3 catch), fixed
        SOAs cycling through ``[150, 100, 80, 60, 40]`` ms instead of QUEST,
        placeholder face stimuli. Completes in under a minute.
    io:
        Headless ``TaskIO``. If ``None``, a PsychoPy-backed bundle is built.
    staircase:
        Injected staircase (tests pass :class:`FixedSoaStaircase`). If
        ``None``, a QUEST staircase is built in production mode and a fixed
        ``[150, 100, 80, 60, 40]`` staircase in demo mode.
    rng_seed:
        Optional seed for trial-ordering / face-scheduling RNG.
    output_dir:
        Root directory for behavioral logs. Defaults to ``data/``.
    faces_dir / masks_dir:
        Stimulus directories. Default to the committed ``stimuli/faces`` and
        ``stimuli/masks`` under this task's directory.
    """
    if config is None:
        config = get_task_config(load_session_config(), TASK_NAME)
    else:
        config = dict(config)

    if demo:
        # Shorten only the main block. Practice runs at full count so the RA
        # can still exercise the familiarization flow end-to-end.
        config["total_trials"] = 20
        config["catch_trial_proportion"] = 3 / 20  # exactly 3 catch out of 20

    if output_dir is None:
        output_dir = DATA_DIR
    if faces_dir is None:
        faces_dir = DEFAULT_FACES_DIR
    if masks_dir is None:
        masks_dir = DEFAULT_MASKS_DIR

    own_outlet = False
    if outlet is None:
        outlet = create_demo_outlet()
        own_outlet = True

    # Scan stimulus directories. In demo mode we still scan but fall back to
    # synthetic placeholder IDs if the directory is empty, so demo runs
    # without KDEF files on disk.
    try:
        face_ids = scan_face_directory(faces_dir, config["min_face_identities"])
    except RuntimeError:
        if not demo:
            raise
        face_ids = [f"DEMO_{i:02d}_NES" for i in range(10)]
        log.warning("Demo mode: using %d placeholder face IDs", len(face_ids))

    try:
        mask_ids = scan_mask_directory(masks_dir)
    except RuntimeError:
        if not demo:
            raise
        mask_ids = [f"mondrian_{i:03d}" for i in range(10)]
        log.warning("Demo mode: using %d placeholder mask IDs", len(mask_ids))

    cleanup: Callable[[], None] = lambda: None  # noqa: E731
    if io is None:
        io, cleanup = _build_psychopy_io(
            demo=demo,
            face_ids=face_ids,
            mask_ids=mask_ids,
            faces_dir=faces_dir,
            masks_dir=masks_dir,
            face_size_px=config["face_size_px"],
        )

    if staircase is None:
        if demo:
            staircase = FixedSoaStaircase([150, 100, 80, 60, 40])
        else:
            staircase = _build_quest_staircase(config)

    rng = random.Random(rng_seed)

    key_map = {
        "seen": config["response_key_seen"],
        "unseen": config["response_key_unseen"],
        "unsure": config["response_key_unsure"],
    }

    all_records: list[TrialRecord] = []

    try:
        send_marker(outlet, "task03_start")

        # ----- Instructions -----
        send_marker(outlet, "task03_instructions_start")
        io.show_instructions(INSTRUCTIONS_TEXT, "space")
        send_marker(outlet, "task03_instructions_end")

        # ----- Practice -----
        send_marker(outlet, "task03_practice_start")
        n_practice_face = config["practice_face_trials"]
        n_practice_catch = config["practice_catch_trials"]
        practice_types = (
            ["face"] * n_practice_face + ["catch"] * n_practice_catch
        )
        rng.shuffle(practice_types)

        practice_face_schedule = build_face_schedule(
            face_ids, n_face_trials=n_practice_face, rng=rng
        )
        practice_face_iter = iter(practice_face_schedule)

        for i, ttype in enumerate(practice_types, start=1):
            face_id = next(practice_face_iter) if ttype == "face" else None
            mask_id = rng.choice(mask_ids)
            record = _run_one_trial(
                outlet=outlet,
                io=io,
                config=config,
                trial_number=i,
                phase="practice",
                trial_type=ttype,
                face_id=face_id,
                mask_id=mask_id,
                soa_ms=int(config["practice_soa_ms"]),
                key_map=key_map,
                staircase=None,  # practice does not update the staircase
            )
            all_records.append(record)

        send_marker(outlet, "task03_practice_end")
        io.show_instructions(POST_PRACTICE_TEXT, "space")

        # ----- Main task -----
        trial_types = build_trial_types(
            n_total=config["total_trials"],
            catch_proportion=config["catch_trial_proportion"],
            rng=rng,
        )
        n_main_face_trials = sum(1 for t in trial_types if t == "face")
        face_schedule = build_face_schedule(
            face_ids, n_face_trials=n_main_face_trials, rng=rng
        )
        face_iter = iter(face_schedule)

        trial_offset = len(all_records)
        for i, ttype in enumerate(trial_types, start=1):
            face_id = next(face_iter) if ttype == "face" else None
            mask_id = rng.choice(mask_ids)
            soa_ms = staircase.next_soa()
            record = _run_one_trial(
                outlet=outlet,
                io=io,
                config=config,
                trial_number=trial_offset + i,
                phase="main",
                trial_type=ttype,
                face_id=face_id,
                mask_id=mask_id,
                soa_ms=soa_ms,
                key_map=key_map,
                staircase=staircase,
            )
            all_records.append(record)

        log_path = _save_behavioral_log(all_records, participant_id, output_dir)

        send_marker(outlet, "task03_end")
        log.info(
            "Task 03 complete: %s (threshold estimate = %.1f ms)",
            log_path,
            staircase.threshold_estimate,
        )
        return log_path
    finally:
        cleanup()
        if own_outlet:
            del outlet
