"""Tests for tasks.common.config.

Verifies that the master session config loads correctly, that per-task
sections can be extracted, that overrides merge properly, and that the
shipped ``config/session_defaults.yaml`` contains a section for every task.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tasks.common.config import (
    DEFAULT_SESSION_CONFIG_PATH,
    get_task_config,
    load_session_config,
)

TASK_NAMES = [
    "task01_oddball",
    "task02_rgb_illuminance",
    "task03_backward_masking",
    "task04_mind_state",
    "task05_ssvep",
]


@pytest.fixture()
def tmp_session_config(tmp_path: Path) -> Path:
    """Write a minimal session config to a temp file and return its path."""
    data = {
        "session": {"participant_id": "", "session_date": ""},
        "task01_oddball": {
            "total_trials": 250,
            "deviant_probability": 0.20,
            "isi_min_ms": 1100,
            "isi_max_ms": 1500,
        },
        "task02_rgb_illuminance": {
            "trials_per_color": 100,
            "colors": ["red", "green", "blue"],
        },
    }
    path = tmp_path / "session_defaults.yaml"
    with open(path, "w") as fh:
        yaml.safe_dump(data, fh)
    return path


class TestLoadSessionConfig:
    """Tests for load_session_config."""

    def test_loads_yaml(self, tmp_session_config: Path):
        cfg = load_session_config(tmp_session_config)
        assert "task01_oddball" in cfg
        assert cfg["task01_oddball"]["total_trials"] == 250

    def test_missing_file_raises(self, tmp_path: Path):
        missing = tmp_path / "does_not_exist.yaml"
        with pytest.raises(FileNotFoundError, match="Session config not found"):
            load_session_config(missing)

    def test_empty_yaml_returns_empty_dict(self, tmp_path: Path):
        path = tmp_path / "empty.yaml"
        path.write_text("")
        cfg = load_session_config(path)
        assert cfg == {}

    def test_default_path_loads_shipped_config(self):
        """The shipped config/session_defaults.yaml must be loadable."""
        cfg = load_session_config()
        assert isinstance(cfg, dict)
        assert "session" in cfg

    def test_relative_path_resolves_against_repo_root(self):
        """A relative path should resolve relative to the repo root."""
        cfg = load_session_config("config/session_defaults.yaml")
        assert isinstance(cfg, dict)
        assert "task01_oddball" in cfg


class TestGetTaskConfig:
    """Tests for get_task_config."""

    def test_extracts_section(self, tmp_session_config: Path):
        session = load_session_config(tmp_session_config)
        task_cfg = get_task_config(session, "task01_oddball")
        assert task_cfg["total_trials"] == 250

    def test_returned_dict_is_a_copy(self, tmp_session_config: Path):
        """Mutating the returned dict must not affect the master config."""
        session = load_session_config(tmp_session_config)
        task_cfg = get_task_config(session, "task01_oddball")
        task_cfg["total_trials"] = 10
        assert session["task01_oddball"]["total_trials"] == 250

    def test_overrides_merge(self, tmp_session_config: Path):
        session = load_session_config(tmp_session_config)
        task_cfg = get_task_config(
            session, "task01_oddball", overrides={"total_trials": 10}
        )
        assert task_cfg["total_trials"] == 10
        assert task_cfg["isi_min_ms"] == 1100  # untouched

    def test_none_overrides_ignored(self, tmp_session_config: Path):
        session = load_session_config(tmp_session_config)
        task_cfg = get_task_config(
            session, "task01_oddball", overrides={"total_trials": None}
        )
        assert task_cfg["total_trials"] == 250

    def test_new_override_keys_added(self, tmp_session_config: Path):
        session = load_session_config(tmp_session_config)
        task_cfg = get_task_config(
            session, "task01_oddball", overrides={"demo": True}
        )
        assert task_cfg["demo"] is True
        assert task_cfg["total_trials"] == 250

    def test_unknown_task_raises(self, tmp_session_config: Path):
        session = load_session_config(tmp_session_config)
        with pytest.raises(KeyError, match="task99_bogus"):
            get_task_config(session, "task99_bogus")


class TestShippedSessionDefaults:
    """Verify the shipped session_defaults.yaml is complete and valid."""

    @pytest.fixture(scope="class")
    def shipped_config(self) -> dict:
        assert DEFAULT_SESSION_CONFIG_PATH.exists(), (
            f"Expected {DEFAULT_SESSION_CONFIG_PATH} to be shipped with the repo"
        )
        return load_session_config()

    def test_has_session_section(self, shipped_config: dict):
        assert "session" in shipped_config
        assert "participant_id" in shipped_config["session"]

    @pytest.mark.parametrize("task_name", TASK_NAMES)
    def test_has_task_section(self, shipped_config: dict, task_name: str):
        assert task_name in shipped_config, (
            f"session_defaults.yaml is missing section for {task_name}"
        )
        task_cfg = get_task_config(shipped_config, task_name)
        assert isinstance(task_cfg, dict)
        assert len(task_cfg) > 0, f"{task_name} section is empty"

    def test_oddball_matches_erp_core(self, shipped_config: dict):
        """Task 01 defaults must match the ERP CORE protocol."""
        cfg = get_task_config(shipped_config, "task01_oddball")
        assert cfg["tone_duration_ms"] == 100
        assert cfg["rise_fall_ms"] == 10
        assert cfg["isi_min_ms"] == 1100
        assert cfg["isi_max_ms"] == 1500
