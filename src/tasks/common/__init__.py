"""Shared utilities for all task paradigms."""

from tasks.common.config import load_task_config
from tasks.common.lsl_markers import (
    create_demo_outlet,
    create_session_outlet,
    send_marker,
)

__all__ = [
    "create_demo_outlet",
    "create_session_outlet",
    "load_task_config",
    "send_marker",
]
