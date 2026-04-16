@echo off
REM P013 Session Launcher — double-click on Windows to bring up the GUI.
REM PsychoPy stim tasks don't actually run on Windows in this repo; this
REM file exists mainly so RAs on Windows dev boxes can sanity-check the
REM GUI and the LSL outlet without touching a terminal.

cd /d "%~dp0"
echo Launching P013 GUI from %CD%
uv run python -m tasks.gui_launcher
if errorlevel 1 (
  echo.
  echo Launcher exited with errors. Press any key to close.
  pause >nul
)
