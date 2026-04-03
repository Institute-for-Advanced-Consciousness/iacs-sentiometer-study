# IACS Sentiometer Study

Serial → LSL bridge for the Senzient Sentiometer, plus experimental task scripts for the IACS consciousness detection validation study (Protocol P013).

## What this does

Reads the Sentiometer's USB serial output (6 CSV channels at 500 Hz) and republishes it as a [Lab Streaming Layer](https://labstreaminglayer.readthedocs.io/) stream. This lets LabRecorder capture Sentiometer data synchronized with EEG, ECG, and other LSL streams into a single XDF file.

## Quick start

### 1. Install uv (one-time, per machine)

```powershell
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Close and reopen your terminal after installing.

### 2. Clone and sync

```powershell
git clone https://github.com/Institute-for-Advanced-Consciousness/iacs-sentiometer-study.git
cd iacs-sentiometer-study
uv sync
```

`uv sync` creates a `.venv/`, installs Python 3.11 (if needed), and locks all dependencies. The resulting environment is identical across machines.

### 3. Configure

```powershell
# Copy default config and edit for your machine
copy config\sentiometer.yaml config\local.yaml
notepad config\local.yaml
```

At minimum, set the correct `serial.port` (e.g., `COM3`). Find it with:

```powershell
uv run sentiometer ports
```

### 4. Stream

```powershell
# RECOMMENDED: Guided setup wizard (walks RA through each step)
uv run sentiometer run

# Guided setup with port pre-selected
uv run sentiometer run --port COM4

# Direct stream (no wizard — for experienced users)
uv run sentiometer stream

# Override recording duration (e.g., 120 min for nap paradigm)
uv run sentiometer stream --command "00120 2"

# Attach to device already streaming (started via CoolTerm)
uv run sentiometer stream --no-start-cmd

# Debug mode (verbose logging)
uv run sentiometer stream --debug

# Test line ending variants (if device isn't responding)
uv run sentiometer run --line-ending none
uv run sentiometer run --line-ending cr

# Raw byte dump (for debugging serial communication)
uv run sentiometer debug-raw
uv run sentiometer debug-raw --port COM3 --command "00005 2"
```

The `run` command walks through 8 steps: entering participant info, detecting the device, testing the connection, verifying data flow, creating the LSL stream, and confirming LabRecorder sees it — all before handing off to the live streaming loop. Use this for data collection sessions.

The `stream` command is the no-frills version that connects and starts immediately. Use this for debugging or if you've already verified the setup.

## Architecture

```
serial USB (9600 baud, 8N1)
    │
    ▼
┌─────────────────┐
│  pyserial        │  raw read → split on \r\n → parse CSV
│  SerialBuffer    │
└────────┬────────┘
         │ [float32 x 6]
         ▼
┌─────────────────┐
│  pylsl           │  push_sample() at ~500 Hz
│  StreamOutlet    │
└────────┬────────┘
         │ LSL (network)
         ▼
┌─────────────────┐
│  LabRecorder     │  → .xdf file (synchronized with EEG, ECG, etc.)
└─────────────────┘
```

### LSL stream metadata

| Field | Value |
|-------|-------|
| Stream name | `Sentiometer` |
| Stream type | `Misc` |
| Channels | 6 (`device_ts`, `PD1`–`PD5`) |
| Sample rate | 500 Hz (nominal) |
| Format | float32 |
| Source ID | `sentiometer_iacs_001` |

### Channels

| Index | Label | Description |
|-------|-------|-------------|
| 0 | `device_ts` | Device clock (ms, monotonic) — use for drift correction |
| 1–5 | `PD1`–`PD5` | Photodiode / mirror channels (12-bit ADC, 0–4095) |

## Repo structure

```
iacs-sentiometer-study/
├── config/
│   ├── sentiometer.yaml      # Default config (committed)
│   └── local.yaml             # Your machine's config (gitignored)
├── src/
│   ├── sentiometer/           # Serial → LSL bridge
│   │   ├── stream.py          # Core streaming logic + SerialBuffer
│   │   ├── guided.py          # 8-step guided wizard for RAs
│   │   └── cli.py             # CLI entry point (stream, run, ports, debug-raw)
│   └── tasks/                 # Experimental paradigms
│       ├── oddball/           # Auditory oddball / P300
│       ├── rgb_illuminance/   # RGB illuminance test
│       ├── backward_masking/  # Backward masking / face detection
│       ├── mind_state_switching/  # Geometry Dash → meditation
│       └── ssvep/             # SSVEP ramp-down
├── tests/
│   ├── test_stream.py         # Unit tests
│   └── mock_serial.py         # Synthetic data generator
├── scripts/
│   └── start_stream.py        # Quick launcher
├── pyproject.toml             # Dependencies and build config
├── .python-version            # Python 3.11
└── .github/workflows/ci.yml   # Lint + test on push
```

## Deploying to the lab computer

The entire point of `uv sync` + `uv.lock` is that you get a byte-identical environment on any machine:

```powershell
# On the lab PC
git clone https://github.com/Institute-for-Advanced-Consciousness/iacs-sentiometer-study.git
cd iacs-sentiometer-study
uv sync

