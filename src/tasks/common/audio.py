"""Audio playback utilities shared across tasks.

Thin wrapper around :mod:`psychopy.sound` that tasks (and eventually the
session launcher's pre-session sound check) use to load and play calibrated
oddball stimuli.

PsychoPy is imported lazily inside each function so the module itself can be
imported on development machines without the full ``tasks`` extra installed.
Callers that actually invoke :func:`load_tone` or :func:`play_test_tone` must
be running in an environment where PsychoPy is available (the stimulus iMac
or any dev machine with ``uv sync --extra tasks``).

Standalone sound check (for experimenter use on the stimulus machine)::

    uv run python -m tasks.common.audio --test

This plays the standard tone, waits one second, plays the deviant tone, and
prints a confirmation so the RA can adjust system volume before a session.
"""

from __future__ import annotations

import time
from pathlib import Path

import click
from rich.console import Console

# Force PsychoPy's sound backend to pygame before any Sound() call. PsychoPy
# 2025 defaults to the PTB backend, which requires the `psychtoolbox` package
# we don't ship. pygame is already a hard dep of the `tasks` extra.
#
# PsychoPy 2025's `Sound` class hardcodes `backend = "ptb"` as a class
# attribute and does NOT consult `prefs.hardware['audioLib']` at Sound()
# construction time, so we have to override it directly on the class. We also
# set the pref so any code that DOES read it sees the same answer.
from psychopy import prefs
from psychopy.sound import Sound

prefs.hardware["audioLib"] = ["pygame"]
Sound.backend = "pygame"

REPO_ROOT = Path(__file__).resolve().parents[3]
ASSETS_SOUNDS_DIR = REPO_ROOT / "assets" / "sounds"

STANDARD_TONE_PATH = ASSETS_SOUNDS_DIR / "tone_1000hz.wav"
DEVIANT_TONE_PATH = ASSETS_SOUNDS_DIR / "tone_2000hz.wav"

console = Console()


def load_tone(filepath: str | Path) -> Sound:
    """Load a .wav file and return a ready-to-play PsychoPy ``Sound``.

    Parameters
    ----------
    filepath:
        Path to a .wav file (e.g. ``assets/sounds/tone_1000hz.wav``). May be
        a string or :class:`pathlib.Path`.

    Returns
    -------
    psychopy.sound.Sound
        A PsychoPy sound object. Call ``.play()`` to trigger playback. The
        stereo/mono layout and sample rate are inferred from the file.

    Raises
    ------
    FileNotFoundError
        If *filepath* does not exist on disk.
    """
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"Audio file not found: {path.resolve()}")

    return Sound(str(path))


def play_test_tone(filepath: str | Path, volume: float = 1.0) -> None:
    """Play a single tone once at the specified volume and block until it ends.

    Used by the pre-session sound check so an RA can verify headphone levels
    before starting the oddball task.

    Parameters
    ----------
    filepath:
        Path to a .wav file to play.
    volume:
        Playback volume in the range ``[0.0, 1.0]``. Defaults to ``1.0`` (full
        amplitude as stored in the .wav file). Values outside the range are
        clipped.
    """
    volume = max(0.0, min(1.0, float(volume)))
    tone = load_tone(filepath)
    tone.setVolume(volume)
    tone.play()
    # Block for the tone's nominal duration so the caller's next action
    # (e.g. a second tone in --test mode) doesn't overlap with playback.
    duration_s = tone.getDuration() if hasattr(tone, "getDuration") else 0.05
    time.sleep(duration_s + 0.02)


@click.command()
@click.option(
    "--test",
    "run_test",
    is_flag=True,
    default=False,
    help="Play the standard then deviant oddball tones as a sound check.",
)
def main(run_test: bool) -> None:
    """CLI entry point for ``python -m tasks.common.audio``."""
    if not run_test:
        console.print(
            "[yellow]No action specified.[/yellow] "
            "Pass [bold]--test[/bold] to run the pre-session sound check."
        )
        return

    console.print("[bold]Pre-session sound check[/bold]")
    console.print(f"Playing standard tone: {STANDARD_TONE_PATH.name}")
    play_test_tone(STANDARD_TONE_PATH)
    time.sleep(1.0)
    console.print(f"Playing deviant tone:  {DEVIANT_TONE_PATH.name}")
    play_test_tone(DEVIANT_TONE_PATH)
    console.print(
        "[green]Sound check complete -- adjust system volume if needed.[/green]"
    )


if __name__ == "__main__":
    main()
