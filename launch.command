#!/bin/bash
# P013 Session Launcher — double-click on macOS to bring up the GUI.
# Opens a Terminal window (that's what macOS does for any .command file)
# then runs the GUI via uv. Launch P013.app also delegates here so that
# Terminal's permissions handle TCC for the study folder.
#
# Resolve the directory this script lives in so it works no matter where
# the RA double-clicks from (Desktop shortcut, Finder, etc.).
cd "$(dirname "$0")" || exit 1

# Finder-launched shells don't inherit the full user PATH. Re-add the
# places uv tends to live so a fresh iMac still finds it.
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

echo "Launching P013 GUI from $(pwd)"
uv run python -m tasks.gui_launcher
exit_code=$?

# pyglet 1.5.x on macOS exits with code 1 when its ObjC text view can't
# be torn down cleanly on window close, even when the session itself
# completed fine. Treat 0 and 1 as clean close; only pause on >= 2 so
# the RA can see a real error before the Terminal window disappears.
if [ $exit_code -ge 2 ]; then
  echo ""
  echo "Launcher exited with code $exit_code."
  echo "Press Enter to close."
  read -r _
fi
