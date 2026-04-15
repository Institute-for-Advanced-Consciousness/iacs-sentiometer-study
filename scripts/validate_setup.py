"""Pre-session hardware and software validation check.

Run this on the stimulus iMac **before the first participant** to verify that
every dependency and asset the task suite needs is in place. The script does
not run any task or touch any device that could affect a session — it only
reads versions, imports packages, resolves display info, and attempts a few
no-op operations (create + destroy an LSL outlet, open + close an audio
mixer). On completion it prints a Rich table of every check with OK / WARN /
FAIL statuses, and a one-line summary at the bottom.

Usage:

    uv run python scripts/validate_setup.py

Exit code is 0 if all hard-required checks are OK (warnings are allowed), or
1 if any hard-required check failed. Hard-required checks are: Python
version, core packages (pylsl, click, rich, pyyaml, numpy, scipy), LSL
outlet round-trip, tone files, KDEF face directory, Mondrian mask
directory. Soft/warn-only checks: PsychoPy, Pygame, Vayl app, display
resolution (informational only).
"""

from __future__ import annotations

import importlib
import importlib.metadata
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.table import Table

REPO_ROOT = Path(__file__).resolve().parent.parent

# Stimulus file locations checked below
TONE_DIR = REPO_ROOT / "assets" / "sounds"
REQUIRED_TONES = ["tone_1000hz.wav", "tone_2000hz.wav", "Simple_Gong.wav"]
FACES_DIR = REPO_ROOT / "src" / "tasks" / "03_backward_masking" / "stimuli" / "faces"
MASKS_DIR = REPO_ROOT / "src" / "tasks" / "03_backward_masking" / "stimuli" / "masks"
MIN_NEUTRAL_FACES = 10
MIN_MASKS = 50

# Minimum Python version
MIN_PY = (3, 11)

console = Console()


@dataclass
class CheckResult:
    name: str
    status: str  # "OK" | "WARN" | "FAIL"
    details: str
    required: bool  # if True, failure blocks the overall validation

    @property
    def passed(self) -> bool:
        return self.status == "OK"


def _status_cell(status: str) -> str:
    return {
        "OK": "[green]OK[/green]",
        "WARN": "[yellow]WARN[/yellow]",
        "FAIL": "[red]FAIL[/red]",
    }.get(status, status)


# ----- Individual checks -----------------------------------------------------


def check_python_version() -> CheckResult:
    version = sys.version_info
    version_str = f"{version.major}.{version.minor}.{version.micro}"
    if (version.major, version.minor) >= MIN_PY:
        return CheckResult(
            "Python version",
            "OK",
            f"{version_str} (need >= {MIN_PY[0]}.{MIN_PY[1]})",
            required=True,
        )
    return CheckResult(
        "Python version",
        "FAIL",
        f"{version_str} (need >= {MIN_PY[0]}.{MIN_PY[1]})",
        required=True,
    )


def check_package(
    pkg_name: str,
    required: bool = True,
) -> CheckResult:
    """Try to import *pkg_name* and report its version."""
    try:
        mod = importlib.import_module(pkg_name)
        try:
            version = importlib.metadata.version(pkg_name)
        except importlib.metadata.PackageNotFoundError:
            version = getattr(mod, "__version__", "?")
        return CheckResult(
            f"Package: {pkg_name}", "OK", f"v{version}", required=required
        )
    except ImportError as exc:
        return CheckResult(
            f"Package: {pkg_name}",
            "FAIL" if required else "WARN",
            f"not installed ({exc})",
            required=required,
        )


def check_display_resolution() -> CheckResult:
    """Try to detect the current display resolution (informational)."""
    try:
        import tkinter  # noqa: PLC0415

        root = tkinter.Tk()
        w, h = root.winfo_screenwidth(), root.winfo_screenheight()
        root.destroy()
        return CheckResult(
            "Display resolution", "OK", f"{w} x {h} px", required=False
        )
    except Exception as exc:
        return CheckResult(
            "Display resolution",
            "WARN",
            f"could not detect ({exc})",
            required=False,
        )


def check_audio_device() -> CheckResult:
    """Try to open + close the Pygame mixer as a proxy for audio availability."""
    try:
        import pygame  # noqa: PLC0415

        pygame.mixer.init()
        # Pygame populates these after init
        freq, _format, channels = pygame.mixer.get_init() or (0, 0, 0)
        pygame.mixer.quit()
        return CheckResult(
            "Audio output (pygame.mixer)",
            "OK",
            f"{freq} Hz, {channels}ch",
            required=False,
        )
    except Exception as exc:
        return CheckResult(
            "Audio output (pygame.mixer)",
            "WARN",
            f"could not initialize ({exc})",
            required=False,
        )


