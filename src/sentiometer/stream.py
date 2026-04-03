"""
Sentiometer Serial → LSL Bridge
================================
Reads CSV-formatted samples from the Sentiometer over USB serial and pushes
them as an LSL stream for synchronization with EEG, ECG, and other modalities
via LabRecorder.

Packet format (per line):
    device_ts,PD1,PD2,PD3,PD4,PD5

All values are integers. device_ts increments by sample_interval_ms per sample.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import serial
import pylsl

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class StreamStats:
    """Running statistics for monitoring stream health."""
    samples_pushed: int = 0
    parse_errors: int = 0
    dropped_samples: int = 0
    start_time: float = field(default_factory=time.monotonic)
    last_device_ts: Optional[float] = None

    @property
    def elapsed_sec(self) -> float:
        return time.monotonic() - self.start_time

    @property
    def effective_rate(self) -> float:
        elapsed = self.elapsed_sec
        return self.samples_pushed / elapsed if elapsed > 0 else 0.0

    def summary(self) -> str:
        return (
            f"Samples: {self.samples_pushed:,} | "
            f"Rate: {self.effective_rate:.1f} Hz | "
            f"Dropped: {self.dropped_samples:,} | "
            f"Parse errors: {self.parse_errors:,} | "
            f"Elapsed: {self.elapsed_sec:.0f}s"
        )


# ---------------------------------------------------------------------------
# LSL outlet factory
# ---------------------------------------------------------------------------

def create_lsl_outlet(cfg: dict) -> pylsl.StreamOutlet:
    """Create and return an LSL StreamOutlet from config dict."""
    lsl_cfg = cfg["lsl"]

    info = pylsl.StreamInfo(
        name=lsl_cfg["name"],
        type=lsl_cfg["type"],
        channel_count=lsl_cfg["channel_count"],
        nominal_srate=lsl_cfg["nominal_srate"],
        channel_format=pylsl.cf_float32,
        source_id=lsl_cfg["source_id"],
    )

    # Embed channel metadata (LabRecorder / XDF readers use this)
    channels_xml = info.desc().append_child("channels")
    labels = lsl_cfg["channel_labels"]
    units = lsl_cfg.get("channel_units", ["arbitrary"] * lsl_cfg["channel_count"])

    for label, unit in zip(labels, units):
        ch = channels_xml.append_child("channel")
        ch.append_child_value("label", label)
        ch.append_child_value("unit", unit)
        ch.append_child_value("type", lsl_cfg["type"])

    # Embed acquisition metadata
    acq = info.desc().append_child("acquisition")
    acq.append_child_value("manufacturer", "Senzient Inc.")
    acq.append_child_value("model", "Sentiometer V1")
    acq.append_child_value("serial_baudrate", str(cfg["serial"]["baudrate"]))

    # chunk_size=0 means push every sample immediately (lowest latency)
    outlet = pylsl.StreamOutlet(info, chunk_size=0)
    logger.info(
        "LSL outlet created: name=%s, type=%s, channels=%d, srate=%d, id=%s",
        lsl_cfg["name"], lsl_cfg["type"], lsl_cfg["channel_count"],
        lsl_cfg["nominal_srate"], lsl_cfg["source_id"],
    )
    return outlet


# ---------------------------------------------------------------------------
# Serial connection
# ---------------------------------------------------------------------------

PARITY_MAP = {
    "none": serial.PARITY_NONE,
    "even": serial.PARITY_EVEN,
    "odd": serial.PARITY_ODD,
}


def open_serial(cfg: dict) -> serial.Serial:
    """Open and return a serial.Serial connection from config dict."""
    scfg = cfg["serial"]
    conn = serial.Serial(
        port=scfg["port"],
        baudrate=scfg["baudrate"],
        bytesize=scfg["bytesize"],
        parity=PARITY_MAP.get(scfg["parity"], serial.PARITY_NONE),
        stopbits=scfg["stopbits"],
        timeout=scfg["timeout_sec"],
    )
    logger.info("Serial connected: port=%s, baudrate=%d", scfg["port"], scfg["baudrate"])
    return conn


def send_command(conn: serial.Serial, command: str, line_ending: str = "\r\n") -> None:
    """Send a command string to the device."""
    payload = (command + line_ending).encode("ascii")
    conn.write(payload)
    logger.info("Sent command: %r (%d bytes)", command, len(payload))
    # Brief pause to let the device process before it starts streaming
    time.sleep(0.2)


# ---------------------------------------------------------------------------
# Parse a single line
# ---------------------------------------------------------------------------

def parse_line(raw: bytes, expected_n: int = 6) -> Optional[list[float]]:
    """
    Parse a raw serial line into a list of floats.
    Returns None on any parse failure (malformed, wrong column count, etc.).
    """
    try:
        text = raw.decode("ascii", errors="replace").strip()
        if not text:
            return None
        parts = text.split(",")
        if len(parts) != expected_n:
            return None
        return [float(p) for p in parts]
    except (ValueError, UnicodeDecodeError):
        return None


# ---------------------------------------------------------------------------
# Main streaming loop
# ---------------------------------------------------------------------------

def run_stream(cfg: dict, send_start: bool = True) -> None:
    """
    Main blocking loop: connect serial, create LSL outlet, push samples.

    Parameters
    ----------
    cfg : dict
        Full config dictionary (serial + device + lsl + logging sections).
    send_start : bool
        If True, send the device start command before entering the read loop.
        Set False if the device is already streaming (e.g., started via CoolTerm).
    """
    stats = StreamStats()
    expected_n = cfg["device"]["values_per_line"]
    interval_ms = cfg["device"]["sample_interval_ms"]
    status_every = cfg["logging"]["status_every_n_samples"]

    # --- Connect serial ---
    conn = open_serial(cfg)

    # --- Flush any stale bytes ---
    conn.reset_input_buffer()

    # --- Send start command ---
    if send_start:
        send_command(
            conn,
            cfg["device"]["start_command"],
            cfg["serial"].get("line_ending", "\r\n"),
        )
        logger.info("Waiting for first sample...")
    else:
        logger.info("Skipping start command (--no-start-cmd). Waiting for data...")

    # --- Create LSL outlet ---
    outlet = create_lsl_outlet(cfg)
    logger.info("LSL outlet is live. Open LabRecorder to begin capturing.")

    # --- Main loop ---
    try:
        while True:
            raw_line = conn.readline()
            if not raw_line:
                # Timeout — no data received within timeout_sec
                continue

            values = parse_line(raw_line, expected_n)
            if values is None:
                stats.parse_errors += 1
                if stats.parse_errors <= 10:
                    logger.warning("Parse error #%d: %r", stats.parse_errors, raw_line[:80])
                continue

            # --- Dropped sample detection ---
            device_ts = values[0]
            if stats.last_device_ts is not None:
                gap = device_ts - stats.last_device_ts
                expected_gap = interval_ms
                if gap > expected_gap * 1.5:
                    n_dropped = int(gap / expected_gap) - 1
                    stats.dropped_samples += n_dropped
                    logger.warning(
                        "Gap detected: %.0fms (expected %dms) — ~%d dropped samples at device_ts=%.0f",
                        gap, expected_gap, n_dropped, device_ts,
                    )
            stats.last_device_ts = device_ts

            # --- Push to LSL ---
            outlet.push_sample(values)
            stats.samples_pushed += 1

            # --- Periodic status ---
            if stats.samples_pushed % status_every == 0:
                logger.info(stats.summary())

    except KeyboardInterrupt:
        logger.info("Stopped by user (Ctrl+C).")
    finally:
        conn.close()
        logger.info("Serial port closed.")
        logger.info("Final stats: %s", stats.summary())
