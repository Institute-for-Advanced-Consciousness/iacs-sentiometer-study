"""Tests for src/tasks/01_oddball/task.py.

Drives the task headlessly with a mock ``TaskIO`` so PsychoPy is not required.
The test runs through instructions, two practice attempts (the first fails,
the second passes), and a small main block, then verifies that every marker
type required by the spec appears on the LSL stream and that the behavioral
CSV contains rows for both practice and main phases.

The task module lives in a directory whose name starts with a digit
(``01_oddball``), which Python's ``import`` statement cannot parse. We load it
via :func:`importlib.import_module`, which uses the file system directly.
"""

from __future__ import annotations

import csv
import importlib
from pathlib import Path
from typing import Any

import pytest
from pylsl import StreamInlet, resolve_byprop

from tasks.common.config import get_task_config, load_session_config
from tasks.common.lsl_markers import create_session_outlet, send_marker

oddball_task = importlib.import_module("tasks.01_oddball.task")


# ----- Mock TaskIO -----------------------------------------------------------


class MockTaskIO:
    """Headless ``TaskIO`` implementation that drives the task deterministically.

    Trial counting is keyed off the practice/main split passed in at
    construction time:

    * Trials 1 .. ``practice_trials`` (attempt 1): always misses (returns
      ``None`` for every response). With zero hits the practice gate fails.
    * Trials ``practice_trials + 1`` .. ``2 * practice_trials`` (attempt 2):
      hits every deviant, ignores every standard. Perfect performance — gate
      passes.
    * Trials ``2 * practice_trials + 1`` onwards (main block): forces at
      least one hit, one miss, and one false alarm so all three main-task
      response markers are exercised.
    """

    def __init__(self, practice_trials: int) -> None:
        self.practice_trials = practice_trials
        self._tone_count = 0
        self._main_deviant_count = 0
        self._main_standard_count = 0
        self._last_tone: str | None = None
        self.shown_screens: list[str] = []
        self.played_tones: list[str] = []

    def play_tone(self, tone_type: str) -> float:
        self._tone_count += 1
        self._last_tone = tone_type
        self.played_tones.append(tone_type)
        # Use the real LSL clock so timestamps in the CSV are plausible.
        from pylsl import local_clock  # noqa: PLC0415

        return local_clock()

    def wait_for_response(self, onset_lsl_time: float, window_s: float) -> float | None:
        n = self._tone_count
        practice_n = self.practice_trials
        last = self._last_tone

        if n <= practice_n:
            return None  # attempt 1: all miss

        if n <= 2 * practice_n:
            return 250.0 if last == "deviant" else None  # attempt 2: perfect

        # Main block: deterministic mix that exercises every response marker.
        if last == "deviant":
            self._main_deviant_count += 1
            # First main deviant: miss. Rest: hit.
            return None if self._main_deviant_count == 1 else 250.0

        self._main_standard_count += 1
        # First main standard: false alarm. Rest: correct rejection.
        return 200.0 if self._main_standard_count == 1 else None

    def show_screen(self, text: str, wait_key: str | None) -> None:
        self.shown_screens.append(text)

    def check_escape(self) -> None:
        return None

    def wait(self, seconds: float) -> None:
        return None


# ----- Inlet fixture ---------------------------------------------------------


def _drain_inlet(inlet: StreamInlet) -> list[str]:
    """Pull every available sample from *inlet* and return the marker strings."""
    markers: list[str] = []
    while True:
        sample, _ = inlet.pull_sample(timeout=0.2)
        if sample is None:
            break
        markers.append(sample[0])
    return markers


