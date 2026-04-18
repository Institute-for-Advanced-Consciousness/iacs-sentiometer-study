"""Launch Task 03 (Backward Masking) standalone and save everything locally.

One-off helper used to collect a diagnostic pass for the
"fixed-SOA Phase B" design conversation. It runs the task exactly as it
would run inside a real session (full 275-trial QUEST, no demo overrides),
writes the behavioural CSV to ``data/{pid}/``, and prints the paths so we
can analyse them afterward.

Usage::

    uv run python scripts/run_task03_diagnostic.py --pid T03_DIAG_001

Not part of the production session flow; lives in ``scripts/`` to keep it
out of the launcher and out of the test suite.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from tasks.common.config import get_task_config, load_session_config  # noqa: E402
from tasks.common.lsl_markers import create_session_outlet  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pid",
        default=f"T03_DIAG_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        help="Participant ID for file naming (default: T03_DIAG_<timestamp>).",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Demo mode: 20 trials, fixed SOAs (not useful for diagnostic).",
    )
    args = parser.parse_args()

    data_dir = REPO_ROOT / "data"
    data_dir.mkdir(exist_ok=True)

    print(f"Launching Task 03 — participant_id={args.pid}")
    print(f"Behavioural CSV will land in: {data_dir / args.pid}/")
    print()

    task_mod = importlib.import_module("tasks.03_backward_masking.task")
    cfg = get_task_config(load_session_config(), "task03_backward_masking")
    outlet = create_session_outlet(args.pid)

    try:
        csv_path = task_mod.run(
            outlet=outlet,
            config=cfg,
            participant_id=args.pid,
            demo=args.demo,
            output_dir=data_dir,
        )
        print()
        print(f"Behavioural log saved to: {csv_path}")
    finally:
        del outlet


if __name__ == "__main__":
    main()
