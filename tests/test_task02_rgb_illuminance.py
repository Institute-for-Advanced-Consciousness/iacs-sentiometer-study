"""Tests for src/tasks/02_rgb_illuminance/task.py.

Drives Task 02 headlessly with a mock ``TaskIO`` so PsychoPy is not required.
The end-to-end test runs the full lifecycle (instructions -> colors with ITIs
-> breaks -> end), captures markers via a real LSL inlet, and asserts every
required marker type appears, that the no-consecutive-same-color constraint
holds in the recorded sequence, and that the CSV columns / pure-RGB values
are correct.
"""

from __future__ import annotations

import csv
import importlib
import random
from pathlib import Path

import pytest
from pylsl import StreamInlet, local_clock, resolve_byprop

from tasks.common.config import get_task_config, load_session_config
from tasks.common.lsl_markers import create_session_outlet, send_marker

rgb_task = importlib.import_module("tasks.02_rgb_illuminance.task")


# ----- Mock TaskIO -----------------------------------------------------------


class MockTaskIO:
    """Headless TaskIO: records every call, returns plausible LSL timestamps."""

    def __init__(self) -> None:
        self.color_calls: list[str] = []
        self.iti_calls: int = 0
        self.break_frames: list[int] = []
        self.shown_screens: list[str] = []

    def show_color(self, color_name: str) -> float:
        self.color_calls.append(color_name)
        return local_clock()

    def show_gray_fixation(self) -> float:
        self.iti_calls += 1
        return local_clock()

    def show_instructions(self, text: str, wait_key: str) -> None:
        self.shown_screens.append(text)

    def show_break_frame(self, remaining_seconds: int) -> None:
        self.break_frames.append(remaining_seconds)

    def check_escape(self) -> None:
        return None

    def wait(self, seconds: float) -> None:
        return None


# ----- Inlet helpers ---------------------------------------------------------


def _drain_inlet(inlet: StreamInlet) -> list[str]:
    markers: list[str] = []
    while True:
        sample, _ = inlet.pull_sample(timeout=0.2)
        if sample is None:
            break
        markers.append(sample[0])
    return markers


@pytest.fixture()
def captured_marker_outlet():
    outlet = create_session_outlet("RGB_TEST")
    streams = resolve_byprop("source_id", "P013_RGB_TEST", minimum=1, timeout=5.0)
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


class TestBuildColorSequence:
    def test_total_count(self):
        seq = rgb_task.build_color_sequence(100, ["red", "green", "blue"])
        assert len(seq) == 300

    def test_per_color_counts_full_size(self):
        seq = rgb_task.build_color_sequence(
            100, ["red", "green", "blue"], rng=random.Random(0)
        )
        assert seq.count("red") == 100
        assert seq.count("green") == 100
        assert seq.count("blue") == 100

    def test_no_consecutive_repeats_full_size(self):
        for seed in range(20):
            seq = rgb_task.build_color_sequence(
                100, ["red", "green", "blue"], rng=random.Random(seed)
            )
            for i in range(1, len(seq)):
                assert seq[i] != seq[i - 1], (
                    f"seed={seed}: consecutive {seq[i - 1]} at index {i}"
                )

    def test_no_consecutive_repeats_demo_size(self):
        for seed in range(20):
            seq = rgb_task.build_color_sequence(
                5, ["red", "green", "blue"], rng=random.Random(seed)
            )
            assert len(seq) == 15
            assert seq.count("red") == 5
            assert seq.count("green") == 5
            assert seq.count("blue") == 5
            for i in range(1, len(seq)):
                assert seq[i] != seq[i - 1]

    def test_deterministic_with_seed(self):
        seq_a = rgb_task.build_color_sequence(
            10, ["red", "green", "blue"], rng=random.Random(42)
        )
        seq_b = rgb_task.build_color_sequence(
            10, ["red", "green", "blue"], rng=random.Random(42)
        )
        assert seq_a == seq_b


class TestIsBreakTrial:
    def test_explicit_list_takes_precedence(self):
        cfg = {"break_after_trials": [5, 10], "break_interval_trials": 100}
        assert rgb_task.is_break_trial(5, 15, cfg) is True
        assert rgb_task.is_break_trial(10, 15, cfg) is True
        assert rgb_task.is_break_trial(7, 15, cfg) is False
        # The interval value is ignored when the explicit list is set.
        assert rgb_task.is_break_trial(100, 300, cfg) is False

    def test_modular_interval_excludes_last_trial(self):
        cfg = {"break_interval_trials": 100}
        assert rgb_task.is_break_trial(100, 300, cfg) is True
        assert rgb_task.is_break_trial(200, 300, cfg) is True
        # Trial 300 is the last trial -- no break after it.
        assert rgb_task.is_break_trial(300, 300, cfg) is False
        assert rgb_task.is_break_trial(150, 300, cfg) is False

    def test_zero_interval_never_breaks(self):
        assert rgb_task.is_break_trial(100, 300, {}) is False
        assert rgb_task.is_break_trial(100, 300, {"break_interval_trials": 0}) is False