@pytest.fixture()
def captured_marker_outlet():
    """Yield ``(outlet, inlet)`` with the inlet's data channel already live.

    Mirrors the handshake pattern used in ``test_markers.py``: send dummy
    handshake markers until the inlet is fully connected, drain them, then
    hand control to the test.
    """
    outlet = create_session_outlet("ODDBALL_TEST")
    streams = resolve_byprop("source_id", "P013_ODDBALL_TEST", minimum=1, timeout=5.0)
    assert streams, "Could not resolve test marker stream"
    inlet = StreamInlet(streams[0])
    inlet.open_stream(timeout=5.0)

    for _ in range(50):
        send_marker(outlet, "__handshake__")
        sample, _ = inlet.pull_sample(timeout=0.1)
        if sample is not None:
            break
    else:
        pytest.fail("Inlet never connected after 50 handshake attempts")

    while True:
        extra, _ = inlet.pull_sample(timeout=0.05)
        if extra is None:
            break

    yield outlet, inlet
    del inlet
    del outlet


# ----- Pure-helper tests -----------------------------------------------------


class TestBuildTrialSequence:
    """Tests for the pure :func:`build_trial_sequence` helper."""

    def test_counts_match_probability(self):
        seq = oddball_task.build_trial_sequence(
            n_total=250, deviant_probability=0.20, max_consecutive_standards=8
        )
        assert len(seq) == 250
        assert seq.count("deviant") == 50
        assert seq.count("standard") == 200

    def test_practice_block_satisfies_max_run(self):
        """8 std + 2 dev with max_consec=3 is feasible — verify."""
        seq = oddball_task.build_trial_sequence(
            n_total=10, deviant_probability=0.20, max_consecutive_standards=3
        )
        assert _max_run(seq, "standard") <= 3

    def test_relaxes_when_infeasible(self, caplog):
        """Requesting max_consec=3 with 200/50 ratio is impossible; warn + relax."""
        with caplog.at_level("WARNING"):
            seq = oddball_task.build_trial_sequence(
                n_total=250, deviant_probability=0.20, max_consecutive_standards=3
            )
        assert any("infeasible" in r.message for r in caplog.records)
        # min feasible = ceil(200 / 51) = 4
        assert _max_run(seq, "standard") <= 4

    def test_deterministic_with_seed(self):
        import random as _random  # noqa: PLC0415

        seq_a = oddball_task.build_trial_sequence(
            n_total=20, deviant_probability=0.20, max_consecutive_standards=4,
            rng=_random.Random(123),
        )
        seq_b = oddball_task.build_trial_sequence(
            n_total=20, deviant_probability=0.20, max_consecutive_standards=4,
            rng=_random.Random(123),
        )
        assert seq_a == seq_b


def _max_run(seq: list[str], target: str) -> int:
    longest = current = 0
    for x in seq:
        if x == target:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


class TestComputePracticeMetrics:
    def test_perfect_performance(self):
        records = [
            _make_record("deviant", "hit"),
            _make_record("standard", "correct_rejection"),
            _make_record("deviant", "hit"),
            _make_record("standard", "correct_rejection"),
        ]
        hit, fa = oddball_task.compute_practice_metrics(records)
        assert hit == 1.0
        assert fa == 0.0

    def test_failed_performance(self):
        records = [
            _make_record("deviant", "miss"),
            _make_record("standard", "false_alarm"),
            _make_record("deviant", "miss"),
            _make_record("standard", "correct_rejection"),
        ]
        hit, fa = oddball_task.compute_practice_metrics(records)
        assert hit == 0.0
        assert fa == 0.5


def _make_record(tone_type: str, response_type: str) -> Any:
    return oddball_task.TrialRecord(
        trial_number=0,
        phase="practice",
        practice_attempt=1,
        tone_type=tone_type,
        tone_onset_time=0.0,
        response_time=None,
        response_type=response_type,
        rt_ms=None,
    )


# ----- End-to-end simulated run ---------------------------------------------


