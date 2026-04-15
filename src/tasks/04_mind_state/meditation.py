"""Task 04 meditation block: instructions -> gong -> silent timer -> gong.

The block has no visual content during the meditation itself (the screen is
black while the participant's eyes are closed). Two gong strikes bracket the
silent period: one at the start (immediately after the participant presses
spacebar to begin), one at the end (after ``duration_s`` has elapsed).

Markers emitted:

* ``task04_meditation_instructions_start`` / ``_end``
* ``task04_meditation_gong_start``
* ``task04_meditation_start``
* ``task04_meditation_gong_end``
* ``task04_meditation_end`` (after the participant presses spacebar to
  acknowledge the completion screen)

Like :mod:`game`, this module never touches Pygame directly -- it delegates
every side effect to a ``TaskIO`` so it can run headlessly in tests.
"""

from __future__ import annotations

from pylsl import StreamOutlet, local_clock

from tasks.common.lsl_markers import send_marker

MEDITATION_INSTRUCTIONS_TEXT = (
    "Close your eyes. Focus your attention on the sensation of your breath "
    "at the nostrils. When you feel settled, begin scanning through your "
    "body from head to toe. If you notice your mind wandering, gently return "
    "your attention to the breath.\n\n"
    "When you are ready, press spacebar. You will hear a gong, and the "
    "meditation will begin."
)

MEDITATION_COMPLETE_TEXT = (
    "The meditation is complete. Please open your eyes.\n\n"
    "Press spacebar to continue."
)


def run_meditation_block(
    outlet: StreamOutlet,
    io,
    duration_s: float,
) -> list[dict]:
    """Run the meditation block end-to-end.

    Emits the six meditation markers in order and returns a list of phase
    events timestamped with ``local_clock()`` for the behavioral log.
    """
    log: list[dict] = []

    # ----- Instructions -----
    send_marker(outlet, "task04_meditation_instructions_start")
    log.append(
        {
            "timestamp": local_clock(),
            "phase": "meditation",
            "type": "instructions_start",
        }
    )
    io.show_text_and_wait(MEDITATION_INSTRUCTIONS_TEXT, "space")
    send_marker(outlet, "task04_meditation_instructions_end")
    log.append(
        {
            "timestamp": local_clock(),
            "phase": "meditation",
            "type": "instructions_end",
        }
    )

    # ----- Start gong + silent timer -----
    io.show_black_screen()
    io.play_gong()
    send_marker(outlet, "task04_meditation_gong_start")
    send_marker(outlet, "task04_meditation_start")
    log.append(
        {
            "timestamp": local_clock(),
            "phase": "meditation",
            "type": "start",
            "duration_s": duration_s,
        }
    )

    io.wait(duration_s)

    # ----- End gong + completion screen -----
    io.play_gong()
    send_marker(outlet, "task04_meditation_gong_end")
    log.append(
        {
            "timestamp": local_clock(),
            "phase": "meditation",
            "type": "gong_end",
        }
    )

    io.show_text_and_wait(MEDITATION_COMPLETE_TEXT, "space")
    send_marker(outlet, "task04_meditation_end")
    log.append(
        {
            "timestamp": local_clock(),
            "phase": "meditation",
            "type": "end",
        }
    )

    return log
