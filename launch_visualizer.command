#!/bin/bash
# Sentiometer Mac Visualizer launcher — double-click on macOS.
# Opens a Terminal window (macOS does this for any .command file), then
# runs the visualizer GUI via uv. The .command file lives in the repo
# root so a Finder shortcut can target it directly.

cd "$(dirname "$0")" || exit 1

# Finder-launched shells don't inherit the full user PATH. Re-add the
# common locations uv tends to live in so a fresh Mac still finds it.
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

if ! command -v uv >/dev/null 2>&1; then
  echo "Could not find 'uv' on PATH."
  echo "Install it from https://docs.astral.sh/uv/ and try again."
  echo ""
  echo "Press Enter to close."
  read -r _
  exit 1
fi

echo "Launching Sentiometer Visualizer from $(pwd)"
echo "(first run will install matplotlib + tk extras — give it a minute)"
echo ""

uv run --extra viz python scripts/mac_visualizer.py
exit_code=$?

if [ $exit_code -ne 0 ]; then
  echo ""
  echo "Visualizer exited with code $exit_code."
  echo "Press Enter to close."
  read -r _
fi
