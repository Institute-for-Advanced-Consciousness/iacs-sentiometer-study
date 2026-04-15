"""Tests for tasks.common.config.

Verifies that config loading, override merging, and error handling work
correctly.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tasks.common.config import load_task_config


@pytest.fixture()
def tmp_task_dir(tmp_path: Path) -> Path:
    """Create a temporary task directory with a sample config.yaml."""
    config = {
        "n_trials": 250,
        "isi_min_ms": 1000,
        "isi_max_ms": 1200,
        "standard_freq_hz": 1000,
        "deviant_freq_hz": 2000,
    }
    config_path = tmp_path / "config.yaml"
    with open(config_path, "w") as fh:
        yaml.safe_dump(config, fh)
    return tmp_path


class TestLoadTaskConfig:
    """Tests for load_task_config."""

    def test_loads_yaml(self, tmp_task_dir: Path):
        """Config values should match what was written."""
        cfg = load_task_config(tmp_task_dir)
        assert cfg["n_trials"] == 250
        assert cfg["standard_freq_hz"] == 1000

    def test_overrides_merge(self, tmp_task_dir: Path):
        """CLI overrides should take precedence over YAML defaults."""
        cfg = load_task_config(tmp_task_dir, overrides={"n_trials": 10})
        assert cfg["n_trials"] == 10
        # Other values should be unchanged
        assert cfg["isi_min_ms"] == 1000

    def test_none_overrides_ignored(self, tmp_task_dir: Path):
        """None values in overrides should not clobber defaults."""
        cfg = load_task_config(tmp_task_dir, overrides={"n_trials": None})
        assert cfg["n_trials"] == 250

    def test_new_override_keys_added(self, tmp_task_dir: Path):
        """Override keys not in YAML should be added to the config."""
        cfg = load_task_config(tmp_task_dir, overrides={"demo": True})
        assert cfg["demo"] is True
        assert cfg["n_trials"] == 250

    def test_missing_config_raises(self, tmp_path: Path):
        """FileNotFoundError should be raised if config.yaml is missing."""
        empty_dir = tmp_path / "no_config"
        empty_dir.mkdir()
        with pytest.raises(FileNotFoundError, match="No config.yaml found"):
            load_task_config(empty_dir)

    def test_empty_yaml_returns_empty_dict(self, tmp_path: Path):
        """An empty config.yaml should return an empty dict, not None."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text("")
        cfg = load_task_config(tmp_path)
        assert cfg == {}

    def test_accepts_string_path(self, tmp_task_dir: Path):
        """Should accept a string path as well as a Path object."""
        cfg = load_task_config(str(tmp_task_dir))
        assert cfg["n_trials"] == 250


class TestRealTaskConfigs:
    """Verify that each task's config.yaml in the repo is loadable."""

    _task_dirs = [
        "src/tasks/01_oddball",
        "src/tasks/02_rgb_illuminance",
        "src/tasks/03_backward_masking",
        "src/tasks/04_mind_state",
        "src/tasks/05_ssvep",
    ]

    @pytest.mark.parametrize("task_dir", _task_dirs)
    def test_task_config_loads(self, task_dir: str):
        """Each task config.yaml should be loadable without errors."""
        repo_root = Path(__file__).resolve().parent.parent
        full_path = repo_root / task_dir
        cfg = load_task_config(full_path)
        assert isinstance(cfg, dict)
