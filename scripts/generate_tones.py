"""Generate calibrated oddball stimuli (1000 Hz and 2000 Hz tones).

Produces two .wav files in ``assets/sounds/`` used by Task 01 (Auditory
Oddball / P300):

* ``tone_1000hz.wav`` — 1000 Hz standard tone
* ``tone_2000hz.wav`` — 2000 Hz deviant tone

Both tones are 100 ms total duration with a 10 ms raised-cosine rise/fall
envelope, mono, 44.1 kHz sample rate, 16-bit PCM. These parameters match
the ERP CORE standardized auditory oddball protocol (Kappenman et al.,
2021, NeuroImage). Peak amplitude is set to 80% of int16 full-scale to
leave headroom for the playback chain.

Run with:

    uv run python scripts/generate_tones.py

The script prints the duration, sample count, and peak amplitude of each
generated file as a sanity check.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from scipy.io import wavfile

SAMPLE_RATE_HZ = 44_100
TONE_DURATION_S = 0.100  # 100 ms total (ERP CORE)
RAMP_DURATION_S = 0.010  # 10 ms rise / 10 ms fall (ERP CORE)
PEAK_FRACTION = 0.8  # 80% of int16 full-scale (headroom for playback)

STANDARD_FREQ_HZ = 1_000
DEVIANT_FREQ_HZ = 2_000

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = REPO_ROOT / "assets" / "sounds"


def generate_tone(frequency_hz: int) -> np.ndarray:
    """Generate a single oddball tone as an int16 numpy array.

    Parameters
    ----------
    frequency_hz:
        Carrier frequency in Hz (e.g. 1000 or 2000).

    Returns
    -------
    np.ndarray
        Mono int16 waveform of shape ``(n_samples,)`` ready for
        :func:`scipy.io.wavfile.write`.
    """
    n_samples = int(round(TONE_DURATION_S * SAMPLE_RATE_HZ))
    ramp_samples = int(round(RAMP_DURATION_S * SAMPLE_RATE_HZ))

    # Continuous sine wave at the target frequency.
    t = np.arange(n_samples) / SAMPLE_RATE_HZ
    wave = np.sin(2.0 * np.pi * frequency_hz * t)

    # Raised-cosine envelope: 0 → 1 over ramp_samples, flat 1, 1 → 0 over ramp_samples.
    envelope = np.ones(n_samples, dtype=np.float64)
    ramp_idx = np.arange(ramp_samples)
    rise = 0.5 * (1.0 - np.cos(np.pi * ramp_idx / (ramp_samples - 1)))
    envelope[:ramp_samples] = rise
    envelope[-ramp_samples:] = rise[::-1]
    wave *= envelope

    # Scale to int16 with headroom.
    peak_int16 = int(PEAK_FRACTION * np.iinfo(np.int16).max)
    wave_int16 = np.round(wave * peak_int16).astype(np.int16)
    return wave_int16


def write_tone(frequency_hz: int, output_path: Path) -> None:
    """Generate *frequency_hz* tone and write it to *output_path* as 16-bit PCM."""
    wave = generate_tone(frequency_hz)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    wavfile.write(str(output_path), SAMPLE_RATE_HZ, wave)

    duration_ms = 1000.0 * len(wave) / SAMPLE_RATE_HZ
    peak = int(np.max(np.abs(wave)))
    print(
        f"  {output_path.name}: {duration_ms:.2f} ms, "
        f"{len(wave)} samples, peak amplitude = {peak} "
        f"({100.0 * peak / np.iinfo(np.int16).max:.1f}% of int16 max)"
    )


def main() -> None:
    """Entry point: generate both oddball tones and report the results."""
    print(f"Generating oddball tones -> {OUTPUT_DIR}")
    write_tone(STANDARD_FREQ_HZ, OUTPUT_DIR / "tone_1000hz.wav")
    write_tone(DEVIANT_FREQ_HZ, OUTPUT_DIR / "tone_2000hz.wav")
    print("Done.")


if __name__ == "__main__":
    main()
