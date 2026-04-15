"""Shared utilities for all task paradigms."""

from tasks.common.config import get_task_config, load_session_config
from tasks.common.lsl_markers import (
    create_demo_outlet,
    create_session_outlet,
    send_marker,
)

__all__ = [
    "create_demo_outlet",
    "create_session_outlet",
    "get_task_config",
    "load_session_config",
    "send_marker",
]
