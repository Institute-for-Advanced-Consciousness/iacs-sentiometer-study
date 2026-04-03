"""
Guided Session Setup Wizard
============================
Interactive step-by-step walkthrough for research assistants.
Each step validates a prerequisite before advancing, with clear
instructions and troubleshooting guidance on failure.

Usage:
    sentiometer run                   # full guided flow
    sentiometer run --port COM4       # skip port detection
    sentiometer run --quick           # skip wizard, go straight to streaming
"""

from __future__ import annotations

import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import serial
import serial.tools.list_ports
import pylsl
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

logger = logging.getLogger(__name__)
console = Console()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TOTAL_STEPS = 8


def step_header(step: int, title: str) -> None:
    """Print a formatted step header."""
    console.print()
    console.rule(
        f"[bold cyan]Step {step} of {TOTAL_STEPS}:[/bold cyan] [bold]{title}[/bold]",
        style="cyan",
    )
    console.print()


def success(msg: str) -> None:
    console.print(f"  [bold green]✓[/bold green] {msg}")


def warn(msg: str) -> None:
    console.print(f"  [bold yellow]⚠[/bold yellow] {msg}")


def fail(msg: str) -> None:
    console.print(f"  [bold red]✗[/bold red] {msg}")


def info(msg: str) -> None:
    console.print(f"  [dim]ℹ[/dim] {msg}")


def waiting(msg: str) -> None:
    console.print(f"  [bold blue]⏳[/bold blue] {msg}")


def prompt_enter(msg: str = "Press Enter to continue...") -> None:
    """Wait for the RA to press Enter."""
    console.print()
    console.input(f"  [dim]→ {msg}[/dim] ")


def prompt_yes_no(msg: str, default: bool = True) -> bool:
    """Ask a yes/no question and return the answer."""
    hint = "Y/n" if default else "y/N"
    response = console.input(f"  [dim]→ {msg} [{hint}]:[/dim] ").strip().lower()
    if not response:
        return default
    return response in ("y", "yes")


def prompt_text(msg: str, default: str = "") -> str:
    """Ask for text input with an optional default."""
    if default:
        response = console.input(f"  [dim]→ {msg} [{default}]:[/dim] ").strip()
        return response if response else default
    else:
        while True:
            response = console.input(f"  [dim]→ {msg}:[/dim] ").strip()
            if response:
                return response
            fail("This field is required.")


# ---------------------------------------------------------------------------
# Session metadata
# ---------------------------------------------------------------------------

@dataclass
class SessionInfo:
    participant_id: str = ""
    duration_min: int = 5
    port: str = ""
    command: str = ""


# ---------------------------------------------------------------------------
# Wizard steps
# ---------------------------------------------------------------------------

def step_1_session_info(cfg: dict, session: SessionInfo) -> None:
    """Collect participant ID and recording duration."""
    step_header(1, "Session Information")

    session.participant_id = prompt_text("Enter participant ID (e.g., SENT001)")

    duration_default = cfg["device"]["start_command"].split()[0].lstrip("0") or "5"
    duration_str = prompt_text(
        "Enter recording duration in minutes",
        default=duration_default,
    )
    try:
        session.duration_min = int(duration_str)
    except ValueError:
        warn(f"Could not parse '{duration_str}' as a number. Using {duration_default} minutes.")
        session.duration_min = int(duration_default)

    # Build the start command from duration + sample interval
    sample_ms = cfg["device"]["sample_interval_ms"]
    session.command = f"{session.duration_min:05d} {sample_ms}"

    success(f"Participant: {session.participant_id}")
    success(f"Duration: {session.duration_min} minutes")
    success(f"Device command: \"{session.command}\"")