# ----- End-to-end simulated run ---------------------------------------------


class TestSimulatedRun:
    @pytest.fixture()
    def small_config(self) -> dict:
        cfg = get_task_config(load_session_config(), "task02_rgb_illuminance")
        cfg["trials_per_color"] = 3  # 9 total trials
        cfg["trial_duration_min_s"] = 0.001
        cfg["trial_duration_max_s"] = 0.001
        cfg["iti_duration_ms"] = 1
        cfg["break_after_trials"] = [3, 6]
        cfg["break_duration_s"] = 1
        return cfg

    def test_full_lifecycle_emits_all_markers(
        self, captured_marker_outlet, small_config: dict, tmp_path: Path
    ):
        outlet, inlet = captured_marker_outlet
        mock = MockTaskIO()

        log_path = rgb_task.run(
            outlet=outlet,
            config=small_config,
            participant_id="PYTEST_T02",
            io=mock,
            rng_seed=42,
            output_dir=tmp_path,
        )

        markers = _drain_inlet(inlet)
        marker_set = set(markers)

        expected = {
            "task02_start",
            "task02_end",
            "task02_instructions_start",
            "task02_instructions_end",
            "task02_color_red",
            "task02_color_green",
            "task02_color_blue",
            "task02_iti",
            "task02_break_start",
            "task02_break_end",
        }
        missing = expected - marker_set
        assert not missing, f"Missing marker types: {sorted(missing)}"

        # Counts: 9 trials -> 9 ITIs, 2 breaks (after trials 3 and 6, not 9)
        assert markers.count("task02_iti") == 9
        assert markers.count("task02_break_start") == 2
        assert markers.count("task02_break_end") == 2

        # Color counts: 3 of each
        assert markers.count("task02_color_red") == 3
        assert markers.count("task02_color_green") == 3
        assert markers.count("task02_color_blue") == 3

        # Ordering sanity
        assert markers.index("task02_start") < markers.index("task02_end")
        assert markers[-1] == "task02_end"
        first_color_idx = next(
            i for i, m in enumerate(markers) if m.startswith("task02_color_")
        )
        assert markers.index("task02_instructions_end") < first_color_idx

        # Behavioral log
        assert log_path.exists()
        with open(log_path, newline="") as fh:
            rows = list(csv.DictReader(fh))
        assert len(rows) == 9
        assert list(rows[0].keys()) == [
            "trial_number",
            "color",
            "color_rgb",
            "onset_time",
            "offset_time",
            "duration_s",
            "iti_onset_time",
        ]
        # No consecutive same color in the recorded sequence
        for i in range(1, len(rows)):
            assert rows[i]["color"] != rows[i - 1]["color"], (
                f"Consecutive same color at row {i}"
            )
        # Every RGB value is one of the three pure colors
        valid_rgbs = {"255,0,0", "0,255,0", "0,0,255"}
        for row in rows:
            assert row["color_rgb"] in valid_rgbs

    def test_demo_mode_one_break_after_trial_5(
        self, captured_marker_outlet, tmp_path: Path
    ):
        """Demo: 15 trials (5 per color), exactly 1 break after trial 5."""
        outlet, inlet = captured_marker_outlet
        mock = MockTaskIO()

        rgb_task.run(
            outlet=outlet,
            config=None,  # use shipped defaults; demo overrides apply on top
            participant_id="PYTEST_T02_DEMO",
            demo=True,
            io=mock,
            rng_seed=7,
            output_dir=tmp_path,
        )

        markers = _drain_inlet(inlet)
        assert markers.count("task02_color_red") == 5
        assert markers.count("task02_color_green") == 5
        assert markers.count("task02_color_blue") == 5
        assert markers.count("task02_iti") == 15
        assert markers.count("task02_break_start") == 1
        assert markers.count("task02_break_end") == 1

        # The single break should fall after trial 5: 5 colors before the
        # break, 10 after.
        color_indices = [
            i for i, m in enumerate(markers) if m.startswith("task02_color_")
        ]
        break_idx = markers.index("task02_break_start")
        colors_before = sum(1 for i in color_indices if i < break_idx)
        colors_after = sum(1 for i in color_indices if i > break_idx)
        assert colors_before == 5
        assert colors_after == 10