class TestSimulatedRun:
    """Drive the full task with a mock IO and verify markers + CSV."""

    @pytest.fixture()
    def small_config(self) -> dict:
        """A tiny config that exercises every code path quickly."""
        cfg = get_task_config(load_session_config(), "task01_oddball")
        cfg["practice_trials"] = 4
        cfg["practice_deviants"] = 2
        cfg["total_trials"] = 12
        cfg["deviant_probability"] = 0.25
        cfg["isi_min_ms"] = 0
        cfg["isi_max_ms"] = 0
        cfg["response_window_ms"] = 100
        cfg["max_consecutive_standards"] = 3
        return cfg

    def test_markers_emitted_for_full_lifecycle(
        self, captured_marker_outlet, small_config: dict, tmp_path: Path
    ):
        outlet, inlet = captured_marker_outlet
        mock_io = MockTaskIO(practice_trials=small_config["practice_trials"])

        log_path = oddball_task.run(
            outlet=outlet,
            config=small_config,
            participant_id="PYTEST_T01",
            io=mock_io,
            rng_seed=42,
            output_dir=tmp_path,
        )

        markers = _drain_inlet(inlet)
        marker_set = set(markers)

        expected = {
            "task01_start",
            "task01_end",
            "task01_instructions_start",
            "task01_instructions_end",
            "task01_practice_start",
            "task01_practice_end",
            "task01_practice_attempt_1",
            "task01_practice_attempt_2",
            "task01_practice_passed",
            "task01_practice_tone_standard",
            "task01_practice_tone_deviant",
            "task01_tone_standard",
            "task01_tone_deviant",
            "task01_response_hit",
            "task01_response_false_alarm",
            "task01_response_miss",
        }
        missing = expected - marker_set
        assert not missing, f"Missing marker types: {sorted(missing)}"

        # Practice attempts: 2 starts, 2 ends, attempt_1 + attempt_2, exactly 1 passed
        assert markers.count("task01_practice_start") == 2
        assert markers.count("task01_practice_end") == 2
        assert markers.count("task01_practice_passed") == 1

        # Ordering sanity: start before end, instructions before practice,
        # practice before main, end is last.
        assert markers.index("task01_start") < markers.index("task01_end")
        assert markers.index("task01_instructions_start") < markers.index(
            "task01_practice_start"
        )
        assert markers.index("task01_practice_passed") < markers.index(
            "task01_tone_standard"
        )
        assert markers[-1] == "task01_end"

        # Behavioral log written and contains both practice and main rows
        assert log_path.exists()
        with open(log_path, newline="") as fh:
            rows = list(csv.DictReader(fh))
        phases = {r["phase"] for r in rows}
        assert phases == {"practice", "main"}
        practice_attempts = {r["practice_attempt"] for r in rows if r["phase"] == "practice"}
        assert practice_attempts == {"1", "2"}
        main_rows = [r for r in rows if r["phase"] == "main"]
        assert len(main_rows) == small_config["total_trials"]

    def test_demo_mode_passes_practice_on_first_attempt(
        self, captured_marker_outlet, tmp_path: Path
    ):
        """In demo mode the practice gate passes regardless of accuracy."""
        outlet, inlet = captured_marker_outlet
        cfg = get_task_config(load_session_config(), "task01_oddball")
        cfg["isi_min_ms"] = 0
        cfg["isi_max_ms"] = 0
        cfg["response_window_ms"] = 50
        # MockTaskIO with practice_trials matching the demo config — attempt 1 will
        # always miss but demo mode forces a pass anyway.
        mock_io = MockTaskIO(practice_trials=cfg["practice_trials"])

        oddball_task.run(
            outlet=outlet,
            config=cfg,
            participant_id="PYTEST_T01_DEMO",
            demo=True,
            io=mock_io,
            rng_seed=7,
            output_dir=tmp_path,
        )

        markers = _drain_inlet(inlet)
        assert markers.count("task01_practice_start") == 1
        assert markers.count("task01_practice_passed") == 1
        assert "task01_practice_attempt_1" in markers
        assert "task01_practice_attempt_2" not in markers
