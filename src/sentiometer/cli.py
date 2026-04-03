"""
Command-line interface for the Sentiometer LSL streamer.

Usage examples:
    # Guided setup wizard (recommended for RAs)
    sentiometer run

    # Guided setup with port pre-selected
    sentiometer run --port COM4

    # Direct stream (no wizard)
    sentiometer stream

    # Override port and duration
    sentiometer stream --port COM4 --command "00120 2"

    # Attach to already-streaming device (started via CoolTerm)
    sentiometer stream --port COM3 --no-start-cmd

    # List available serial ports
    sentiometer ports
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import click
import yaml
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

console = Console()

# Default config location (relative to repo root)
DEFAULT_CONFIG = Path(__file__).resolve().parent.parent.parent / "config" / "sentiometer.yaml"

LINE_ENDING_MAP = {
    "crlf": "\r\n",
    "cr": "\r",
    "lf": "\n",
    "none": "",
}


def load_config(config_path: Path) -> dict:
    """Load and return YAML config, with validation."""
    if not config_path.exists():
        console.print(f"[red]Config not found:[/red] {config_path}")
        console.print(
            "Copy config/sentiometer.yaml to config/local.yaml"
            " and edit for your machine."
        )
        sys.exit(1)
    with open(config_path) as f:
        cfg = yaml.safe_load(f)
    return cfg


def setup_logging(cfg: dict) -> None:
    """Configure logging with Rich handler for clean terminal output."""
    level = getattr(logging, cfg.get("logging", {}).get("level", "INFO").upper())
    handlers: list[logging.Handler] = [
        RichHandler(
            console=console,
            show_time=True,
            show_path=False,
            markup=True,
            rich_tracebacks=True,
        )
    ]

    log_file = cfg.get("logging", {}).get("log_file")
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_path))

    logging.basicConfig(level=level, handlers=handlers, format="%(message)s")


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------

@click.group()
@click.version_option(package_name="iacs-sentiometer-study")
def main():
    """IACS Sentiometer — Serial to LSL bridge."""
    pass


# ---------------------------------------------------------------------------
# `sentiometer stream` command
# ---------------------------------------------------------------------------

@main.command()
@click.option(
    "--config", "-c",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Path to YAML config file. Defaults to config/sentiometer.yaml.",
)
@click.option("--port", "-p", default=None, help="Serial port override (e.g., COM4).")
@click.option(
    "--command", "-cmd", default=None,
    help='Device start command override (e.g., "00120 2").',
)
@click.option(
    "--no-start-cmd",
    is_flag=True,
    default=False,
    help="Skip sending start command (device already streaming).",
)
@click.option("--debug", is_flag=True, default=False, help="Enable debug logging.")
@click.option(
    "--line-ending",
    type=click.Choice(["crlf", "cr", "lf", "none"], case_sensitive=False),
    default=None,
    help="Line ending for commands: crlf, cr, lf, or none.",
)
def stream(config, port, command, no_start_cmd, debug, line_ending):
    """Start the Sentiometer -> LSL stream."""
    # Resolve config path
    config_path = config or DEFAULT_CONFIG
    # Also check for local override
    local_config = config_path.parent / "local.yaml"
    if config is None and local_config.exists():
        config_path = local_config

    cfg = load_config(config_path)

    if debug:
        cfg.setdefault("logging", {})["level"] = "DEBUG"

    setup_logging(cfg)
    logger = logging.getLogger("sentiometer")

    # Apply CLI overrides
    if port:
        cfg["serial"]["port"] = port
        logger.info("Port overridden to: %s", port)
    if command:
        cfg["device"]["start_command"] = command
        logger.info("Start command overridden to: %r", command)
    if line_ending is not None:
        cfg["serial"]["line_ending"] = LINE_ENDING_MAP[line_ending]
        logger.info("Line ending overridden to: %s (%r)", line_ending, cfg["serial"]["line_ending"])

    # Print config summary
    console.print()
    console.rule("[bold blue]Sentiometer LSL Streamer[/bold blue]")
    console.print(f"  Config:   {config_path}")
    console.print(f"  Port:     {cfg['serial']['port']}")
    console.print(f"  Baudrate: {cfg['serial']['baudrate']}")
    console.print(f"  Command:  {cfg['device']['start_command']}")
    console.print(f"  LSL name: {cfg['lsl']['name']}")
    console.print(f"  Channels: {cfg['lsl']['channel_count']} @ {cfg['lsl']['nominal_srate']} Hz")
    console.print()

    # Import here to avoid serial import errors when just running --help
    from sentiometer.stream import run_stream

    try:
        run_stream(cfg, send_start=not no_start_cmd)
    except Exception as e:
        logger.exception("Fatal error: %s", e)
        sys.exit(1)


# ---------------------------------------------------------------------------
# `sentiometer run` command (guided wizard — recommended for RAs)
# ---------------------------------------------------------------------------

@main.command()
@click.option(
    "--config", "-c",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Path to YAML config file.",
)
@click.option(
    "--port", "-p", default=None,
    help="Serial port override (skips port detection step).",
)
@click.option("--debug", is_flag=True, default=False, help="Enable debug logging.")
@click.option(
    "--line-ending",
    type=click.Choice(["crlf", "cr", "lf", "none"], case_sensitive=False),
    default=None,
    help="Line ending for commands: crlf, cr, lf, or none.",
)
def run(config, port, debug, line_ending):
    """Guided session setup wizard (recommended for data collection)."""
    # Resolve config path
    config_path = config or DEFAULT_CONFIG
    local_config = config_path.parent / "local.yaml"
    if config is None and local_config.exists():
        config_path = local_config

    cfg = load_config(config_path)

    if debug:
        cfg.setdefault("logging", {})["level"] = "DEBUG"
    if line_ending is not None:
        cfg["serial"]["line_ending"] = LINE_ENDING_MAP[line_ending]

    setup_logging(cfg)

    from sentiometer.guided import run_guided

    try:
        run_guided(cfg, port_override=port)
    except Exception as e:
        console.print(f"\n  [bold red]Fatal error:[/bold red] {e}")
        console.print("  Please take a screenshot of this error and notify the PI.")
        logging.getLogger("sentiometer").exception("Fatal error in guided setup")
        sys.exit(1)


# ---------------------------------------------------------------------------
# `sentiometer ports` command
# ---------------------------------------------------------------------------

@main.command()
def ports():
    """List available serial ports (helps find the Sentiometer)."""
    from serial.tools.list_ports import comports

    table = Table(title="Available Serial Ports")
    table.add_column("Port", style="bold cyan")
    table.add_column("Description")
    table.add_column("Hardware ID", style="dim")

    found = list(comports())
    if not found:
        console.print("[yellow]No serial ports detected.[/yellow]")
        return

    for p in sorted(found, key=lambda x: x.device):
        table.add_row(p.device, p.description, p.hwid)

    console.print(table)
    console.print(
        f"\n[dim]Found {len(found)} port(s)."
        " Use --port COMx with the stream command.[/dim]"
    )


# ---------------------------------------------------------------------------
# `sentiometer debug-raw` command
# ---------------------------------------------------------------------------

@main.command("debug-raw")
@click.option("--port", "-p", default=None, help="Serial port (e.g., COM3).")
@click.option(
    "--command", "-cmd", default=None,
    help='Device start command override (e.g., "00005 2").',
)
def debug_raw(port, command):
    """Send a raw command and dump all bytes from the device."""
    import time

    import serial

    from sentiometer.stream import auto_detect_port

    # Resolve port
    if not port:
        port = auto_detect_port()
        if not port:
            console.print("[red]No USB Serial Device found.[/red]")
            sys.exit(1)
        console.print(f"Auto-detected port: [cyan]{port}[/cyan]")

    # Resolve command
    cfg = load_config(DEFAULT_CONFIG)
    local_config = DEFAULT_CONFIG.parent / "local.yaml"
    if local_config.exists():
        cfg = load_config(local_config)
    cmd = command or cfg["device"]["start_command"]

    console.print(f"Port:    [cyan]{port}[/cyan]")
    console.print(f"Command: [cyan]{cmd!r}[/cyan] (no line ending)")
    console.print()

    # Open serial
    conn = serial.Serial(
        port=port,
        baudrate=cfg["serial"]["baudrate"],
        bytesize=cfg["serial"]["bytesize"],
        parity=serial.PARITY_NONE,
        stopbits=cfg["serial"]["stopbits"],
        timeout=1.0,
    )
    conn.dtr = True
    conn.rts = True
    console.print("[green]Serial port opened. DTR=True, RTS=True[/green]")

    # Wait for device init
    console.print("[dim]Waiting 2s for device to initialize...[/dim]")
    time.sleep(2.0)

    # Flush and send raw command (no line ending)
    conn.reset_input_buffer()
    payload = cmd.encode("ascii")
    conn.write(payload)
    console.print(
        f"[green]Sent {len(payload)} bytes:[/green]"
        f" {payload.hex(' ')}  ({payload!r})"
    )
    console.print()

    # Read raw bytes for 15 seconds
    console.rule("[bold]Raw device output (15 seconds)")
    total_bytes = 0
    t_start = time.monotonic()
    duration = 15.0

    while time.monotonic() - t_start < duration:
        chunk = conn.read(1024)
        if not chunk:
            continue
        total_bytes += len(chunk)
        elapsed = time.monotonic() - t_start
        hex_str = chunk.hex(" ")
        ascii_str = chunk.decode("ascii", errors="replace")
        # Replace non-printable chars for display
        display = "".join(
            c if 32 <= ord(c) < 127 else "." for c in ascii_str
        )
        console.print(
            f"[dim]{elapsed:6.2f}s[/dim]"
            f" [cyan]({len(chunk):4d}B)[/cyan]"
            f"  HEX: {hex_str}"
        )
        console.print(
            f"         "
            f"         ASCII: {display}"
        )

    conn.close()
    console.print()
    console.rule("[bold]Done")
    console.print(
        f"Total bytes received: [bold cyan]{total_bytes:,}[/bold cyan]"
        f" in {duration:.0f}s"
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    main()
