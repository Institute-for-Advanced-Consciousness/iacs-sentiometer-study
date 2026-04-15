"""Tests for tasks.common.lsl_markers.

Verifies that the session marker outlet can be created, that markers are
receivable by a test inlet, and that sequential "tasks" can share a single
outlet without dropping or reconnecting.
"""

from __future__ import annotations

import pytest
from pylsl import StreamInlet, resolve_byprop

from tasks.common.lsl_markers import (
    STREAM_NAME,
    STREAM_TYPE,
    create_demo_outlet,
    create_session_outlet,
    send_marker,
)


@pytest.fixture()
def session_outlet():
    """Create an outlet for tests and tear it down afterwards."""
    outlet = create_session_outlet("TEST")
    yield outlet
    del outlet


class TestCreateSessionOutlet:
    """Tests for create_session_outlet."""

    def test_outlet_is_discoverable(self, session_outlet):
        """The outlet should be discoverable via its source_id."""
        streams = resolve_byprop("source_id", "P013_TEST", minimum=1, timeout=5.0)
        assert len(streams) >= 1

    def test_stream_metadata(self, session_outlet):
        """Stream info fields must match the spec in CLAUDE.md."""
        streams = resolve_byprop("source_id", "P013_TEST", minimum=1, timeout=5.0)
        info = streams[0]
        assert info.name() == STREAM_NAME
        assert info.type() == STREAM_TYPE
        assert info.channel_count() == 1
        assert info.nominal_srate() == 0

    def test_source_id_includes_participant(self):
        """source_id must be P013_{participant_id}."""
        outlet = create_session_outlet("P042")
        streams = resolve_byprop("source_id", "P013_P042", minimum=1, timeout=5.0)
        assert len(streams) >= 1
        del outlet


class TestCreateDemoOutlet:
    """Tests for create_demo_outlet."""

    def test_demo_uses_demo_id(self):
        """Demo outlet should have source_id P013_DEMO."""
        outlet = create_demo_outlet()
        streams = resolve_byprop("source_id", "P013_DEMO", minimum=1, timeout=5.0)
        assert len(streams) >= 1
        del outlet


class TestSendMarker:
    """Tests for send_marker.

    These tests use a dedicated outlet+inlet pair with a unique source_id
    to avoid cross-test interference from LSL stream discovery.
    """

    @pytest.fixture()
    def outlet_and_inlet(self):
        """Create a paired outlet and inlet, ensuring the inlet is connected.

        LSL on Windows can take a moment to fully establish the data channel
        even after open_stream returns.  We send handshake markers in a loop
        until one arrives, confirming the link is live.
        """
        outlet = create_session_outlet("RECV")
        streams = resolve_byprop("source_id", "P013_RECV", minimum=1, timeout=5.0)
        assert streams, "Could not resolve test stream"
        inlet = StreamInlet(streams[0])
        inlet.open_stream(timeout=5.0)

        # Retry handshake until the data channel is established
        for _ in range(50):
            send_marker(outlet, "__handshake__")
            sample, _ = inlet.pull_sample(timeout=0.1)
            if sample is not None:
                break
        else:
            pytest.fail("Inlet never connected after 50 handshake attempts")

        # Drain any extra handshake markers that queued up
        while True:
            extra, _ = inlet.pull_sample(timeout=0.05)
            if extra is None:
                break

        yield outlet, inlet
        del inlet
        del outlet

    def test_single_marker_received(self, outlet_and_inlet):
        """A single marker should be receivable by an inlet."""
        outlet, inlet = outlet_and_inlet

        send_marker(outlet, "task01_tone_standard")
        sample, ts = inlet.pull_sample(timeout=5.0)
        assert sample is not None, "No sample received"
        assert sample[0] == "task01_tone_standard"
        assert ts > 0

    def test_returns_timestamp(self, session_outlet):
        """send_marker should return a positive local_clock timestamp."""
        ts = send_marker(session_outlet, "test_marker")
        assert isinstance(ts, float)
        assert ts > 0

    def test_sequential_tasks_share_outlet(self, outlet_and_inlet):
        """Multiple 'tasks' can send through the same outlet without issues."""
        outlet, inlet = outlet_and_inlet

        # Simulate markers from three sequential tasks
        markers_to_send = [
            "task01_start",
            "task01_tone_standard",
            "task01_end",
            "task02_start",
            "task02_color_red",
            "task02_end",
            "task03_start",
            "task03_face_onset",
            "task03_end",
        ]

        for m in markers_to_send:
            send_marker(outlet, m)

        received = []
        for _ in markers_to_send:
            sample, _ = inlet.pull_sample(timeout=5.0)
            assert sample is not None, f"Missing marker (got {len(received)} so far)"
            received.append(sample[0])

        assert received == markers_to_send

    def test_timestamps_are_monotonic(self, session_outlet):
        """Timestamps returned by successive send_marker calls must increase."""
        t1 = send_marker(session_outlet, "a")
        t2 = send_marker(session_outlet, "b")
        t3 = send_marker(session_outlet, "c")
        assert t1 < t2 < t3
