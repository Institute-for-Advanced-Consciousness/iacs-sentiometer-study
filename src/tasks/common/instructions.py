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


def show_final_screen() -> None:
    """Open a fullscreen Pygame window with the end-of-session message.

    Called by the launcher immediately after the last task. Flips to the
    screen without waiting, then blocks until the RA presses Space or
    Enter. Pygame is used (not PsychoPy) so the display handoff from
    Task 05's Pygame window is seamless — no NSApplication stack swap,
    no Tk interference.
    """
    import pygame  # noqa: PLC0415

    if not pygame.get_init():
        pygame.init()
    if not pygame.display.get_init():
        pygame.display.init()
    if not pygame.font.get_init():
        pygame.font.init()

    # Reuse Task 05's already-visible, already-black surface if it's
    # still up. That surface was left alive on purpose (no pygame.quit()
    # in cleanup) so the handoff from ramp → completion screen is a
    # single redraw rather than a destroy + recreate. Destroying the
    # old window and recreating one exposes the macOS desktop for
    # ~100 ms — visible to the participant as a desktop flash.
    screen = pygame.display.get_surface()
    if screen is None:
        try:
            mode_size: tuple[int, int] = pygame.display.get_desktop_sizes()[0]
        except (AttributeError, IndexError):
            info = pygame.display.Info()
            mode_size = (info.current_w or 1920, info.current_h or 1080)
        screen = pygame.display.set_mode(mode_size, pygame.NOFRAME)

    pygame.display.set_caption("P013 — Session Complete")
    pygame.event.set_grab(False)
    surf_w, surf_h = screen.get_size()
    title_font = pygame.font.SysFont(None, 72)
    body_font = pygame.font.SysFont(None, 48)
    clock = pygame.time.Clock()

    def _wrap(text: str, font_obj, max_width_px: int) -> list[str]:
        out: list[str] = []
        for paragraph in text.split("\n"):
            if not paragraph.strip():
                out.append("")
                continue
            current: list[str] = []
            for word in paragraph.split():
                trial = (" ".join(current + [word])).strip()
                if font_obj.size(trial)[0] <= max_width_px or not current:
                    current.append(word)
                else:
                    out.append(" ".join(current))
                    current = [word]
            if current:
                out.append(" ".join(current))
        return out

    title = "You have completed all the tasks."
    body = "Please use the intercom to let the RAs know you've finished."

    # White background + black text so the handoff from Task 05's white
    # Pygame surface to this completion screen has no background change —
    # only the text is added.
    screen.fill((255, 255, 255))
    max_w = int(0.8 * surf_w)

    title_lines = _wrap(title, title_font, max_w)
    body_lines = _wrap(body, body_font, max_w)
    gap_px = 48
    title_h = title_font.get_linesize() * len(title_lines)
    body_h = body_font.get_linesize() * len(body_lines)
    total_h = title_h + gap_px + body_h
    y = (surf_h - total_h) // 2
    for line in title_lines:
        surf = title_font.render(line, True, (0, 0, 0))
        rect = surf.get_rect(center=(surf_w // 2, y + title_font.get_linesize() // 2))
        screen.blit(surf, rect)
        y += title_font.get_linesize()
    y += gap_px
    for line in body_lines:
        surf = body_font.render(line, True, (30, 30, 30))
        rect = surf.get_rect(center=(surf_w // 2, y + body_font.get_linesize() // 2))
        screen.blit(surf, rect)
        y += body_font.get_linesize()
    pygame.display.flip()

    # Drain the event queue aggressively. Task 05 leaves the Pygame window
    # iconified for the full ramp duration, during which the OS (and Vayl's
    # overlay losing / regaining focus) can queue phantom keypress events.
    # If any of those land in the queue as KEYDOWN Space / Return they'd
    # dismiss the completion screen the instant we enter the wait loop —
    # the screen appears to "immediately close". Pump the queue twice with
    # a short sleep in between so the OS has a tick to settle, then clear.
    # A minimum on-screen time (``min_visible_s``) additionally guards
    # against any residual key events that arrive after the clear.
    pygame.event.pump()
    pygame.time.wait(50)
    pygame.event.pump()
    pygame.event.clear()

    min_visible_s = 0.75
    shown_at = pygame.time.get_ticks()
    waiting = True
    while waiting:
        elapsed_s = (pygame.time.get_ticks() - shown_at) / 1000.0
        for ev in pygame.event.get():
            if elapsed_s < min_visible_s:
                continue
            if ev.type == pygame.KEYDOWN and ev.key in (
                pygame.K_SPACE, pygame.K_RETURN, pygame.K_KP_ENTER,
            ):
                waiting = False
                break
        clock.tick(30)

    try:
        screen.fill((255, 255, 255))
        pygame.display.flip()
        pygame.display.quit()
        pygame.quit()
    except Exception:  # noqa: BLE001
        pass


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