# Create local config
copy config\sentiometer.yaml config\local.yaml
# Edit local.yaml with the correct COM port for this machine

# Test
uv run sentiometer ports
uv run sentiometer debug-raw          # verify raw bytes from device
uv run sentiometer run                # full guided wizard
```

## Development with Claude Code

### Initial workspace setup

```powershell
# 1. Install Claude Code (requires Node.js 18+)
npm install -g @anthropic-ai/claude-code

# 2. Navigate to repo
cd iacs-sentiometer-study

# 3. Launch Claude Code
claude

# 4. Useful first prompts:
#    "Read the README and pyproject.toml to understand the project structure"
#    "Run the tests and fix any failures"
#    "Add a mock-based integration test for the full serial→LSL pipeline"
```

### Claude Code workflow tips

- **Before making changes**, ask Claude Code to read the relevant source files first
- **For new task scripts**, point it at `src/tasks/<paradigm>/` and the existing stream.py as a pattern
- **For testing**, the `tests/mock_serial.py` provides a hardware-free serial emulator
- **Commit frequently** — Claude Code can run `git add` and `git commit` for you

### CLAUDE.md (optional)

If you want Claude Code to auto-load project context, create a `CLAUDE.md` in the repo root:

```markdown
# CLAUDE.md

This is the IACS Sentiometer validation study repo. Key context:

- `src/sentiometer/` is the serial→LSL bridge for the Senzient Sentiometer
- `src/tasks/` contains experimental paradigm scripts
- All LSL streams use Lab Streaming Layer; data is recorded with LabRecorder to XDF
- Device protocol: 9600 baud 8N1, sends "00005 2" to start (duration_min sample_ms)
- Serial format: device_ts,PD1,PD2,PD3,PD4,PD5 (CSV, 500 Hz)
- Python 3.11, managed with uv, dependencies in pyproject.toml
- Tests use mock_serial.py to avoid needing hardware
- Windows-only deployment (both dev and lab machines)
```

## Running tests

```powershell
# All tests
uv run pytest tests/ -v

# With coverage
uv run pytest tests/ --cov=sentiometer --cov-report=term-missing

# Lint
uv run ruff check src/ tests/

# Type check
uv run mypy src/sentiometer/
```

## Troubleshooting

**"No serial ports detected"** — Check that the Sentiometer USB cable is connected and shows up in Device Manager under "Ports (COM & LPT)".

**"Permission denied on COMx"** — Close CoolTerm or any other application that has the port open. Only one process can hold a serial port at a time.

**"Delayed for NNNNNmsecs"** — The device is still in a recording session from a previous command. The wizard waits up to 60 seconds for this to clear automatically. If you're impatient, unplug the device, wait 30 seconds, and replug.

**"Parse errors" in the log** — The first few lines after device start may be partial. The streamer skips these and counts them. If parse errors persist beyond the first second, check your baud rate setting.

**LabRecorder doesn't see the stream** — Verify `uv run sentiometer stream` is running and shows "LSL outlet is live." Both the streamer and LabRecorder must be on the same network subnet (or the same machine).

**Dropped samples** — The streamer monitors the device timestamp for gaps and logs warnings. Occasional drops (< 0.1%) are normal with USB serial. Persistent drops may indicate a USB bandwidth issue — try a different port or hub.