def step_2_usb_detection(cfg: dict, session: SessionInfo) -> None:
    """Scan for serial ports and identify the Sentiometer."""
    step_header(2, "USB Connection")

    waiting("Scanning for serial ports...")
    ports = list(serial.tools.list_ports.comports())

    if not ports:
        fail("No serial ports found!")
        console.print()
        console.print("  [yellow]Troubleshooting:[/yellow]")
        console.print("    1. Check that the Sentiometer USB cable is plugged in")
        console.print("    2. Open Device Manager → Ports (COM & LPT)")
        console.print("    3. If no port appears, try a different USB cable or port")
        console.print("    4. You may need to install the USB-to-serial driver")
        console.print()
        prompt_enter("Plug in the device, then press Enter to re-scan...")
        # Retry once
        ports = list(serial.tools.list_ports.comports())
        if not ports:
            fail("Still no ports found. Cannot continue without a serial connection.")
            sys.exit(1)

    # Display found ports
    table = Table(box=box.SIMPLE)
    table.add_column("Port", style="bold cyan")
    table.add_column("Description")
    for p in sorted(ports, key=lambda x: x.device):
        table.add_row(p.device, p.description)
    console.print(table)

    if session.port:
        # Port was pre-specified via CLI
        success(f"Using pre-specified port: {session.port}")
    elif len(ports) == 1:
        session.port = ports[0].device
        success(f"Found one port: {session.port} — {ports[0].description}")
        if not prompt_yes_no(f"Is {session.port} the Sentiometer?"):
            session.port = prompt_text("Enter the correct port (e.g., COM4)")
    else:
        info(f"Found {len(ports)} ports.")
        session.port = prompt_text("Which port is the Sentiometer? (e.g., COM3)")


def step_3_close_coolterm(cfg: dict, session: SessionInfo) -> None:
    """Remind RA to close CoolTerm and other serial monitors."""
    step_header(3, "Close Other Serial Applications")

    console.print("  [bold yellow]IMPORTANT:[/bold yellow] Only one program can use a serial port at a time.")
    console.print()
    console.print("  Please make sure the following are [bold]CLOSED[/bold]:")
    console.print("    • CoolTerm")
    console.print("    • Arduino IDE Serial Monitor")
    console.print("    • PuTTY")
    console.print("    • Any other serial terminal")
    console.print()
    info("If you were using CoolTerm to verify the signal, close it now.")
    info("This script will handle all communication with the device.")

    prompt_enter("Press Enter when all serial applications are closed...")


def step_4_serial_test(cfg: dict, session: SessionInfo) -> serial.Serial:
    """Attempt to open the serial port."""
    step_header(4, "Serial Connection Test")

    from sentiometer.stream import open_serial, PARITY_MAP

    # Temporarily override port in config
    cfg["serial"]["port"] = session.port

    waiting(f"Attempting to open {session.port}...")

    max_retries = 3
    conn = None
    for attempt in range(1, max_retries + 1):
        try:
            conn = open_serial(cfg)
            break
        except serial.SerialException as e:
            error_msg = str(e)
            if "PermissionError" in error_msg or "Access is denied" in error_msg:
                fail(f"Port {session.port} is in use by another application!")
                console.print()
                console.print("  [yellow]This usually means CoolTerm is still open.[/yellow]")
                console.print("  Close it completely (check the system tray too).")
                if attempt < max_retries:
                    prompt_enter(f"Press Enter to retry ({attempt}/{max_retries})...")
            elif "FileNotFoundError" in error_msg or "could not open port" in error_msg:
                fail(f"Port {session.port} does not exist!")
                console.print("  Check Device Manager for the correct port name.")
                session.port = prompt_text("Enter the correct port")
                cfg["serial"]["port"] = session.port
                if attempt < max_retries:
                    continue
            else:
                fail(f"Unexpected serial error: {e}")
                if attempt < max_retries:
                    prompt_enter(f"Press Enter to retry ({attempt}/{max_retries})...")

    if conn is None:
        fail("Could not open serial port after multiple attempts. Exiting.")
        sys.exit(1)

    success(f"Serial port {session.port} opened successfully.")
    success(f"Settings: {cfg['serial']['baudrate']} baud, 8N1")

    conn.reset_input_buffer()
    return conn


