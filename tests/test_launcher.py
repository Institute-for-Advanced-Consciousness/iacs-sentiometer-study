"""Tests for src/tasks/launcher.py.

The launcher orchestrates the whole session: it creates the marker outlet,
writes ``session_log.json``, and calls each of the five tasks in order.
These tests exercise the orchestration logic with a mock ``task_runner``
so we never actually import or invoke the real task modules. The
``interactive=False`` flag bypasses every Rich ``Prompt`` / ``Confirm``
call so no stdin is read during the test.

Covered:

* End-to-end ``run_session`` calls all five tasks in order and writes a
  valid ``session_log.json``.
* ``--skip-to N`` marks tasks 1..N-1 as skipped and starts from task N.
* ``demo=True`` propagates into every task_runner invocation.
* ``KeyboardInterrupt`` mid-session saves a partial ``session_log.json``
  with ``status='aborted'`` and ``aborted_during`` set to the in-progress
  task name; earlier tasks retain their ``completed`` status.
* ``skip_to`` out of range raises ``ValueError``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tasks import launcher as launcher_mod

# ----- Mock task runner ------------------------------------------------------


class RecordingRunner:
    """Mock ``task_runner`` that records every call and returns ``completed``."""

    def __init__(
        self,
        *,
        raise_on: str | None = None,
        raise_type: type[BaseException] = KeyboardInterrupt,
    ) -> None:
        self.raise_on = raise_on
        self.raise_type = raise_type
        self.calls: list[dict] = []

    def __call__(
        self,
        task_name: str,
        module_path: str,
        outlet,
        session_config: dict,
        participant_id: str,
        demo: bool,
        data_root: Path,
    ) -> tuple[str, str | None]:
        self.calls.append(
            {
                "task_name": task_name,
                "module_path": module_path,
                "participant_id": participant_id,
                "demo": demo,
                "data_root": data_root,
            }
        )
        if self.raise_on == task_name:
            raise self.raise_type
        return ("completed", None)


# ----- Tests -----------------------------------------------------------------


class TestFullRun:
    def test_all_tasks_called_in_order(self, tmp_path: Path):
        runner = RecordingRunner()
        result = launcher_mod.run_session(
            participant_id="PYTEST_LAUNCHER",
            demo=True,
            data_root=tmp_path,
            interactive=False,
            task_runner=runner,
        )

        assert result["status"] == "completed"
        task_sequence = [c["task_name"] for c in runner.calls]
        assert task_sequence == [
            "task01_oddball",
            "task02_rgb_illuminance",
            "task03_backward_masking",
            "task04_mind_state",
            "task05_ssvep",
        ]
        # Module paths match the importlib pattern
        for call in runner.calls:
            assert call["module_path"].startswith("tasks.")
            assert call["module_path"].endswith(".task")

    def test_session_log_is_written_and_valid(self, tmp_path: Path):
        runner = RecordingRunner()
        launcher_mod.run_session(
            participant_id="PYTEST_LOG",
            demo=True,
            data_root=tmp_path,
            interactive=False,
            task_runner=runner,
        )

        log_path = tmp_path / "PYTEST_LOG" / "session_log.json"
        assert log_path.exists()
        data = json.loads(log_path.read_text())

        # Required top-level fields
        assert data["participant_id"] == "PYTEST_LOG"
        assert data["status"] == "completed"
        assert data["session_date"]
        assert data["start_time"]
        assert data["end_time"]
        assert data["demo"] is True
        assert data["abort_reason"] is None
        assert data["aborted_during"] is None

        # Config snapshot
        assert "config_snapshot" in data
        assert "task01_oddball" in data["config_snapshot"]

        # System info
        assert "python" in data["system_info"]
        assert "platform" in data["system_info"]

        # Every task has a completed status with start + end timestamps
        for task_name in [
            "task01_oddball",
            "task02_rgb_illuminance",
            "task03_backward_masking",
            "task04_mind_state",
            "task05_ssvep",
        ]:
            task_entry = data["tasks"][task_name]
            assert task_entry["status"] == "completed"
            assert task_entry["start"]
            assert task_entry["end"]


class TestSkipTo:
    def test_skip_to_3_marks_early_tasks_skipped(self, tmp_path: Path):
        runner = RecordingRunner()
        launcher_mod.run_session(
            participant_id="PYTEST_SKIP",
            demo=True,
            skip_to=3,
            data_root=tmp_path,
            interactive=False,
            task_runner=runner,
        )

        # Only tasks 3, 4, 5 were actually called
        task_sequence = [c["task_name"] for c in runner.calls]
        assert task_sequence == [
            "task03_backward_masking",
            "task04_mind_state",
            "task05_ssvep",
        ]

        # Session log marks tasks 1 and 2 as skipped
        log_path = tmp_path / "PYTEST_SKIP" / "session_log.json"
        data = json.loads(log_path.read_text())
        assert data["tasks"]["task01_oddball"]["status"] == "skipped"
        assert data["tasks"]["task01_oddball"]["reason"] == "skip_to=3"
        assert data["tasks"]["task02_rgb_illuminance"]["status"] == "skipped"
        assert data["tasks"]["task03_backward_masking"]["status"] == "completed"
        assert data["tasks"]["task04_mind_state"]["status"] == "completed"
        assert data["tasks"]["task05_ssvep"]["status"] == "completed"

    def test_skip_to_out_of_range_raises(self, tmp_path: Path):
        with pytest.raises(ValueError, match="skip_to must be in"):
            launcher_mod.run_session(
                participant_id="PYTEST_OOR",
                demo=True,
                skip_to=0,
                data_root=tmp_path,
                interactive=False,
                task_runner=RecordingRunner(),
            )
        with pytest.raises(ValueError, match="skip_to must be in"):
            launcher_mod.run_session(
                participant_id="PYTEST_OOR",
                demo=True,
                skip_to=6,
                data_root=tmp_path,
                interactive=False,
                task_runner=RecordingRunner(),
            )


class TestDemoFlag:
    def test_demo_true_propagates_to_all_tasks(self, tmp_path: Path):
        runner = RecordingRunner()
        launcher_mod.run_session(
            participant_id="PYTEST_DEMO",
            demo=True,
            data_root=tmp_path,
            interactive=False,
            task_runner=runner,
        )
        assert all(c["demo"] is True for c in runner.calls)

    def test_demo_false_propagates_to_all_tasks(self, tmp_path: Path):
        runner = RecordingRunner()
        launcher_mod.run_session(
            participant_id="PYTEST_NOT_DEMO",
            demo=False,
            data_root=tmp_path,
            interactive=False,
            task_runner=runner,
        )
        assert all(c["demo"] is False for c in runner.calls)


class TestAbort:
    def test_ctrl_c_mid_session_saves_partial_log(self, tmp_path: Path):
        """KeyboardInterrupt during task 3 must produce an aborted session log."""
        runner = RecordingRunner(raise_on="task03_backward_masking")

        result = launcher_mod.run_session(
            participant_id="PYTEST_ABORT",
            demo=True,
            data_root=tmp_path,
            interactive=False,
            task_runner=runner,
        )

        # Result dict reflects the abort
        assert result["status"] == "aborted"
        assert result["aborted_during"] == "task03_backward_masking"
        assert "KeyboardInterrupt" in result["abort_reason"]
        assert result["end_time"] is not None

        # On-disk log matches
        log_path = tmp_path / "PYTEST_ABORT" / "session_log.json"
        data = json.loads(log_path.read_text())
        assert data["status"] == "aborted"
        assert data["aborted_during"] == "task03_backward_masking"

        # Earlier tasks still marked completed
        assert data["tasks"]["task01_oddball"]["status"] == "completed"
        assert data["tasks"]["task02_rgb_illuminance"]["status"] == "completed"

        # The in-progress task is marked aborted (not running)
        assert data["tasks"]["task03_backward_masking"]["status"] == "aborted"

        # Tasks 4 and 5 should not appear (never entered the loop)
        assert "task04_mind_state" not in data["tasks"]
        assert "task05_ssvep" not in data["tasks"]

        # Only tasks 1 and 2 ever reached the runner (task 3 raised before return)
        task_sequence = [c["task_name"] for c in runner.calls]
        assert task_sequence == [
            "task01_oddball",
            "task02_rgb_illuminance",
            "task03_backward_masking",
        ]


class TestTaskFailureNonFatal:
    def test_task_failure_recorded_but_session_continues(self, tmp_path: Path):
        """A task returning ('failed', error) should not abort the session."""

        def failing_runner(
            task_name, module_path, outlet, session_config,
            participant_id, demo, data_root,
        ):
            if task_name == "task02_rgb_illuminance":
                return ("failed", "RuntimeError: simulated failure")
            return ("completed", None)

        result = launcher_mod.run_session(
            participant_id="PYTEST_FAIL",
            demo=True,
            data_root=tmp_path,
            interactive=False,
            task_runner=failing_runner,
        )

        assert result["status"] == "completed"
        assert result["tasks"]["task02_rgb_illuminance"]["status"] == "failed"
        assert "simulated failure" in result["tasks"]["task02_rgb_illuminance"]["error"]
        # Later tasks still ran
        assert result["tasks"]["task03_backward_masking"]["status"] == "completed"
        assert result["tasks"]["task05_ssvep"]["status"] == "completed"
