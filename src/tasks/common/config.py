"""Task configuration loader.

Each task directory contains a ``config.yaml`` with paradigm parameters that
mirror the IRB protocol exactly.  This module loads those files and optionally
merges CLI overrides on top so experimenters can tweak values for quick debug
runs (e.g. ``--n-trials 10``).
"""

from __future__ import annotations

from pathlib import Path

import yaml


def load_task_config(task_dir: str | Path, overrides: dict | None = None) -> dict:
    """Load a task's ``config.yaml`` and merge optional CLI overrides.

    Parameters
    ----------
    task_dir:
        Path to the task directory (e.g.
        ``"src/tasks/01_oddball"``).  Must contain a ``config.yaml`` file.
    overrides:
        Optional dict of key-value pairs that take precedence over YAML
        defaults.  Keys use the same names as the YAML file.  ``None``
        values in the dict are silently skipped so that unprovided CLI
        flags don't clobber defaults.

    Returns
    -------
    dict
        Merged configuration dictionary.

    Raises
    ------
    FileNotFoundError
        If ``config.yaml`` does not exist in *task_dir*.
    """
    config_path = Path(task_dir) / "config.yaml"
    if not config_path.exists():
        raise FileNotFoundError(
            f"No config.yaml found in {task_dir!s}. "
            f"Expected it at {config_path.resolve()}"
        )

    with open(config_path) as fh:
        config: dict = yaml.safe_load(fh) or {}

    if overrides:
        for key, value in overrides.items():
            if value is not None:
                config[key] = value

    return config