def step_5_device_communication(
    cfg: dict, session: SessionInfo, conn: serial.Serial
) -> None:
    """Send start command and verify data is flowing."""
    step_header(5, "Device Communication")

    from sentiometer.stream import send_command, parse_line

    expected_n = cfg["device"]["values_per_line"]

    waiting(f'Sending start command: "{session.command}"')
    line_ending = cfg["serial"].get("line_ending", "\r\n")
    send_command(conn, session.command, line_ending)

    waiting("Waiting for data (up to 10 seconds)...")

    # Try to read a valid sample within a timeout
    deadline = time.monotonic() + 10.0
    first_sample = None
    lines_read = 0
    parse_errors = 0

    while time.monotonic() < deadline:
        raw = conn.readline()
        if not raw:
            continue
        lines_read += 1
        values = parse_line(raw, expected_n)
        if values is not None:
            first_sample = values
            break
        else:
            parse_errors += 1
            # First few lines are often partial — that's normal
            if parse_errors <= 3:
                info(f"Skipped partial line ({parse_errors}): {raw[:60]!r}")

    if first_sample is None:
        fail("No valid data received within 10 seconds!")
        console.print()
        console.print("  [yellow]Troubleshooting:[/yellow]")
        console.print("    1. Is the Sentiometer powered on?")
        console.print("    2. Check USB cable connection")
        console.print(f"    3. Is the baud rate correct? (currently {cfg['serial']['baudrate']})")
        console.print(f"    4. Try the command manually in CoolTerm: \"{session.command}\"")
        console.print()
        if not prompt_yes_no("Try again?"):
            conn.close()
            sys.exit(1)
        # Retry
        send_command(conn, session.command, line_ending)
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            raw = conn.readline()
            if not raw:
                continue
            values = parse_line(raw, expected_n)
            if values is not None:
                first_sample = values
                break
        if first_sample is None:
            fail("Still no data. Check hardware and try again. Exiting.")
            conn.close()
            sys.exit(1)

    # Display first sample
    labels = cfg["lsl"]["channel_labels"]
    success("Receiving data! First valid sample:")
    for label, val in zip(labels, first_sample):
        console.print(f"    {label:>12s}: {val:>8.0f}")

    # Read a burst to estimate actual sample rate
    waiting("Measuring sample rate (2 seconds)...")
    sample_count = 0
    t_start = time.monotonic()
    while time.monotonic() - t_start < 2.0:
        raw = conn.readline()
        if raw and parse_line(raw, expected_n) is not None:
            sample_count += 1

    elapsed = time.monotonic() - t_start
    measured_rate = sample_count / elapsed if elapsed > 0 else 0
    expected_rate = cfg["lsl"]["nominal_srate"]

    if abs(measured_rate - expected_rate) < expected_rate * 0.1:
        success(f"Sample rate: ~{measured_rate:.0f} Hz (expected: {expected_rate} Hz)")
    else:
        warn(
            f"Sample rate: ~{measured_rate:.0f} Hz (expected: {expected_rate} Hz) — "
            f"deviation > 10%"
        )
        info("This may indicate a baud rate mismatch or USB latency.")
        info("Proceeding anyway — check the data quality post-recording.")


def step_6_lsl_outlet(cfg: dict) -> pylsl.StreamOutlet:
    """Create the LSL outlet."""
    step_header(6, "LSL Stream")

    from sentiometer.stream import create_lsl_outlet

    waiting(
        f'Creating LSL outlet: "{cfg["lsl"]["name"]}" '
        f'({cfg["lsl"]["channel_count"]} channels @ {cfg["lsl"]["nominal_srate"]} Hz)'
    )
    outlet = create_lsl_outlet(cfg)
    success("LSL outlet is live and discoverable on the network.")
    return outlet


def step_7_labrecorder(cfg: dict) -> None:
    """Prompt RA to verify LabRecorder sees the stream."""
    step_header(7, "LabRecorder Verification")

    console.print("  [bold]Now open LabRecorder on this computer.[/bold]")
    console.print()
    console.print('  1. In LabRecorder, click [bold]"Update"[/bold] to refresh the stream list.')
    console.print(f'  2. You should see [bold cyan]"{cfg["lsl"]["name"]}"[/bold cyan] appear.')
    console.print("  3. Check the box next to it so it will be recorded.")
    console.print()
    console.print("  [dim]If you don't see it:[/dim]")
    console.print("    • Click Update again (streams can take a moment to appear)")
    console.print("    • Make sure LabRecorder and this script are on the same machine")
    console.print("    • Check that no firewall is blocking LSL (UDP ports 16571+)")

    while True:
        console.print()
        confirmed = prompt_yes_no(
            f'Do you see "{cfg["lsl"]["name"]}" in LabRecorder?'
        )
        if confirmed:
            success("LabRecorder is ready.")
            break
        else:
            warn("Stream not visible yet.")
            console.print("  Try these steps:")
            console.print('    1. Click "Update" in LabRecorder')
            console.print("    2. If still missing, close and reopen LabRecorder")
            console.print("    3. Make sure this script is still running")
            if not prompt_yes_no("Try again?"):
                warn("Proceeding without LabRecorder confirmation.")
                break


