# CLAUDE.md

This is the IACS Sentiometer validation study repo (Protocol P013). It houses the Sentiometer LSL streaming bridge and all experimental task scripts.

## Key architecture

- `src/sentiometer/` — Serial → LSL bridge for the Senzient Sentiometer optical consciousness detection device
- `src/tasks/` — Experimental paradigm scripts (oddball, rgb_illuminance, backward_masking, mind_state_switching, ssvep)
- All modality streams use Lab Streaming Layer (LSL); data is recorded with LabRecorder into XDF format
- Synchronization across modalities (EEG, ECG, Sentiometer, task events) relies on LSL timestamps

## Device protocol

- USB serial: 9600 baud, 8N1, no flow control
- Start command: `"00005 2"` (5 min recording, 2ms sample interval = 500 Hz)
- Serial output format: `device_ts,PD1,PD2,PD3,PD4,PD5` (CSV, one line per sample)
- 12-bit ADC values (0–4095) for photodiode channels
- Device timestamp is in milliseconds, monotonically increasing

## Environment

- Python 3.11, managed with `uv`
- Dependencies in `pyproject.toml`
- `uv sync` to install; `uv run sentiometer stream` to launch
- Tests use `tests/mock_serial.py` to simulate the device without hardware
- Windows-only deployment (both dev and lab PCs)

## Conventions

- Config files: YAML in `config/`. Never commit `local.yaml`.
- New task scripts should emit LSL marker streams for event synchronization
- All task paradigms should be self-contained within their `src/tasks/<name>/` directory
- Use `click` for CLIs, `rich` for terminal output, `pylsl` for all LSL operations
