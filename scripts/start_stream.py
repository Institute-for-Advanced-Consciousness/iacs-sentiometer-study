#!/usr/bin/env python
"""
Quick launcher for the Sentiometer LSL stream.
Equivalent to: sentiometer stream

This script can be run directly without installing the package:
    python scripts/start_stream.py --port COM3
"""

import sys
from pathlib import Path

# Add src to path so we can run without installing
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sentiometer.cli import main

if __name__ == "__main__":
    main()
