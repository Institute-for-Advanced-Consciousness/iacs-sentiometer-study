# IACS Sentiometer Study — Repository Guide

## What This Repo Is

This repository (`iacs-sentiometer-study`) contains **all software** for IACS Protocol P013: *"Validation of an Optical Consciousness Detection Instrument via Concurrent EEG/Polysomnography in Healthy Adults."*

It serves two purposes under one roof:

1. **Device layer** (`src/sentiometer/`): Python code to stream the Sentiometer's 6-channel optical signal over USB-serial into Lab Streaming Layer (LSL). This code is **deployed on the Sentiometer's dedicated computer**, not on the task/stimulus machine. It lives in this repo for version control but runs independently.

2. **Task layer** (`src/tasks/`): Five experimental paradigms plus a session launcher that guides the participant and experimenter through the full protocol in order. The task suite runs on the stimulus computer (24" iMac) and emits LSL event markers through a single persistent marker stream. LabRecorder on the same network picks up this marker stream alongside the EEG, CGX AIM-2, and Sentiometer streams from their respective machines — all synced via LSL into one XDF per session.

---

## The Science (Why This Matters)

The Sentiometer is a novel optical instrument (650 nm laser + 5-photodiode array, 500 Hz) developed by Prof. Santosh Helekar at Houston Methodist. It claims to detect consciousness-related signals from the forehead.

We are **not** advocates — we are **evaluators**. The study is designed to test whether the Sentiometer signal:
- Correlates with established neural markers of consciousness (P300, sleep stages, perceptual thresholds)
- Contains no information it shouldn't (visual qualia content — the RGB test is a *null hypothesis* test)
- Remains stable when it should (SSVEP ramp — participant stays conscious, signal should be flat)
- Differentiates cognitive states (gameplay vs. meditation)

**Key principle**: Each paradigm has a clear, falsifiable prediction. If the Sentiometer fails any of these, that is a valid scientific result.

---

## Repository Structure

```
iacs-sentiometer-study/
├── CLAUDE.md                    # ← You are here
├── pyproject.toml               # uv/pip project config, all dependencies
├── config/
│   ├── sentiometer.yaml         # Default device config (committed)
│   └── local.yaml               # Machine-specific overrides (gitignored)
├── src/
│   ├── sentiometer/             # DEVICE LAYER
│   │   ├── __init__.py
│   │   ├── cli.py               # Click CLI entry point (`sentiometer` command)
│   │   ├── stream.py            # Serial → LSL bridge (core streaming loop)
│   │   ├── guided.py            # Guided setup wizard
│   │   └── config.py            # YAML config loader
│   └── tasks/                   # TASK LAYER
│       ├── __init__.py
│       ├── launcher.py          # Session launcher / task sequencer
│       ├── common/              # Shared utilities across all tasks
│       │   ├── __init__.py
│       │   ├── lsl_markers.py   # LSL marker stream creation & event sending
│       │   ├── display.py       # PsychoPy window management & shared display utils
│       │   ├── instructions.py  # Instruction screen rendering
│       │   └── config.py        # Task configuration loader
│       ├── 01_oddball/          # Auditory Oddball / P300
│       │   ├── __init__.py
│       │   ├── task.py          # Main task script
│       │   └── config.yaml      # Paradigm-specific parameters
│       ├── 02_rgb_illuminance/  # RGB Illuminance / Visual Qualia Decoding
│       │   ├── __init__.py
│       │   ├── task.py
│       │   └── config.yaml
│       ├── 03_backward_masking/ # Backward Masking / Face Detection
│       │   ├── __init__.py
│       │   ├── task.py
│       │   ├── config.yaml
│       │   └── stimuli/         # KDEF face images + Mondrian masks (gitignored, see README)
│       ├── 04_mind_state/       # Mind-State Switching (Gameplay + Meditation)
│       │   ├── __init__.py
│       │   ├── task.py          # Orchestrator for both blocks
│       │   ├── game.py          # Custom Geometry Dash clone with LSL markers
│       │   ├── meditation.py    # Meditation timer with LSL markers
│       │   └── config.yaml
│       └── 05_ssvep/            # SSVEP Frequency Ramp-Down
│           ├── __init__.py
│           ├── task.py
│           └── config.yaml
├── assets/                      # Shared stimulus assets
│   ├── sounds/                  # Oddball tones (1000Hz, 2000Hz .wav files)
│   ├── images/                  # Fixation crosses, instruction screens
│   └── fonts/                   # If needed for task displays
├── scripts/                     # One-off utilities
│   ├── generate_tones.py        # Create calibrated oddball stimuli
│   ├── generate_mondrians.py    # Create Mondrian mask images
│   └── validate_setup.py        # Pre-session hardware check
├── tests/                       # pytest tests
│   ├── test_sentiometer.py
│   ├── test_markers.py
│   └── test_task_configs.py
├── data/                        # gitignored — local data storage during collection
├── .gitignore
└── README.md
```

---

## Session Flow (What Participants Experience)

The session is deliberately ordered as a **progressive consciousness ramp** — from maximal alertness toward sleep. This is not arbitrary; it prevents arousal carryover from corrupting sleep recordings.

```
Arrival & Consent
    ↓
EEG + Sentiometer Setup (45 min)
    ↓
Trait Questionnaires: FFMQ, PSQI, ESS (paper/digital)
State Questionnaires: SSS, POMS
    ↓
[TASK 01] Auditory Oddball / P300              (~5 min)
    ↓
[TASK 02] RGB Illuminance Test                  (~10 min)
    ↓
[TASK 03] Backward Masking / Face Detection     (~10 min)
    ↓
[TASK 04] Mind-State Switching                  (~12 min)
    ├── Gameplay block (5 min)
    ├── Break (1 min)
    └── Meditation block (5 min)
    ↓
[TASK 05] SSVEP Frequency Ramp-Down            (~5 min)
    ↓
Transition to sleep (lights off, Ozlo Sleepbuds in, eye mask on)
    ↓
2-Hour Nap (EEG/polysomnography + Sentiometer continuous)
    ↓
Post-Sleep: SSS, POMS, sleep quality VAS, dream report
    ↓
Cleanup & debrief
```

Total on-site: ~4.25 hours.

---

## Paradigm Specifications

### Task 01: Auditory Oddball (P300)

| Parameter | Value |
|-----------|-------|
| Standard tone | 1000 Hz, ~70 dB SPL, 50 ms (5 ms rise/fall) |
| Deviant tone | 2000 Hz, ~70 dB SPL, 50 ms (5 ms rise/fall) |
| Ratio | 80% standard / 20% deviant |
| ISI | 1000–1200 ms (uniform jitter) |
| Total trials | ~250 (200 standard + 50 deviant) |
| Task | Active: button press on deviant |
| Audio delivery | Sony XBA-100 in-ear headphones |
| Duration | ~5 min |

**LSL markers to emit**: `task01_start`, `task01_end`, `task01_tone_standard`, `task01_tone_deviant`, `task01_response_hit`, `task01_response_false_alarm`, `task01_response_miss`

**Primary endpoint**: Sentiometer signal change 250–350 ms post-deviant vs. post-standard, correlated with EEG P300 at Pz/Cz.

### Task 02: RGB Illuminance Test

| Parameter | Value |
|-----------|-------|
| Stimuli | Full-screen solid R, G, or B on 24" iMac |
| Fixation | Central cross overlaid on all screens |
| Trials | 300 total (100 each color) |
| Trial duration | 1.6–2.6 s jittered (mean ~2 s) |
| Constraint | No consecutive same color |
| Task | Passive fixation |
| Duration | ~10 min |

**LSL markers**: `task02_start`, `task02_end`, `task02_color_red`, `task02_color_green`, `task02_color_blue`

**Primary endpoint**: SVM decoding of R/G/B from Sentiometer features vs. 10,000-iteration permutation null. **Hypothesis is NULL** — we predict the Sentiometer cannot decode color. EEG decoding is the positive control.

### Task 03: Backward Masking (Face Detection)

| Parameter | Value |
|-----------|-------|
| Target | Neutral KDEF faces, presented centrally |
| Target duration | ~17 ms (1 frame at 60 Hz) |
| Mask | Mondrian pattern, 200 ms |
| SOA | Adaptive staircase → individual ~50% threshold |
| Catch trials | ~17% mask-only (no face) |
| Response | 3-point: Seen / Not Seen / Unsure |
| Trial structure | Fixation (500 ms) → Face (~17 ms) → SOA → Mask (200 ms) → Response (~1 s) |
| Total trials | ~250–300 (including ~45–50 catch) |
| Duration | ~10 min |

**LSL markers**: `task03_start`, `task03_end`, `task03_face_onset`, `task03_mask_onset`, `task03_catch_trial`, `task03_response_seen`, `task03_response_unseen`, `task03_response_unsure`, `task03_soa_value_XX` (where XX = ms)

**Primary endpoint**: Sentiometer difference between "seen" and "unseen" at threshold SOAs. EEG VAN and P3b as positive controls.

**Stimulus note**: KDEF images are licensed for research use. They must NOT be committed to the repo. Place them in `src/tasks/03_backward_masking/stimuli/` locally. The `.gitignore` excludes this directory.

### Task 04: Mind-State Switching

**Block 1 — Gameplay (5 min):**

| Parameter | Value |
|-----------|-------|
| Game | Custom Python rhythm-runner (Geometry Dash analog) |
| Controls | Keyboard (spacebar / arrow keys) |
| Display | 24" iMac via PsychoPy or Pygame |
| LSL events | task04_obstacle_appear, task04_jump, task04_collision, task04_score_update |

**Transition**: 1-min break with on-screen timer.

**Block 2 — Meditation (5 min):**

| Parameter | Value |
|-----------|-------|
| Type | Unguided anapanasati + body scan |
| Instructions | Displayed on screen, then screen dims/blanks |
| Eyes | Closed |
| Experience | None required |

**LSL markers**: `task04_start`, `task04_end`, `task04_game_start`, `task04_game_end`, `task04_meditation_start`, `task04_meditation_end`, `task04_break_start`, `task04_break_end`, plus all in-game events (prefixed `task04_`)

**Primary endpoint**: SVM classification (gameplay vs. meditation) from Sentiometer, vs. permutation null, after partialling out HRV and motion.

### Task 05: SSVEP Frequency Ramp-Down

| Parameter | Value |
|-----------|-------|
| Stimulus | Flickering checkerboard + fixation cross on 24" iMac |
| Frequency range | 40 Hz → 1 Hz in 1-Hz steps |
| Duration per step | 7.5 s |
| Transition | Continuous (no gap between steps) |
| Task | Passive fixation, eyes open |
| Duration | 5 min (300 s) |

**LSL markers**: `task05_start`, `task05_end`, `task05_freq_step_XX` (where XX = Hz, e.g., `task05_freq_step_40`, `task05_freq_step_39`, ...)

**Primary endpoint**: No significant frequency-dependent modulation of Sentiometer (stability test). EEG SSVEP entrainment confirms stimulus effectiveness.

**Exploratory**: Sentiometer amplitude during gamma (30–40 Hz) vs. delta (1–4 Hz) stimulation.

---

## Technical Standards

### LSL Conventions
- **Session marker stream**: One persistent LSL outlet for the entire session. Created by the launcher at session start, closed at session end.
  - Stream name: `P013_Task_Markers`
  - Stream type: `Markers`
  - Channel format: `cf_string`
  - Nominal rate: 0
  - Source ID: `P013_{participant_id}` (e.g., `P013_P001`)
- **Marker naming**: All markers use a `taskNN_` prefix to identify which paradigm they belong to (e.g., `task01_tone_standard`, `task02_color_red`, `task04_game_start`). Session-level markers have no prefix: `session_start`, `session_end`.
- **Session-level markers**: `session_start`, `session_end`, `task01_start`, `task01_end`, `task02_start`, `task02_end`, etc. — all sent through the same `P013_Task_Markers` stream.
- **Architecture**: The launcher creates the outlet once, passes it to each task's `run()` function, and closes it after all tasks complete or on graceful abort. Tasks never create or destroy marker streams. In `--demo` mode (standalone testing), a task creates a temporary outlet for itself if none is provided.
- **Timestamp**: Always use `pylsl.local_clock()` at the moment of the event, not after processing.
- **Sentiometer stream**: Runs on a separate dedicated computer (see Hardware Reference). Not managed by this task suite. LabRecorder picks up the Sentiometer stream over the network alongside the task markers, EEG, and CGX streams.

### Display
- **Target display**: 24" iMac, 60 Hz refresh
- **Framework**: PsychoPy (preferred for timing-critical paradigms) or Pygame (for the game). Use PsychoPy's `visual.Window` with `waitBlanking=True` for frame-accurate stimulus onset.
- **Fullscreen**: All tasks run fullscreen. Escape key exits gracefully (with confirmation dialog).
- **Timing validation**: Each task should log flip timestamps so post-hoc timing can be verified against LSL markers.

### Audio
- **Delivery**: Sony XBA-100 in-ear headphones
- **Oddball tones**: Pre-generated .wav files (not runtime synthesis). 44.1 kHz, 16-bit. Use `generate_tones.py` to create calibrated stimuli.

### Code Style
- Python 3.11+
- `ruff` for linting (line length 100)
- `click` for all CLI interfaces
- `rich` for terminal output and progress indicators
- `pylsl` for all LSL operations
- Type hints on all public functions
- Docstrings on all modules and classes

### Config Pattern
- Each task has a `config.yaml` with all paradigm parameters
- Parameters should match the IRB protocol values exactly — do not hardcode magic numbers
- Runtime overrides via CLI flags for testing (e.g., `--n-trials 10` for quick debug runs)

### Testing
- Each task must have a `--demo` mode that runs a short version (5–10 trials) without requiring hardware
- Unit tests for marker emission, trial sequencing, and config loading
- Integration tests that verify XDF files contain expected marker streams

---

## Session Launcher

**The launcher is the single entry point for every session.** Nothing else is run by hand. `uv run python -m tasks.launcher` (no arguments required) opens a GUI that the experimenter uses to configure and start the session. Only after the experimenter clicks **Start Session** in the GUI does any task code run.

### Launcher GUI (pre-session setup screen)

The GUI opens on launch and collects/validates everything needed before the session begins:

**Participant & session metadata**
- Participant ID (text field, required, validated against `P\d{3}` format)
- Session date (auto-filled, editable)
- Experimenter initials (text field)
- Notes (free-text box for anything RAs want to record)

**Stream setup & health checks** (live-updating status panel)
- **Task marker stream**: button to create the `P013_Task_Markers` outlet with the entered participant ID baked into the source ID. Once created, show stream name, source ID, and a green "LIVE" indicator so RAs on the LabRecorder machine can confirm they see it on the network.
- **Sentiometer check**: button to run a connection/health check against the Sentiometer (via its LSL stream on the network, or via direct serial test if co-located). Displays: stream found (Y/N), sample rate, last sample timestamp, channel count. Must be green before Start Session is enabled.
- **EEG stream check**: scans the network for the BrainVision LSL stream. Displays stream name and status.
- **CGX AIM-2 check**: scans the network for the CGX LSL stream. Displays stream name and status.
- **LabRecorder confirmation**: manual checkbox — "LabRecorder is recording all four streams (task markers, Sentiometer, EEG, CGX)". Shows the exact stream names RAs should look for so they can locate them quickly on the LabRecorder machine.

**Session controls**
- **Start Session** button: disabled until participant ID is valid and all required stream checks are green (LabRecorder checkbox ticked). On click: sends `session_start` marker, closes the GUI, and launches Task 01.
- **Abort** button: available at any time once the session is running. Sends `session_end` marker, closes the outlet, saves partial data, logs the abort reason.

### Launcher responsibilities (post-GUI, during session)

1. Holds the `P013_Task_Markers` outlet for the full session (created in the GUI, never recreated)
2. Runs each task in protocol order (01 → 05), passing the shared marker outlet to each task's `run()` function
3. Between tasks, shows a brief "Task X complete — press Enter / click Continue to proceed to Task Y" screen so the experimenter can check in with the participant
4. Logs session metadata to `data/{participant_id}/session_log.json` (participant ID, experimenter, date, task start/end times, notes, any abort reasons)
5. Sends `session_end` marker and closes the outlet after all tasks complete
6. Handles graceful abort at any point: sends `session_end`, closes outlet, saves partial data, logs reason

### CLI flags (all optional — GUI is the primary interface)
- `--participant-id P001` — pre-fills the participant ID field in the GUI
- `--skip-to N` — skip to task N (crash recovery)
- `--demo` — runs all tasks in demo mode (short trial counts, for testing)
- `--no-gui` — headless fallback for CI/testing only; never used in a real session

**Framework**: Tkinter (stdlib, no extra dependency). If we later need richer widgets, revisit with PyQt/PySide. Keep the GUI code in `src/tasks/launcher_gui.py` separate from the session-runtime code in `src/tasks/launcher.py` so the session logic stays testable without a display.

---

## What NOT to Commit

- `config/local.yaml` (machine-specific serial ports, paths)
- `data/` (participant XDF files — these go to secure storage)
- `src/tasks/03_backward_masking/stimuli/` (licensed KDEF images)
- `.venv/`
- `__pycache__/`
- Any file containing participant data or PII

---

## Development Workflow

1. Always work on a feature branch: `git checkout -b task/01-oddball`
2. Test with `--demo` mode before full runs
3. Run `uv run ruff check src/` before committing
4. Run `uv run pytest` before pushing
5. PR into `main` with description of what changed and why

---

## Hardware Reference

| Device | Role | Interface | Computer |
|--------|------|-----------|----------|
| Sentiometer | Optical consciousness signal | USB-Serial → Python → LSL | **Dedicated Sentiometer laptop** (runs `src/sentiometer/` code) |
| BrainVision 64-ch EEG | Gold-standard neural recording | BrainVision Recorder → LSL | EEG acquisition PC |
| CGX AIM-2 | EOG, chin EMG, HRV, respiration, GSR, SpO₂ | CGX software → LSL | CGX acquisition PC |
| 24" iMac | Stimulus display + task marker stream | PsychoPy/Pygame, `P013_Task_Markers` LSL | **Stimulus computer** (runs `src/tasks/` code) |
| Sony XBA-100 | Audio delivery | 3.5mm jack | Connected to stimulus computer |
| Ozlo Sleepbuds | Sleep-phase audio (nap only) | Bluetooth | N/A |
| Mavogel eye mask | Light blocking (nap only) | N/A | N/A |

**Network**: All LSL streams are discoverable on the same local network. LabRecorder (running on any machine) discovers and records all streams into a single XDF file.

---

## Key Contacts

- **PI / Lab Director**: Nicco Reggente, Ph.D. (IACS)
- **Sentiometer Developer**: Prof. Santosh Helekar (Houston Methodist)
- **Expert Advisor / Sleep Scoring**: Prof. Ken Paller (Northwestern)
- **On-Call MD**: Dr. Alexander Bystritsky
- **Sponsor**: Senzient, Inc.
