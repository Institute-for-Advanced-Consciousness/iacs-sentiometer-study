"""LSL marker stream creation and event sending.

Provides the single persistent ``P013_Task_Markers`` outlet used by every task
in the session.  The launcher calls :func:`create_session_outlet` once at
startup, passes the returned :class:`~pylsl.StreamOutlet` to each task's
``run()`` function, and closes it when the session ends.

For standalone ``--demo`` testing a task can call :func:`create_demo_outlet`
to get a temporary outlet without needing the full launcher.
"""

from __future__ import annotations

import logging

from pylsl import StreamInfo, StreamOutlet, local_clock

log = logging.getLogger(__name__)

# LSL stream constants (must match CLAUDE.md § LSL Conventions)
STREAM_NAME = "P013_Task_Markers"
STREAM_TYPE = "Markers"
CHANNEL_FORMAT = "string"
NOMINAL_RATE = 0
SOURCE_ID_PREFIX = "P013_"


def create_session_outlet(participant_id: str) -> StreamOutlet:
    """Create the single session-wide LSL marker outlet.

    Parameters
    ----------
    participant_id:
        Participant identifier (e.g. ``"P001"``).  Used to build the
        ``source_id`` field (``P013_P001``).

    Returns
    -------
    StreamOutlet
        Ready-to-use outlet.  The caller is responsible for keeping it alive
        for the duration of the session and deleting / letting it be GC'd when
        the session ends.
    """
    source_id = f"{SOURCE_ID_PREFIX}{participant_id}"
    info = StreamInfo(
        name=STREAM_NAME,
        type=STREAM_TYPE,
        channel_count=1,
        nominal_srate=NOMINAL_RATE,
        channel_format=CHANNEL_FORMAT,
        source_id=source_id,
    )
    outlet = StreamOutlet(info)
    log.info("Created LSL outlet %s (source_id=%s)", STREAM_NAME, source_id)
    return outlet


def create_demo_outlet() -> StreamOutlet:
    """Create a temporary outlet for standalone ``--demo`` testing.

    Equivalent to ``create_session_outlet("DEMO")``.
    """
    return create_session_outlet("DEMO")


def send_marker(outlet: StreamOutlet, marker: str) -> float:
    """Push a single string marker with a ``local_clock()`` timestamp.

    Parameters
    ----------
    outlet:
        The session-wide (or demo) marker outlet.
    marker:
        Marker string to send (e.g. ``"task01_tone_standard"``).

    Returns
    -------
    float
        The LSL ``local_clock()`` timestamp recorded at the moment the marker
        was sent.  Useful for behavioural logging.
    """
    timestamp = local_clock()
    outlet.push_sample([marker], timestamp)
    log.debug("Marker sent: %s @ %.6f", marker, timestamp)
    return timestamp
