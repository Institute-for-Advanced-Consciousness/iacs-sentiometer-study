"""Instruction screen rendering.

Provides simple helpers to display text instructions and countdown timers
between (or within) tasks.  All text is white-on-dark, centred, and sized
for comfortable reading on the 24" iMac stimulus display.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from psychopy import core, event, visual

if TYPE_CHECKING:
    from psychopy.visual import Window

# Default text height in PsychoPy "height" units.  With a 1920×1080 display
# this works out to roughly 24 pt — legible from normal viewing distance.
_TEXT_HEIGHT = 0.04


def show_instructions(win: Window, text: str, wait_key: str = "space") -> None:
    """Render centred instruction text and wait for a keypress.

    Parameters
    ----------
    win:
        PsychoPy window.
    text:
        Instruction text to display (may contain newlines for paragraphs).
    wait_key:
        Key that dismisses the screen (default ``"space"``).
    """
    stim = visual.TextStim(
        win,
        text=text,
        color="white",
        height=_TEXT_HEIGHT,
        wrapWidth=1.5,  # allow long lines before wrapping
        alignText="center",
    )
    stim.draw()
    win.flip()
    # Poll keys with explicit event pumping. `event.waitKeys` alone can hang
    # on macOS when Tk is alive in the same process because pyglet's event
    # loop isn't being driven.
    event.clearEvents(eventType="keyboard")
    while True:
        try:
            win.winHandle.dispatch_events()
        except Exception:  # noqa: BLE001
            pass
        if event.getKeys(keyList=[wait_key]):
            break
        core.wait(0.01, hogCPUperiod=0.01)


def show_countdown(win: Window, seconds: int) -> None:
    """Display a visual countdown from *seconds* down to 1.

    Each number is displayed for one second.  The screen is cleared (flipped
    to black) after the final number.

    Parameters
    ----------
    win:
        PsychoPy window.
    seconds:
        Number of seconds to count down from (must be >= 1).
    """
    for remaining in range(seconds, 0, -1):
        stim = visual.TextStim(
            win,
            text=str(remaining),
            color="white",
            height=0.12,
        )
        stim.draw()
        win.flip()
        time.sleep(1.0)
    # Clear to black after countdown
    win.flip()
