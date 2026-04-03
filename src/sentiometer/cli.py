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


def load_config(config_path: Path) -> dict:
    """Load and return YAML config, with validation."""
    if not config_path.exists():
        console.print(f"[red]Config not found:[/red] {config_path}")
        console.print("Copy config/sentiometer.yaml to config/local.yaml and edit for your machine.")
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
@click.option("--command", "-cmd", default=None, help='Device start command override (e.g., "00120 2").')
@click.option(
    "--no-start-cmd",
    is_flag=True,
    default=False,
    help="Skip sending start command (device already streaming).",
)
@click.option("--debug", is_flag=True, default=False, help="Enable debug logging.")
def stream(config, port, command, no_start_cmd, debug):
    """Start the Sentiometer → LSL stream."""
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
@click.option("--port", "-p", default=None, help="Serial port override (skips port detection step).")
@click.option("--debug", is_flag=True, default=False, help="Enable debug logging.")
def run(config, port, debug):
    """Guided session setup wizard (recommended for data collection)."""
    # Resolve config path
    config_path = config or DEFAULT_CONFIG
    local_config = config_path.parent / "local.yaml"
    if config is None and local_config.exists():
        config_path = local_config

    cfg = load_config(config_path)

    if debug:
        cfg.setdefault("logging", {})["level"] = "DEBUG"

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
    console.print(f"\n[dim]Found {len(found)} port(s). Use --port COMx with the stream command.[/dim]")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    main()
