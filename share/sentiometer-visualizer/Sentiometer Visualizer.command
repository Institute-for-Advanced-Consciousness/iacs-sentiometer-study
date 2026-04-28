#!/bin/bash
# Sentiometer Visualizer — double-click launcher for macOS.
#
# First run: installs `uv` (a small Python package manager) from its
# official source and downloads the libraries the visualizer needs into
# an isolated environment. Nothing installs into your system Python.
#
# Subsequent runs: launches in a couple of seconds.

set -e

# Resolve the directory this script lives in so it works no matter where
# the user double-clicks from (Desktop, Downloads, anywhere).
cd "$(dirname "$0")" || exit 1

# Finder-launched shells don't inherit the full user PATH. Re-add the
# common locations uv tends to live in.
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$HOME/.cargo/bin:$PATH"

if ! command -v uv >/dev/null 2>&1; then
  echo "================================================================"
  echo " First-time setup: installing 'uv' (Python package manager)"
  echo " (one-time, ~10 MB download from the official source)"
  echo "================================================================"
  echo ""
  if ! curl -LsSf https://astral.sh/uv/install.sh | sh; then
    echo ""
    echo "Could not install uv automatically."
    echo "Open Terminal and run this command yourself, then re-launch:"
    echo "  curl -LsSf https://astral.sh/uv/install.sh | sh"
    echo ""
    echo "Press Enter to close."
    read -r _
    exit 1
  fi
  export PATH="$HOME/.local/bin:$PATH"
  echo ""
fi

echo "Launching Sentiometer Visualizer…"
echo "(first run downloads matplotlib + numpy + pyserial — give it ~30 s)"
echo ""

# `uv run` reads the PEP 723 metadata block at the top of the .py file
# and installs everything declared there into an isolated environment.
uv run sentiometer_viz.py
status=$?

# Non-zero exit → keep the window open so the user can read the error.
if [ $status -ne 0 ]; then
  echo ""
  echo "Visualizer exited with status $status."
  echo "Press Enter to close this window."
  read -r _
fi