def check_lsl_roundtrip() -> CheckResult:
    """Create and immediately destroy a test LSL outlet."""
    try:
        from pylsl import StreamInfo, StreamOutlet  # noqa: PLC0415

        info = StreamInfo(
            name="P013_ValidateSetup",
            type="Markers",
            channel_count=1,
            nominal_srate=0,
            channel_format="string",
            source_id="P013_validate_setup",
        )
        outlet = StreamOutlet(info)
        del outlet
        return CheckResult(
            "LSL outlet round-trip",
            "OK",
            "create + destroy succeeded",
            required=True,
        )
    except Exception as exc:
        return CheckResult(
            "LSL outlet round-trip",
            "FAIL",
            f"failed ({exc})",
            required=True,
        )


def check_tone_files() -> CheckResult:
    missing = [t for t in REQUIRED_TONES if not (TONE_DIR / t).exists()]
    if missing:
        return CheckResult(
            f"Tone files ({TONE_DIR.name}/)",
            "FAIL",
            f"missing: {', '.join(missing)}. Run scripts/generate_tones.py.",
            required=True,
        )
    return CheckResult(
        f"Tone files ({TONE_DIR.name}/)",
        "OK",
        f"all {len(REQUIRED_TONES)} present",
        required=True,
    )


def check_kdef_faces() -> CheckResult:
    if not FACES_DIR.exists():
        return CheckResult(
            "KDEF neutral faces",
            "FAIL",
            f"directory missing: {FACES_DIR}",
            required=True,
        )
    neutrals = sorted(p for p in FACES_DIR.glob("*.png") if "NE" in p.name)
    if len(neutrals) < MIN_NEUTRAL_FACES:
        return CheckResult(
            "KDEF neutral faces",
            "FAIL",
            (
                f"found {len(neutrals)} neutrals (need >= {MIN_NEUTRAL_FACES}). "
                "See stimuli/README.md."
            ),
            required=True,
        )
    return CheckResult(
        "KDEF neutral faces",
        "OK",
        f"{len(neutrals)} neutral faces found",
        required=True,
    )


def check_mondrian_masks() -> CheckResult:
    if not MASKS_DIR.exists():
        return CheckResult(
            "Mondrian masks",
            "FAIL",
            f"directory missing: {MASKS_DIR}. Run scripts/generate_mondrians.py.",
            required=True,
        )
    masks = sorted(MASKS_DIR.glob("*.png"))
    if len(masks) < MIN_MASKS:
        return CheckResult(
            "Mondrian masks",
            "FAIL",
            (
                f"found {len(masks)} masks (need >= {MIN_MASKS}). "
                "Run scripts/generate_mondrians.py."
            ),
            required=True,
        )
    return CheckResult(
        "Mondrian masks",
        "OK",
        f"{len(masks)} masks found",
        required=True,
    )


def check_vayl_reachable() -> CheckResult:
    try:
        urllib.request.urlopen("http://127.0.0.1:9471/api/status", timeout=2)
        return CheckResult(
            "Vayl app (localhost:9471)",
            "OK",
            "API responding",
            required=False,
        )
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return CheckResult(
            "Vayl app (localhost:9471)",
            "WARN",
            (
                f"not reachable ({exc}); needed for Task 05 only "
                "(Install from lab shared drive)"
            ),
            required=False,
        )


# ----- Main -----------------------------------------------------------------


def run_all_checks() -> list[CheckResult]:
    results: list[CheckResult] = []

    results.append(check_python_version())

    # Core (required)
    for pkg in ("pylsl", "click", "rich", "yaml", "numpy", "scipy"):
        results.append(check_package(pkg, required=True))

    # Task-layer frameworks (warn-only; PsychoPy only runs on the iMac,
    # Pygame is task 04 only)
    for pkg in ("psychopy", "pygame"):
        results.append(check_package(pkg, required=False))

    results.append(check_display_resolution())
    results.append(check_audio_device())
    results.append(check_lsl_roundtrip())
    results.append(check_tone_files())
    results.append(check_kdef_faces())
    results.append(check_mondrian_masks())
    results.append(check_vayl_reachable())

    return results


def render_results(results: list[CheckResult]) -> None:
    table = Table(
        title="IACS P013 Setup Validation",
        show_header=True,
        header_style="bold magenta",
        expand=False,
    )
    table.add_column("Check", style="white")
    table.add_column("Status", justify="center")
    table.add_column("Details", style="dim")
    for r in results:
        table.add_row(r.name, _status_cell(r.status), r.details)
    console.print(table)


def main() -> int:
    console.print("[bold cyan]Running setup validation...[/bold cyan]\n")
    results = run_all_checks()
    render_results(results)

    hard_failures = [r for r in results if r.required and not r.passed]
    warnings = [r for r in results if not r.required and r.status == "WARN"]

    console.print()
    if hard_failures:
        console.print(
            f"[bold red]Setup validation FAILED.[/bold red] "
            f"{len(hard_failures)} required check(s) need fixing:"
        )
        for r in hard_failures:
            console.print(f"  [red]-[/red] {r.name}: {r.details}")
        return 1

    if warnings:
        console.print(
            f"[bold yellow]Setup validated with {len(warnings)} warning(s).[/bold yellow] "
            "Ready for data collection, but review the warnings above."
        )
    else:
        console.print(
            "[bold green]Setup validated. Ready for data collection.[/bold green]"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
