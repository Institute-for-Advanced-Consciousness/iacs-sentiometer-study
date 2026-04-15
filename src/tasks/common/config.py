"""Session configuration loader.

All configurable parameters for every task live in a single master file,
``config/session_defaults.yaml``. This module loads that file and exposes
helpers to pull out one task's section at a time. The launcher GUI loads the
master file at startup, optionally lets the RA edit values for the current
session, and then passes the relevant per-task dicts into each task's
``run()`` function.

Demo mode and other programmatic tweaks apply on top of the per-task dict via
the ``overrides`` argument to :func:`get_task_config`. Overrides are applied
to an in-memory copy and do not modify the YAML file on disk, so the shipped
defaults always reflect the IRB protocol.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SESSION_CONFIG_PATH = REPO_ROOT / "config" / "session_defaults.yaml"


def load_session_config(
    config_path: str | Path = DEFAULT_SESSION_CONFIG_PATH,
) -> dict:
    """Load the master session config YAML from disk.

    Parameters
    ----------
    config_path:
        Path to the session defaults YAML. Relative paths are resolved
        against the repo root so callers can pass e.g.
        ``"config/session_defaults.yaml"`` from anywhere.

    Returns
    -------
    dict
        Parsed YAML contents. An empty file returns an empty dict.

    Raises
    ------
    FileNotFoundError
        If *config_path* does not exist.
    """
    path = Path(config_path)
    if not path.is_absolute():
        path = REPO_ROOT / path
    if not path.exists():
        raise FileNotFoundError(
            f"Session config not found at {path.resolve()}. "
            f"Expected a master YAML file shipped with the repo."
        )
    with open(path) as fh:
        data: dict = yaml.safe_load(fh) or {}
    return data


def get_task_config(
    session_config: dict,
    task_name: str,
    overrides: dict | None = None,
) -> dict:
    """Extract one task's section from the loaded session config.

    Returns a **copy** of the task dict so that callers can mutate it
    (e.g. apply demo overrides) without leaking changes into the master
    config dict held by the launcher.

    Parameters
    ----------
    session_config:
        The dict returned by :func:`load_session_config`.
    task_name:
        Key of the task section (e.g. ``"task01_oddball"``,
        ``"task05_ssvep"``).
    overrides:
        Optional dict of values to merge on top of the task section.
        ``None`` values are silently skipped so unprovided CLI flags
        don't clobber defaults.

    Returns
    -------
    dict
        Merged per-task configuration.

    Raises
    ------
    KeyError
        If *task_name* is not a key in *session_config*.
    """
    if task_name not in session_config:
        available = sorted(k for k in session_config if k != "session")
        raise KeyError(
            f"Task {task_name!r} not found in session config. "
            f"Available task sections: {available}"
        )

    task_cfg: dict = dict(session_config[task_name])

    if overrides:
        for key, value in overrides.items():
            if value is not None:
                task_cfg[key] = value

    return task_cfg
