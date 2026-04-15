"""Tests for src/tasks/05_ssvep/task.py.

Task 05 delegates all flicker stimulation to the Vayl desktop app via the
third-party ``vayl_lsl_bridge.VaylBridge``. Our code is a thin orchestrator
that (a) shows instruction/completion screens and (b) emits 7 boundary
markers on the shared ``P013_Task_Markers`` stream. These tests mock the
bridge so no HTTP traffic to localhost:9471 happens and no extra LSL
outlets are created, then verify:

1. A full run with a working bridge emits all 7 P013 markers in order,
   calls ``start_ramp`` with the configured carrier values (which correspond
   to effective SSVEP = 2x carrier), and writes the session log CSV.
2. In production (``demo=False``), a bridge whose ``status()`` fails raises
   a clear ``RuntimeError`` pointing at the Vayl API URL.
3. In demo mode (``demo=True``), a failing ``status()`` triggers the
   sleep-based fallback path and the task still emits all 7 markers.
4. The carrier-to-effective conversion is documented and exercised: with
   carrier ``20 -> 0.5`` Hz, the effective SSVEP is ``40 -> 1`` Hz.
"""

from __future__ import annotations

import csv
import importlib
from pathlib import Path

import pytest
from pylsl import StreamInlet, resolve_byprop

from tasks.common.lsl_markers import create_session_outlet, send_marker

task_mod = importlib.import_module("tasks.05_ssvep.task")


# ----- Mock TaskIO / Bridge --------------------------------------------------


class MockTaskIO:
    """Headless TaskIO: no Pygame, just bookkeeping."""

    def __init__(self) -> None:
        self.shown_texts: list[str] = []
        self.iconify_calls = 0
        self.restore_calls = 0
        self.waits: list[float] = []

    def show_text_and_wait(self, text: str, wait_key: str) -> None:
        self.shown_texts.append(text)

    def iconify(self) -> None:
        self.iconify_calls += 1

    def restore(self) -> None:
        self.restore_calls += 1

    def check_escape(self) -> None:
        return None

    def wait(self, seconds: float) -> None:
        self.waits.append(seconds)


class MockVaylBridge:
    """Minimal stand-in for :class:`vayl_lsl_bridge.VaylBridge`.

    Records every call so tests can assert against them, returns the same
    timing-dict shape that the real bridge returns from its HTTP wrappers.
    """

    def __init__(self, fail_status: bool = False) -> None:
        self.fail_status = fail_status
        self.status_calls = 0
        self.start_ramp_calls: list[tuple] = []
        self.wait_for_ramp_calls: list[float] = []
        self.turn_off_calls = 0

    def status(self) -> dict:
        self.status_calls += 1
        if self.fail_status:
            raise ConnectionError(
                "Mock: Cannot reach Vayl API at http://127.0.0.1:9471"
            )
        return {"version": "1.2.3", "running": True}

    def start_ramp(
        self, start_hz: float, end_hz: float, duration_seconds: float
    ) -> dict:
        self.start_ramp_calls.append((start_hz, end_hz, duration_seconds))
        return {
            "status": "ok",
            "params": {
                "startHz": start_hz,
                "endHz": end_hz,
                "durationSeconds": duration_seconds,
            },
            "timing": {
                "wallTimeMs": 1_711_000_000_000,
                "wallTimeISO": "2026-04-15T12:00:00Z",
                "nativeCallMs": 1.42,
                "rampEndISO": "2026-04-15T12:05:00Z",
            },
        }

    def wait_for_ramp(self, duration_seconds: float, extra_buffer: float = 0.5) -> None:
        self.wait_for_ramp_calls.append(duration_seconds)

    def turn_off(self) -> dict:
        self.turn_off_calls += 1
        return {
            "status": "ok",
            "timing": {
                "wallTimeMs": 1_711_000_300_000,
                "wallTimeISO": "2026-04-15T12:05:00Z",
            },
        }


# ----- Inlet helpers ---------------------------------------------------------


def _drain_inlet(inlet: StreamInlet) -> list[str]:
    markers: list[str] = []
    while True:
        sample, _ = inlet.pull_sample(timeout=0.2)
        if sample is None:
            break
        markers.append(sample[0])
    return markers


@pytest.fixture()
def captured_marker_outlet():
    outlet = create_session_outlet("SSVEP_TEST")
    streams = resolve_byprop("source_id", "P013_SSVEP_TEST", minimum=1, timeout=5.0)
    assert streams, "Could not resolve test marker stream"
    inlet = StreamInlet(streams[0])
    inlet.open_stream(timeout=5.0)

    for _ in range(50):
        send_marker(outlet, "__handshake__")
        sample, _ = inlet.pull_sample(timeout=0.1)
        if sample is not None:
            break
    else:
        pytest.fail("Inlet never connected after 50 handshake attempts")

    while True:
        extra, _ = inlet.pull_sample(timeout=0.05)
        if extra is None:
            break

    yield outlet, inlet
    del inlet
    del outlet


# ----- Tests -----------------------------------------------------------------


EXPECTED_MARKERS = [
    "task05_start",
    "task05_instructions_start",
    "task05_instructions_end",
    "task05_ramp_begin",
    "task05_ramp_end",
    "task05_overlay_off",
    "task05_end",
]


def _small_config() -> dict:
    return {
        "carrier_start_hz": 20.0,
        "carrier_end_hz": 0.5,
        "ramp_duration_s": 1.0,
        "vayl_lsl_stream_name": "VaylStim",
        "vayl_api_url": "http://127.0.0.1:9471",
    }


