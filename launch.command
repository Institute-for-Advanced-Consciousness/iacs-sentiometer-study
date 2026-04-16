#!/bin/bash
# P013 Session Launcher — double-click on macOS to bring up the GUI.
# Opens a Terminal window (required for double-click invocation) then
# hands off to `uv run`. Close the Terminal when the session is done.

# Resolve the directory this script lives in so it works no matter where
# the RA double-clicks from (Desktop shortcut, Finder, etc.).
cd "$(dirname "$0")" || exit 1

echo "Launching P013 GUI from $(pwd)"
uv run python -m tasks.gui_launcher
exit_code=$?

if [ $exit_code -ne 0 ]; then
  echo ""
  echo "Launcher exited with code $exit_code."
  echo "Press Enter to close."
  read -r _
fi
