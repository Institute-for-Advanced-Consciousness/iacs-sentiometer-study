"""PsychoPy window management and shared display utilities.

Provides helpers for creating a full-screen PsychoPy window on the 24" iMac
stimulus display, drawing a standard fixation cross, and handling the Escape
key gracefully.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from psychopy import event, visual

if TYPE_CHECKING:
    from psychopy.visual import Window

log = logging.getLogger(__name__)

# Collected by flip_and_log; tasks can read this for post-hoc timing checks.
flip_timestamps: list[float] = []


def create_window(fullscreen: bool = True) -> Window:
    """Create a PsychoPy window configured for the stimulus display.

    Parameters
    ----------
    fullscreen:
        If *True* (default / production), open a borderless full-screen
        window.  Pass *False* for windowed debug/demo runs.

    Returns
    -------
    Window
        A PsychoPy ``visual.Window`` ready for drawing.
    """
    win = visual.Window(
        fullscr=fullscreen,
        color=(-1, -1, -1),  # black
        units="height",
        waitBlanking=True,
        allowGUI=not fullscreen,
    )
    log.info(
        "PsychoPy window created: %s, size=%s, refresh=%.1f Hz",
        "fullscreen" if fullscreen else "windowed",
        win.size,
        win.getActualFrameRate(nIdentical=10, nMaxFrames=100, nWarmUpFrames=10) or 0,
    )
    return win


# ----- fixation cross --------------------------------------------------------

# Thin cross built from two overlapping rectangles.
_CROSS_LENGTH = 0.03  # in height units
_CROSS_WIDTH = 0.003


def draw_fixation(win: Window, color: str = "white") -> None:
    """Draw a thin fixation cross at the centre of *win*.

    Call this before :func:`flip_and_log` — it only adds the shapes to the
    back-buffer; it does **not** flip.

    Parameters
    ----------
    win:
        Target PsychoPy window.
    color:
        Any PsychoPy colour name (default ``"white"``).
    """
    visual.Rect(
        win, width=_CROSS_LENGTH, height=_CROSS_WIDTH, fillColor=color, lineColor=color
    ).draw()
    visual.Rect(
        win, width=_CROSS_WIDTH, height=_CROSS_LENGTH, fillColor=color, lineColor=color
    ).draw()


# ----- flip helper ------------------------------------------------------------


def flip_and_log(win: Window) -> float:
    """Flip the window and record the timestamp.

    Returns
    -------
    float
        The timestamp returned by ``win.flip()``.  Also appended to the
        module-level :data:`flip_timestamps` list for post-hoc review.
    """
    ts = win.flip()
    flip_timestamps.append(ts)
    return ts


def reset_flip_log() -> list[float]:
    """Return the accumulated flip timestamps and clear the log.

    Useful between tasks so each task gets a clean list.
    """
    stamps = flip_timestamps.copy()
    flip_timestamps.clear()
    return stamps


# ----- escape handling --------------------------------------------------------


class EscapePressedError(Exception):
    """Raised by :func:`check_escape` when the participant confirms exit."""


def check_escape(win: Window) -> None:
    """If Escape has been pressed, show a confirmation dialog and maybe exit.

    Call this once per trial or at natural breakpoints.  If the participant
    presses Escape *and* confirms, :class:`EscapePressedError` is raised so
    the calling task can clean up.

    Parameters
    ----------
    win:
        The active PsychoPy window (used to draw the dialog).

    Raises
    ------
    EscapePressedError
        If the participant confirms they want to quit.
    """
    keys = event.getKeys(keyList=["escape"])
    if not keys:
        return

    # Show confirmation overlay
    msg = visual.TextStim(
        win,
        text="Press Y to quit, N to continue",
        color="white",
        height=0.04,
    )
    msg.draw()
    win.flip()

    # Wait for Y or N
    response = event.waitKeys(keyList=["y", "n"])
    if response and response[0] == "y":
        log.info("Escape confirmed — raising EscapePressedError")
        raise EscapePressedError
    # If 'n', just return and resume
    log.info("Escape cancelled — resuming task")