class TestFullRunWithWorkingBridge:
    def test_all_seven_markers_emitted_in_order(
        self, captured_marker_outlet, tmp_path: Path
    ):
        outlet, inlet = captured_marker_outlet
        mock_io = MockTaskIO()
        mock_bridge = MockVaylBridge(fail_status=False)

        log_path = task_mod.run(
            outlet=outlet,
            config=_small_config(),
            participant_id="PYTEST_T05",
            io=mock_io,
            bridge=mock_bridge,
            output_dir=tmp_path,
        )

        markers = _drain_inlet(inlet)
        for m in EXPECTED_MARKERS:
            assert m in markers, f"Missing marker {m}"
        assert len(EXPECTED_MARKERS) == 7

        # Ordering: markers should appear in the EXPECTED order
        indices = [markers.index(m) for m in EXPECTED_MARKERS]
        assert indices == sorted(indices), (
            f"Marker ordering wrong. Got: "
            f"{[(m, markers.index(m)) for m in EXPECTED_MARKERS]}"
        )
        assert markers[-1] == "task05_end"

        # Bridge interactions
        assert mock_bridge.status_calls == 1
        assert len(mock_bridge.start_ramp_calls) == 1
        assert len(mock_bridge.wait_for_ramp_calls) == 1
        assert mock_bridge.turn_off_calls == 1

        # Pygame window lifecycle
        assert mock_io.iconify_calls == 1
        assert mock_io.restore_calls == 1
        # Two shown screens: instructions + completion
        assert len(mock_io.shown_texts) == 2

        # Session log written
        assert log_path.exists()
        assert log_path.name == "task05_session_log.csv"
        with open(log_path, newline="") as fh:
            rows = list(csv.DictReader(fh))
        assert list(rows[0].keys()) == ["event", "timestamp", "details"]
        events = [r["event"] for r in rows]
        assert "task_start" in events
        assert "vayl_status_ok" in events
        assert "ramp_start" in events
        assert "ramp_end" in events
        assert "overlay_off" in events
        # The ramp_start row should carry the server wallTimeMs
        ramp_row = next(r for r in rows if r["event"] == "ramp_start")
        assert "wall_time_ms=1711000000000" in ramp_row["details"]


class TestCarrierToEffectiveFrequencyConversion:
    def test_carrier_values_passed_to_start_ramp(
        self, captured_marker_outlet, tmp_path: Path
    ):
        """Effective SSVEP = 2 * carrier. Verify carrier values go through."""
        outlet, _inlet = captured_marker_outlet
        mock_io = MockTaskIO()
        mock_bridge = MockVaylBridge(fail_status=False)

        task_mod.run(
            outlet=outlet,
            config=_small_config(),
            participant_id="PYTEST_T05_CONV",
            io=mock_io,
            bridge=mock_bridge,
            output_dir=tmp_path,
        )

        # Exactly one start_ramp call with the configured carriers:
        assert mock_bridge.start_ramp_calls == [(20.0, 0.5, 1.0)]
        # Effective SSVEP is documented as 2x carrier:
        carrier_start, carrier_end, _ = mock_bridge.start_ramp_calls[0]
        assert carrier_start * 2 == 40.0  # effective start SSVEP
        assert carrier_end * 2 == 1.0  # effective end SSVEP


class TestVaylConnectivityFailure:
    def test_production_raises_clear_error(
        self, captured_marker_outlet, tmp_path: Path
    ):
        """Non-demo mode: failing status() must raise RuntimeError."""
        outlet, _inlet = captured_marker_outlet
        mock_io = MockTaskIO()
        failing_bridge = MockVaylBridge(fail_status=True)

        with pytest.raises(RuntimeError, match=r"Vayl desktop app is not reachable"):
            task_mod.run(
                outlet=outlet,
                config=_small_config(),
                participant_id="PYTEST_T05_FAIL",
                io=mock_io,
                bridge=failing_bridge,
                demo=False,
                output_dir=tmp_path,
            )

        # start_ramp should NEVER have been called
        assert failing_bridge.start_ramp_calls == []
        assert failing_bridge.turn_off_calls == 0

    def test_demo_mode_falls_back_to_sleep(
        self, captured_marker_outlet, tmp_path: Path
    ):
        """Demo mode: failing status() triggers sleep-based simulation."""
        outlet, inlet = captured_marker_outlet
        mock_io = MockTaskIO()
        failing_bridge = MockVaylBridge(fail_status=True)

        log_path = task_mod.run(
            outlet=outlet,
            config=_small_config(),
            participant_id="PYTEST_T05_DEMO",
            io=mock_io,
            bridge=failing_bridge,
            demo=True,  # <-- fallback path
            output_dir=tmp_path,
        )

        markers = _drain_inlet(inlet)

        # All 7 markers still emitted even without Vayl
        for m in EXPECTED_MARKERS:
            assert m in markers, f"Missing marker {m}"

        # Bridge was NOT driven past status()
        assert failing_bridge.start_ramp_calls == []
        assert failing_bridge.wait_for_ramp_calls == []
        assert failing_bridge.turn_off_calls == 0

        # Sleep fallback: io.wait was called with the ramp duration
        # (demo mode overrides ramp_duration_s to 10)
        assert 10 in mock_io.waits or 10.0 in mock_io.waits

        # Session log records the demo fallback
        with open(log_path, newline="") as fh:
            rows = list(csv.DictReader(fh))
        events = [r["event"] for r in rows]
        assert "vayl_status_failed_demo_fallback" in events
