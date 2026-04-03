"""
Mock serial port that emulates Sentiometer output for testing.

Generates synthetic data matching the real device format:
    device_ts,PD1,PD2,PD3,PD4,PD5

Usage:
    from tests.mock_serial import MockSentiometer
    mock = MockSentiometer(sample_interval_ms=2)
    line = mock.readline()  # b'890206,758,735,4070,1063,572\\r\\n'
"""

from __future__ import annotations

import random
import time
from typing import Optional


class MockSentiometer:
    """
    Drop-in replacement for serial.Serial that generates synthetic
    Sentiometer data. Implements the subset of the serial.Serial API
    used by sentiometer.stream.
    """

    def __init__(
        self,
        sample_interval_ms: int = 2,
        start_ts: int = 890000,
        drop_probability: float = 0.0,
        noise_amplitude: int = 20,
    ):
        self.sample_interval_ms = sample_interval_ms
        self.device_ts = start_ts
        self.drop_probability = drop_probability
        self.noise_amplitude = noise_amplitude
        self.is_open = True
        self._started = False
        self._buffer = b""

        # Baseline values (approximate real device ranges from Nicco's sample)
        self._baselines = [760, 735, 4065, 1055, 565]

    @property
    def in_waiting(self) -> int:
        return len(self._buffer)

    def reset_input_buffer(self) -> None:
        self._buffer = b""

    def write(self, data: bytes) -> int:
        """Accept and ignore commands (mimics device accepting start command)."""
        self._started = True
        return len(data)

    def readline(self) -> bytes:
        """Generate one line of synthetic data at the appropriate rate."""
        if not self._started:
            time.sleep(0.1)
            return b""

        # Simulate real-time pacing
        time.sleep(self.sample_interval_ms / 1000.0)

        # Simulate occasional dropped samples
        if random.random() < self.drop_probability:
            self.device_ts += self.sample_interval_ms  # skip one
        
        self.device_ts += self.sample_interval_ms

        values = [self.device_ts]
        for baseline in self._baselines:
            noise = random.randint(-self.noise_amplitude, self.noise_amplitude)
            values.append(max(0, min(4095, baseline + noise)))

        line = ",".join(str(v) for v in values) + "\r\n"
        return line.encode("ascii")

    def close(self) -> None:
        self.is_open = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
