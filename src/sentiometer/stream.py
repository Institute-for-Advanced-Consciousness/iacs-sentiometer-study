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

import pylsl
import serial
import serial.tools.list_ports

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
    last_device_ts: float | None = None

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


def auto_detect_port() -> str | None:
    """Return the first serial port whose description contains 'USB Serial Device'.

    Skips built-in ports (e.g. COM1) that are not USB devices.
    Returns None if no match is found.
    """
    for p in sorted(serial.tools.list_ports.comports(), key=lambda x: x.device):
        if "USB Serial Device" in (p.description or ""):
            logger.info("Auto-detected serial port: %s (%s)", p.device, p.description)
            return p.device
    return None


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
    # Device requires DTR and RTS high before it will accept commands
    conn.dtr = True
    conn.rts = True
    logger.info("Serial connected: port=%s, baudrate=%d", scfg["port"], scfg["baudrate"])
    return conn


def send_command(conn: serial.Serial, command: str, line_ending: str = "\r\n") -> None:
    """Send a command string to the device."""
    # Pause before sending to let the device initialize after port open
    logger.debug("Waiting 2s for device to initialize...")
    time.sleep(2.0)
    payload = (command + line_ending).encode("ascii")
    conn.write(payload)
    logger.info("Sent command: %r (%d bytes)", command, len(payload))
    # Pause after sending to let the device process the command
    time.sleep(0.5)


# ---------------------------------------------------------------------------
# Buffered serial reader
# ---------------------------------------------------------------------------

class SerialBuffer:
    """Read raw bytes from a serial port and split on \\r\\n.

    This replaces pyserial's readline() which interacts badly with
    timeout settings, DTR timing, and echo-discard logic.  Reading
    raw bytes and splitting ourselves is deterministic.
    """

    def __init__(self, conn: serial.Serial) -> None:
        self.conn = conn
        self._buf = b""

    def read_lines(self) -> list[bytes]:
        """Return all complete \\r\\n-terminated lines available now.

        Reads whatever bytes are waiting (or blocks for 1 byte if
        nothing is buffered yet), appends to the internal buffer,
        splits on \\r\\n, and returns complete lines.  Any trailing
        fragment is kept for the next call.
        """
        chunk = self.conn.read(self.conn.in_waiting or 1)
        if not chunk:
            return []
        self._buf += chunk
        # Split on \r\n; last element is the incomplete tail
        parts = self._buf.split(b"\r\n")
        self._buf = parts[-1]  # keep incomplete fragment
        # Everything except the last element is a complete line
        return [p for p in parts[:-1] if p]

    def clear(self) -> None:
        """Discard the internal buffer."""
        self._buf = b""


# ---------------------------------------------------------------------------
# Parse a single line
# ---------------------------------------------------------------------------

def parse_line(raw: bytes, expected_n: int = 6) -> list[float] | None:
    """Parse a raw serial line into a list of floats.

    Returns None on any parse failure.
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

    # --- Auto-detect port if not specified ---
    if not cfg["serial"].get("port"):
        detected = auto_detect_port()
        if detected:
            cfg["serial"]["port"] = detected
        else:
            logger.warning(
                "No USB Serial Device found; falling back to config port."
            )

    # --- Connect serial ---
    conn = open_serial(cfg)
    conn.reset_input_buffer()

    # --- Buffered reader ---
    buf = SerialBuffer(conn)

    # --- Send start command ---
    if send_start:
        send_command(
            conn,
            cfg["device"]["start_command"],
            cfg["serial"].get("line_ending", "\r\n"),
        )
        logger.info("Command sent. Waiting for data...")
    else:
        logger.info("Skipping start command (--no-start-cmd). Waiting for data...")

    # --- Create LSL outlet ---
    outlet = create_lsl_outlet(cfg)
    logger.info("LSL outlet is live. Open LabRecorder to begin capturing.")

    # --- Main loop ---
    first_sample = True
    try:
        while True:
            lines = buf.read_lines()
            if not lines:
                continue

            for raw_line in lines:
                values = parse_line(raw_line, expected_n)
                if values is None:
                    stats.parse_errors += 1
                    continue

                # Skip first parsed line (likely a partial fragment)
                if first_sample:
                    logger.debug("Discarding first sample (partial line): %r", raw_line[:80])
                    first_sample = False
                    continue

                # --- Dropped sample detection ---
                device_ts = values[0]
                if stats.last_device_ts is not None:
                    gap = device_ts - stats.last_device_ts
                    if gap > interval_ms * 1.5:
                        n_dropped = int(gap / interval_ms) - 1
                        stats.dropped_samples += n_dropped
                        logger.warning(
                            "Gap detected: %.0fms (expected %dms) "
                            "— ~%d dropped samples at device_ts=%.0f",
                            gap, interval_ms, n_dropped, device_ts,
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
