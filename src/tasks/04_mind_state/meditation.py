"""Task 04 meditation block: audio -> gong -> silence -> gong -> open-eyes.

The block has no visual content during the meditation itself (the screen is
black while the participant's eyes are closed). The "press spacebar when
ready and close your eyes immediately after" prompt is owned by
``_run_break`` (the prior bridge phase), so this block starts with the
participant's eyes already closed.

Flow:

1. Black screen + recorded meditation-instructions audio (mp3).
2. First gong rings.
3. Silent ``duration_s`` timer (default 6 min between gongs).
4. Second gong rings.
5. Completion screen — participant opens eyes and presses spacebar.

Markers emitted (in order):

* ``task04_meditation_audio_start`` — mp3 begins
* ``task04_meditation_audio_end``   — mp3 finishes
* ``task04_meditation_gong_start``  — first gong
* ``task04_meditation_start``       — silent timer begins
* ``task04_meditation_gong_end``    — second gong
* ``task04_meditation_end``         — completion screen dismissed

Like :mod:`game`, this module never touches Pygame directly -- it
delegates every side effect to a ``TaskIO`` so it can run headlessly in
tests.
"""

from __future__ import annotations

from pylsl import StreamOutlet, local_clock

from tasks.common.lsl_markers import send_marker

MEDITATION_COMPLETE_TEXT = (
    "The meditation is complete. Please open your eyes.\n\n"
    "Press spacebar to continue."
)


def run_meditation_block(
    outlet: StreamOutlet,
    io,
    duration_s: float,
    audio_speed: float = 1.0,
) -> list[dict]:
    """Run the meditation block end-to-end.

    Parameters
    ----------
    outlet:
        Shared P013 marker outlet.
    io:
        Pygame-backed :class:`TaskIO` (or a mock in tests). Must provide
        ``show_text_and_wait``, ``show_black_screen``, ``play_gong``,
        ``play_instructions_audio``, and ``wait``.
    duration_s:
        Silence between the two gongs, in seconds. Default config is
        360 s (6 min).
    audio_speed:
        Multiplier for the meditation-instructions mp3 playback rate.
        1.0 = native; 3.0 = demo fast-forward. Passed straight to
        ``io.play_instructions_audio``.

    Returns the behavioural-log entries (one dict per phase boundary).
    """
    log: list[dict] = []

    def _mark(kind: str, **extra) -> None:
        log.append(
            {
                "timestamp": local_clock(),
                "phase": "meditation",
                "type": kind,
                **extra,
            }
        )

    # ----- 1. Guided-meditation audio over a black screen -----
    # No instructions text here — the prior break phase already showed
    # the "press space when ready and close your eyes" prompt.
    io.show_black_screen()
    send_marker(outlet, "task04_meditation_audio_start")
    _mark("audio_start", audio_speed=audio_speed)
    io.play_instructions_audio(audio_speed)
    send_marker(outlet, "task04_meditation_audio_end")
    _mark("audio_end")

    # ----- 2. First gong + silent timer -----
    io.play_gong()
    send_marker(outlet, "task04_meditation_gong_start")
    send_marker(outlet, "task04_meditation_start")
    _mark("start", duration_s=duration_s)

    io.wait(duration_s)

    # ----- 3. Second gong -----
    io.play_gong()
    send_marker(outlet, "task04_meditation_gong_end")
    _mark("gong_end")

    # ----- 4. Completion screen -----
    io.show_text_and_wait(MEDITATION_COMPLETE_TEXT, "space")
    send_marker(outlet, "task04_meditation_end")
    _mark("end")

    return log