def step_8_ready(cfg: dict, session: SessionInfo) -> None:
    """Final summary and launch confirmation."""
    step_header(8, "Ready to Record")

    success("All checks passed!")
    console.print()

    summary = Table(box=box.ROUNDED, show_header=False, padding=(0, 2))
    summary.add_column("Field", style="bold")
    summary.add_column("Value", style="cyan")
    summary.add_row("Participant", session.participant_id)
    summary.add_row("Port", session.port)
    summary.add_row("Duration", f"{session.duration_min} minutes")
    summary.add_row("Channels", f"{cfg['lsl']['channel_count']} @ {cfg['lsl']['nominal_srate']} Hz")
    summary.add_row("LSL Stream", f"{cfg['lsl']['name']} (live)")
    summary.add_row("Device Command", f'"{session.command}"')
    console.print(Panel(summary, title="[bold green]Session Summary", border_style="green"))

    console.print()
    console.print("  [bold yellow]IMPORTANT REMINDERS:[/bold yellow]")
    console.print("    • Do [bold]NOT[/bold] close this window during recording")
    console.print("    • Status updates will print every ~10 seconds")
    console.print("    • Press [bold]Ctrl+C[/bold] to stop when the session is complete")
    console.print("    • Check the status log for any dropped sample warnings")
    console.print()

    prompt_enter("Press Enter to begin streaming...")


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

def run_guided(cfg: dict, port_override: Optional[str] = None) -> None:
    """
    Run the full guided wizard, then hand off to the streaming loop.

    Parameters
    ----------
    cfg : dict
        Full config dictionary.
    port_override : str, optional
        If set, skip port auto-detection and use this port.
    """
    # --- Banner ---
    console.print()
    console.print(
        Panel(
            "[bold white]IACS Sentiometer — Session Setup[/bold white]\n"
            "[dim]Protocol P013 — Optical Consciousness Detection Validation[/dim]",
            border_style="blue",
            box=box.DOUBLE,
            padding=(1, 4),
        )
    )

    session = SessionInfo()
    if port_override:
        session.port = port_override

    # --- Steps 1–3: Info gathering ---
    step_1_session_info(cfg, session)
    step_2_usb_detection(cfg, session)
    step_3_close_coolterm(cfg, session)

    # --- Step 4: Serial test ---
    conn = step_4_serial_test(cfg, session)

    # --- Step 5: Device communication ---
    step_5_device_communication(cfg, session, conn)

    # --- Step 6: LSL outlet ---
    outlet = step_6_lsl_outlet(cfg)

    # --- Step 7: LabRecorder check ---
    step_7_labrecorder(cfg)

    # --- Step 8: Final confirmation ---
    step_8_ready(cfg, session)

    # --- Hand off to streaming loop ---
    console.rule("[bold green]Streaming[/bold green]", style="green")
    console.print()

    from sentiometer.stream import StreamStats, parse_line

    expected_n = cfg["device"]["values_per_line"]
    interval_ms = cfg["device"]["sample_interval_ms"]
    status_every = cfg["logging"]["status_every_n_samples"]
    stats = StreamStats()

    try:
        while True:
            raw = conn.readline()
            if not raw:
                continue

            values = parse_line(raw, expected_n)
            if values is None:
                stats.parse_errors += 1
                continue

            # Dropped sample detection
            device_ts = values[0]
            if stats.last_device_ts is not None:
                gap = device_ts - stats.last_device_ts
                if gap > interval_ms * 1.5:
                    n_dropped = int(gap / interval_ms) - 1
                    stats.dropped_samples += n_dropped
                    logger.warning(
                        "Gap: %.0fms (~%d dropped) at device_ts=%.0f",
                        gap, n_dropped, device_ts,
                    )
            stats.last_device_ts = device_ts

            outlet.push_sample(values)
            stats.samples_pushed += 1

            if stats.samples_pushed % status_every == 0:
                console.print(f"  [dim]{stats.summary()}[/dim]")

    except KeyboardInterrupt:
        console.print()
        console.rule("[bold yellow]Session Ended[/bold yellow]", style="yellow")
        console.print()
        console.print(f"  [bold]Final stats:[/bold] {stats.summary()}")
        console.print()
        console.print(f"  Participant: {session.participant_id}")
        console.print(f"  Total samples: {stats.samples_pushed:,}")
        console.print(f"  Duration: {stats.elapsed_sec:.0f} seconds")
        if stats.dropped_samples > 0:
            drop_pct = (stats.dropped_samples / max(stats.samples_pushed, 1)) * 100
            warn(f"Dropped samples: {stats.dropped_samples:,} ({drop_pct:.2f}%)")
        else:
            success("No dropped samples detected.")
        console.print()
        console.print("  [dim]Remember to stop LabRecorder and save the XDF file.[/dim]")
        console.print()
    finally:
        conn.close()
        logger.info("Serial port closed.")
