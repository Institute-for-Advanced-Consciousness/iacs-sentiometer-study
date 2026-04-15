"""Tests for src/tasks/03_backward_masking/task.py.

Drives Task 03 headlessly with a mock ``TaskIO`` and a fixed-SOA staircase so
PsychoPy and the QUEST handler are not required. The test creates temporary
face and mask directories on disk so ``scan_face_directory`` /
``scan_mask_directory`` can run against them, exercises the full practice ->
main lifecycle, and verifies every required marker type appears plus the
paradigm-level invariants (catch proportion, staircase updates on face-only
main trials, response key mapping, no-face-leak across catch trials).
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

masking_task = importlib.import_module("tasks.03_backward_masking.task")


# ----- Mock TaskIO -----------------------------------------------------------


class MockTaskIO:
    """Headless TaskIO that records every call and serves scripted responses.

    The *response_plan* is a callable that takes the current trial index
    (1-based across practice+main) and returns either a semantic label
    ('seen' / 'unseen' / 'unsure') or ``None`` for a timeout.
    """

    def __init__(self, response_plan) -> None:
        self.response_plan = response_plan
        self._trial_counter = 0
        self.fixation_calls = 0
        self.face_calls: list[str | None] = []
        self.mask_calls: list[str] = []
        self.prompts_shown = 0
        self.screens: list[str] = []

    def show_instructions(self, text: str, wait_key: str) -> None:
        self.screens.append(text)

    def show_fixation(self) -> float:
        self.fixation_calls += 1
        return local_clock()

    def show_face(self, face_id: str | None) -> float:
        self.face_calls.append(face_id)
        return local_clock()

    def show_mask(self, mask_id: str) -> float:
        self.mask_calls.append(mask_id)
        return local_clock()

    def show_response_prompt(self) -> None:
        self.prompts_shown += 1

    def wait_for_response(self, window_s: float, key_map: dict):
        self._trial_counter += 1
        label = self.response_plan(self._trial_counter)
        if label is None:
            return None, None
        return label, 250.0

    def check_escape(self) -> None:
        return None

    def wait(self, seconds: float) -> None:
        return None


# ----- Fixtures --------------------------------------------------------------


@pytest.fixture()
def stim_dirs(tmp_path: Path) -> tuple[Path, Path]:
    """Create empty PNG files for 12 neutral faces + 5 masks in *tmp_path*.

    scan_face_directory / scan_mask_directory only inspect filenames, so
    empty files with the right extensions and KDEF 'NE' infix are enough.
    """
    faces = tmp_path / "faces"
    masks = tmp_path / "masks"
    faces.mkdir()
    masks.mkdir()

    for i in range(12):
        gender = "F" if i % 2 == 0 else "M"
        (faces / f"A{gender}{i:02d}NES.png").touch()
    # A couple of non-neutral files that must be filtered out
    (faces / "AF01HAS.png").touch()
    (faces / "AM02SAS.png").touch()

    for i in range(5):
        (masks / f"mondrian_{i:03d}.png").touch()

    return faces, masks


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
    outlet = create_session_outlet("MASKING_TEST")
    streams = resolve_byprop(
        "source_id", "P013_MASKING_TEST", minimum=1, timeout=5.0
    )
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


class TestScanFaceDirectory:
    def test_filters_to_neutral(self, tmp_path: Path):
        for i in range(10):
            (tmp_path / f"AF{i:02d}NES.png").touch()
        (tmp_path / "AF01HAS.png").touch()  # happy; must be excluded
        (tmp_path / "AF01SAS.png").touch()  # sad; must be excluded
        ids = masking_task.scan_face_directory(tmp_path, min_identities=10)
        assert len(ids) == 10
        assert all("NE" in fid for fid in ids)

    def test_raises_when_too_few(self, tmp_path: Path):
        (tmp_path / "AF01NES.png").touch()
        (tmp_path / "AF02NES.png").touch()
        with pytest.raises(RuntimeError, match="need at least"):
            masking_task.scan_face_directory(tmp_path, min_identities=10)

    def test_raises_when_dir_missing(self, tmp_path: Path):
        with pytest.raises(RuntimeError, match="not found"):
            masking_task.scan_face_directory(
                tmp_path / "does_not_exist", min_identities=1
            )


class TestBuildTrialTypes:
    def test_catch_proportion(self):
        seq = masking_task.build_trial_types(
            n_total=100, catch_proportion=0.17, rng=random.Random(0)
        )
        assert seq.count("catch") == 17
        assert seq.count("face") == 83

    def test_demo_proportion(self):
        seq = masking_task.build_trial_types(
            n_total=20, catch_proportion=3 / 20, rng=random.Random(0)
        )
        assert seq.count("catch") == 3
        assert seq.count("face") == 17


class TestBuildFaceSchedule:
    def test_cycles_through_all_faces(self):
        faces = [f"F{i:02d}" for i in range(10)]
        sched = masking_task.build_face_schedule(
            faces, n_face_trials=25, rng=random.Random(0)
        )
        assert len(sched) == 25
        # In 25 trials with 10 faces, every face should appear at least twice.
        for fid in faces:
            assert sched.count(fid) >= 2


class TestFixedSoaStaircase:
    def test_cycles_through_soas(self):
        s = masking_task.FixedSoaStaircase([100, 80, 60])
        assert s.next_soa() == 100
        s.update(seen=True)
        assert s.next_soa() == 80
        s.update(seen=False)
        assert s.next_soa() == 60
        s.update(seen=True)
        assert s.next_soa() == 100  # wraps

    def test_threshold_estimate_is_mean(self):
        s = masking_task.FixedSoaStaircase([100, 80, 60])
        assert s.threshold_estimate == pytest.approx(80.0)


# ----- End-to-end simulated run ---------------------------------------------


class TestSimulatedRun:
    @pytest.fixture()
    def small_config(self) -> dict:
        cfg = get_task_config(load_session_config(), "task03_backward_masking")
        cfg["total_trials"] = 20
        cfg["catch_trial_proportion"] = 3 / 20  # exactly 3 catch
        cfg["practice_trials"] = 4
        cfg["practice_face_trials"] = 3
        cfg["practice_catch_trials"] = 1
        cfg["practice_soa_ms"] = 200
        cfg["fixation_duration_ms"] = 1
        cfg["mask_duration_ms"] = 1
        cfg["response_window_ms"] = 10
        cfg["min_face_identities"] = 10
        return cfg

    def test_full_lifecycle_emits_all_markers(
        self,
        captured_marker_outlet,
        small_config: dict,
        stim_dirs: tuple[Path, Path],
        tmp_path: Path,
    ):
        outlet, inlet = captured_marker_outlet
        faces_dir, masks_dir = stim_dirs

        # Response plan: cycle through all four response types so every
        # main-task response marker is exercised. Practice trials (1..4)
        # return "seen" so the participant sees clear familiarization.
        def response_plan(n: int) -> str | None:
            if n <= 4:
                return "seen"
            cycle = ["seen", "unseen", "unsure", None]
            return cycle[(n - 5) % len(cycle)]

        mock_io = MockTaskIO(response_plan=response_plan)
        staircase = masking_task.FixedSoaStaircase([120, 90, 60, 40])

        log_path = masking_task.run(
            outlet=outlet,
            config=small_config,
            participant_id="PYTEST_T03",
            io=mock_io,
            staircase=staircase,
            rng_seed=42,
            output_dir=tmp_path,
            faces_dir=faces_dir,
            masks_dir=masks_dir,
        )

        markers = _drain_inlet(inlet)
        marker_set = set(markers)

        expected = {
            "task03_start",
            "task03_end",
            "task03_instructions_start",
            "task03_instructions_end",
            "task03_practice_start",
            "task03_practice_end",
            "task03_practice_face_onset",
            "task03_practice_catch",
            "task03_practice_mask_onset",
            "task03_fixation_onset",
            "task03_face_onset",
            "task03_catch_trial",
            "task03_mask_onset",
            "task03_response_seen",
            "task03_response_unseen",
            "task03_response_unsure",
            "task03_response_timeout",
        }
        missing = expected - marker_set
        assert not missing, f"Missing marker types: {sorted(missing)}"

        # At least one SOA marker
        soa_markers = [m for m in markers if m.startswith("task03_soa_value_")]
        assert soa_markers, "No SOA markers emitted"
        # Exactly one SOA marker per main face-present trial
        assert len(soa_markers) == 17, (
            f"Expected 17 SOA markers (one per face-present main trial), "
            f"got {len(soa_markers)}"
        )
        # SOA marker format: task03_soa_value_NNN (3-digit zero-padded)
        for m in soa_markers:
            suffix = m.removeprefix("task03_soa_value_")
            assert len(suffix) == 3 and suffix.isdigit()

        # Ordering sanity
        assert markers[0] == "task03_start"
        assert markers[-1] == "task03_end"
        assert markers.index("task03_practice_end") < markers.index(
            "task03_fixation_onset"
        )

        # Catch-trial invariant: practice_catch and catch_trial markers
        # correspond to trials where show_face() received None.
        n_practice_catch = markers.count("task03_practice_catch")
        n_main_catch = markers.count("task03_catch_trial")
        n_none_face_calls = sum(1 for f in mock_io.face_calls if f is None)
        assert n_practice_catch + n_main_catch == n_none_face_calls
        assert n_main_catch == 3  # exactly 3 catch trials in small_config

        # Staircase invariant: updates ONLY on main face-present trials.
        # 17 face trials in main; practice should not update.
        assert len(staircase._soas) == 4  # unchanged
        # The FixedSoa staircase's internal _idx tracks updates.
        assert staircase._idx == 17

        # Behavioral log
        assert log_path.exists()
        with open(log_path, newline="") as fh:
            rows = list(csv.DictReader(fh))
        assert list(rows[0].keys()) == [
            "trial_number",
            "phase",
            "trial_type",
            "face_id",
            "mask_id",
            "soa_ms",
            "response",
            "rt_ms",
            "quest_threshold_estimate",
        ]
        assert len(rows) == 24  # 4 practice + 20 main
        # Catch rows have face_id == "none"
        for row in rows:
            if row["trial_type"] == "catch":
                assert row["face_id"] == "none"
            else:
                assert row["face_id"] != "none"
        # Practice rows have empty quest_threshold_estimate
        for row in rows:
            if row["phase"] == "practice":
                assert row["quest_threshold_estimate"] == ""
            elif row["trial_type"] == "face":
                assert row["quest_threshold_estimate"] != ""

    def test_response_key_mapping(
        self,
        captured_marker_outlet,
        small_config: dict,
        stim_dirs: tuple[Path, Path],
        tmp_path: Path,
    ):
        """Verify that key_map uses the configured keys, not hardcoded F/J/Space."""
        outlet, _inlet = captured_marker_outlet
        faces_dir, masks_dir = stim_dirs

        captured_key_map: dict = {}

        def grabbing_plan(n: int):
            return "seen"

        class SpyIO(MockTaskIO):
            def wait_for_response(self, window_s: float, key_map: dict):
                if not captured_key_map:
                    captured_key_map.update(key_map)
                return super().wait_for_response(window_s, key_map)

        mock_io = SpyIO(response_plan=grabbing_plan)

        # Override response keys to custom values to prove they propagate
        small_config["response_key_seen"] = "y"
        small_config["response_key_unseen"] = "n"
        small_config["response_key_unsure"] = "u"

        masking_task.run(
            outlet=outlet,
            config=small_config,
            participant_id="PYTEST_T03_KEYS",
            io=mock_io,
            staircase=masking_task.FixedSoaStaircase([100]),
            rng_seed=1,
            output_dir=tmp_path,
            faces_dir=faces_dir,
            masks_dir=masks_dir,
        )

        assert captured_key_map == {"seen": "y", "unseen": "n", "unsure": "u"}
