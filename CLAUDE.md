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
│   ├── local.yaml               # Machine-specific overrides (gitignored)
│   └── session_defaults.yaml    # Master session config — every tunable task parameter lives here
├── src/
│   ├── sentiometer/             # DEVICE LAYER
│   │   ├── __init__.py
│   │   ├── cli.py               # Click CLI entry point (`sentiometer` command)
│   │   ├── stream.py            # Serial → LSL bridge (core streaming loop)
│   │   └── guided.py            # Guided setup wizard
│   └── tasks/                   # TASK LAYER
│       ├── __init__.py
│       ├── launcher.py          # Rich+click terminal launcher (single entry point)
│       ├── common/              # Shared utilities across all tasks
│       │   ├── __init__.py
│       │   ├── lsl_markers.py   # LSL marker stream creation & event sending
│       │   ├── display.py       # PsychoPy window management & shared display utils
│       │   ├── instructions.py  # Instruction screen rendering
│       │   ├── audio.py         # Audio playback + pre-session sound check
│       │   └── config.py        # Session config loader (master YAML)
│       ├── 01_oddball/          # Auditory Oddball / P300
│       │   ├── __init__.py
│       │   └── task.py          # Main task script
│       ├── 02_rgb_illuminance/  # RGB Illuminance / Visual Qualia Decoding
│       │   ├── __init__.py
│       │   └── task.py
│       ├── 03_backward_masking/ # Backward Masking / Face Detection
│       │   ├── __init__.py
│       │   ├── task.py
│       │   └── stimuli/         # KDEF face images + Mondrian masks (gitignored, see README)
│       ├── 04_mind_state/       # Mind-State Switching (Gameplay + Meditation)
│       │   ├── __init__.py
│       │   ├── task.py          # Orchestrator for both blocks
│       │   ├── game.py          # Custom Geometry Dash clone with LSL markers
│       │   └── meditation.py    # Meditation timer with LSL markers
│       └── 05_ssvep/            # SSVEP Frequency Ramp-Down (Vayl orchestrator)
│           ├── __init__.py
│           ├── task.py
│           └── vayl_lsl_bridge.py  # Third-party Vayl HTTP+LSL client — do NOT modify
├── assets/                      # Shared stimulus assets
│   ├── sounds/                  # Oddball tones (1000Hz, 2000Hz .wav files)
│   ├── images/                  # Fixation crosses, instruction screens
│   └── fonts/                   # If needed for task displays
├── scripts/                     # One-off utilities
│   ├── generate_tones.py        # Create calibrated oddball stimuli
│   ├── generate_mondrians.py    # Create 100 Mondrian mask images
│   ├── validate_setup.py        # Pre-session hardware/software check
│   ├── diagnose_serial.py       # Sentiometer serial debugging helper
│   └── start_stream.py          # Sentiometer device-layer quick launcher
├── tests/                       # pytest suite (85 tests)
│   ├── test_stream.py           # Sentiometer serial parser tests
│   ├── test_markers.py          # LSL marker stream tests
│   ├── test_task_configs.py     # session_defaults.yaml loader tests
│   ├── test_task01_oddball.py   # Task 01 pure helpers + simulated run
│   ├── test_task02_rgb_illuminance.py
│   ├── test_task03_backward_masking.py
│   ├── test_task04_mind_state.py
│   ├── test_task05_ssvep.py
│   ├── test_launcher.py         # Launcher orchestration tests
│   └── mock_serial.py           # Hardware-free serial emulator
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

## Session Configuration

**All configurable task parameters live in a single master YAML file**: `config/session_defaults.yaml`. There are no per-task config files. Each task's `run()` function receives only the dict for its own section (e.g. `task01_oddball`), extracted via `tasks.common.config.get_task_config()`.

- **Defaults match the IRB protocol.** The values shipped in the repo reproduce the IRB specification line-for-line. Any edit to this file for a real session is logged (via git or experimenter notes) so we always know what was run.
- **Launcher shows a summary before the session.** At startup, the Tkinter launcher GUI loads `session_defaults.yaml`, displays a per-task summary table with the current values, and provides an **Edit** affordance so the RA can adjust any value for *this session only*. Edits are held in memory and passed into the tasks; they do **not** overwrite the on-disk defaults file.
- **Demo mode overrides automatically.** When `--demo` is set, the launcher applies short-form overrides (e.g. `total_trials = 20`, `step_duration_s = 1.0`) on top of the loaded config. The master YAML is never mutated.
- **Programmatic overrides.** Any caller (launcher, task `--demo` standalone mode, test) can pass an `overrides` dict to `get_task_config(session_cfg, "task01_oddball", overrides={"total_trials": 10})`. Overrides merge on top of the in-memory copy for that call only.

### Configurable vs. fixed parameters

Parameters that **define the paradigm** are hardcoded in the task script, not in the YAML. Changing them would break the science, so they are not surfaced as knobs. Parameters that tune the dose, timing, or scope of a paradigm are configurable.

| Task | Configurable (`session_defaults.yaml`) | Fixed (hardcoded in task script) |
|---|---|---|
| 01 Oddball | total_trials, deviant_probability, ISI range, tone duration, rise/fall, response window, volume, max consecutive standards, practice_trials, practice_deviants, practice_hit_threshold, practice_fa_ceiling | Tone frequencies (1000 / 2000 Hz), target dB SPL, audio file names, active button-press task instruction, practice-then-main lifecycle |
| 02 RGB | trials_per_color, trial duration range, colors list, iti_duration_ms, break_interval_trials, break_duration_s | Fixation cross geometry, no-consecutive-same-color constraint, pure-RGB values, gray-ITI design, passive (no-response) task structure |
| 03 Masking | total_trials, catch_trial_proportion, mask/fixation/response durations, QUEST parameters (beta/delta/gamma/grain, start/min/max SOA), practice counts, practice_soa_ms, response key bindings, face_size_px, min_face_identities | 3-alternative response scheme, trial structure order, KDEF-cropped neutral stimulus set, single-frame target duration, familiarization-only practice, QUEST as the staircase algorithm |
| 04 Mind-State | game/break/meditation durations, game start/max speed, game_speed_increment_interval_s, jump_min/max_height, jump_hold_max_ms, obstacle_types list, gong_file path | Three-block order (game → break → meditation), game mechanics and physics constants, parallax background design, "no game audio" principle, meditation instruction text, eyes-closed condition, single-Pygame-window architecture |
| 05 SSVEP | carrier_start_hz, carrier_end_hz, ramp_duration_s, vayl_lsl_stream_name, vayl_api_url | Stimulation engine (Vayl desktop app), pattern-reversal 2× multiplier, continuous ramp shape, passive-fixation task, bridge client code (`vayl_lsl_bridge.py` is vendored and must not be modified) |

---

## Paradigm Specifications

### Task 01: Auditory Oddball (P300)

| Parameter | Value |
|-----------|-------|
| Standard tone | 1000 Hz, ~70 dB SPL, 100 ms (10 ms rise/fall) |
| Deviant tone | 2000 Hz, ~70 dB SPL, 100 ms (10 ms rise/fall) |
| Ratio | 80% standard / 20% deviant |
| ISI | 1100–1500 ms (jittered) |
| Total trials | ~250 (200 standard + 50 deviant) |
| Task | Active: button press on deviant |
| Audio delivery | Sony XBA-100 in-ear headphones |
| Duration | ~5 min |

**Protocol note**: Tone duration, rise/fall envelope, and ISI range match the **ERP CORE** standardized auditory oddball protocol (Kappenman et al., 2021, *NeuroImage*). This keeps our P300 data comparable with the published ERP CORE reference dataset.

### Practice gate

Before the main task begins, the participant runs a 10-trial practice block (8 standard + 2 deviant, identical stimulus parameters to the main task). After each practice block we compute hit rate (correct deviant detections) and false-alarm rate (button presses on standards) and show feedback on screen.

| Outcome | Pass criteria | Behavior |
|---|---|---|
| Pass | hit rate ≥ `practice_hit_threshold` (default 0.75) **and** false-alarm rate ≤ `practice_fa_ceiling` (default 0.50) | Show "Great job!" with the score, then start the main task on next spacebar |
| Fail | Either criterion not met | Show retry message reminding them to press only on the high tone, then repeat the practice block |

**There is no cap on practice attempts** — the participant keeps going until they pass. Each attempt is logged. If the experimenter judges that the participant cannot do the task, the standard Escape handler aborts. **Demo mode** bypasses the gate: practice always passes after attempt 1.

### LSL markers (all prefixed `task01_`)

| Phase | Markers |
|---|---|
| Session boundaries | `task01_start`, `task01_end` |
| Instructions | `task01_instructions_start`, `task01_instructions_end` |
| Practice | `task01_practice_start`, `task01_practice_end`, `task01_practice_attempt_N` (N = 1, 2, 3, …), `task01_practice_passed`, `task01_practice_tone_standard`, `task01_practice_tone_deviant` |
| Main stimulus | `task01_tone_standard`, `task01_tone_deviant` |
| Main response | `task01_response_hit`, `task01_response_false_alarm`, `task01_response_miss` |

Practice-phase responses are tracked in the behavioral CSV but **not** emitted as response markers, so the marker stream cleanly separates practice events from main-task events. Correct rejections (no button press on standard) emit no marker — they are the silent default.

**Primary endpoint**: Sentiometer signal change 250–350 ms post-deviant vs. post-standard, correlated with EEG P300 at Pz/Cz.

### Task 02: RGB Illuminance Test

| Parameter | Value |
|-----------|-------|
| Stimuli | Full-screen pure R (255,0,0), G (0,255,0), B (0,0,255) on 24" iMac |
| Fixation | Central white cross overlaid on color *and* on the ITI |
| Trials | 300 total (100 each color) |
| Trial duration | 1.2–2.0 s jittered (mean ~1.6 s) |
| ITI | 200 ms medium-gray (128,128,128) + fixation between every color |
| Constraint | No consecutive same color (max-remaining greedy placement) |
| Task | Passive fixation, no responses required |
| Breaks | 30 s rest after trial 100 and trial 200 (auto-resume; no keypress) |
| Duration | ~10 min total (3 × ~3 min color blocks + 2 × 30 s breaks) |

**Why a gray ITI?** Going directly between saturated colors creates a high-contrast flash and prominent retinal afterimages that contaminate the optical signal. A brief medium-gray screen with the fixation cross in place eases the transition without forcing a black-screen interruption.

### LSL markers (all prefixed `task02_`)

| Phase | Markers |
|---|---|
| Session boundaries | `task02_start`, `task02_end` |
| Instructions | `task02_instructions_start`, `task02_instructions_end` |
| Stimulus | `task02_color_red`, `task02_color_green`, `task02_color_blue` (one per trial at the color flip), `task02_iti` (one per trial at the gray flip) |
| Breaks | `task02_break_start`, `task02_break_end` |

**Primary endpoint**: SVM decoding of R/G/B from Sentiometer features vs. 10,000-iteration permutation null. **Hypothesis is NULL** — we predict the Sentiometer cannot decode color. EEG decoding is the positive control.

### Task 03: Backward Masking (Face Detection)

| Parameter | Value |
|-----------|-------|
| Target | 28 KDEF-cropped neutral faces (20 female, 8 male), all used, 256×256 px |
| Target duration | 1 frame (~17 ms at 60 Hz) |
| Mask | One of 100 pre-generated Mondrian patterns (256×256), 200 ms |
| SOA | QUEST adaptive staircase → individual ~50% detection threshold |
| Catch trials | ~17% mask-only (no face) — do NOT update the staircase |
| Response | 3-alternative: F = Seen, J = Not Seen, Spacebar = Unsure |
| Response window | 1500 ms |
| Trial structure | Fixation (500 ms) → Face (1 frame) → Gray + fixation (SOA − 17 ms) → Mask (200 ms) → Response (up to 1500 ms) |
| Practice | Familiarization only (no performance gate): 6 face trials @ 200 ms SOA + 2 catch |
| Total main trials | 275 (~228 face + ~47 catch) |
| Duration | ~10 min |

### Staircase

- PsychoPy `data.QuestHandler` targeting 50% detection (pThreshold=0.5).
- Parameters: `beta=3.5`, `delta=0.01`, `gamma=0.02`, `grain=1` ms.
- Starting SOA = 100 ms; clamped to `[17, 500]` ms.
- Updates only on **main-task face-present trials**. Catch trials and practice trials do not update QUEST.
- "Seen" response = correct; "Not Seen" or "Unsure" = incorrect. (Unsure is grouped with Not Seen for QUEST purposes but logged as its own response category.)

### Practice

A fixed 8-trial familiarization block (6 face-present at a clearly-visible 200 ms SOA + 2 catch, shuffled) runs before the main task. There is **no accuracy gate** — the practice is purely to demonstrate the trial structure and the response mapping. After practice the participant sees "Practice complete. In the real task, the faces will sometimes be very brief and hard to see. Just do your best."

### LSL markers (all prefixed `task03_`, 17+ distinct types)

| Phase | Markers |
|---|---|
| Session boundaries | `task03_start`, `task03_end` |
| Instructions | `task03_instructions_start`, `task03_instructions_end` |
| Practice | `task03_practice_start`, `task03_practice_end`, `task03_practice_face_onset`, `task03_practice_catch`, `task03_practice_mask_onset` |
| Main stimulus (per trial) | `task03_fixation_onset`, `task03_face_onset` (face-present), `task03_catch_trial` (mask-only), `task03_mask_onset` |
| Main response | `task03_response_seen`, `task03_response_unseen`, `task03_response_unsure`, `task03_response_timeout` |
| Staircase tracking | `task03_soa_value_XXX` (3-digit zero-padded ms, e.g. `task03_soa_value_067`) — one per main face-present trial after the response is recorded |

Practice trials do not emit response or SOA markers. Correct-rejection equivalents on catch trials (participant correctly says "Not Seen") still emit a normal `task03_response_unseen` marker — the catch / non-catch distinction is captured by the `task03_catch_trial` vs `task03_face_onset` marker that precedes it.

**Primary endpoint**: Sentiometer difference between "seen" and "unseen" at threshold SOAs. EEG VAN and P3b as positive controls.

### Stimuli (committed to this repo)

The `stimuli/faces/` and `stimuli/masks/` directories are **committed to the repo** (see `src/tasks/03_backward_masking/stimuli/README.md`). The repository is private until publication; redistribution of the KDEF images is subject to the upstream license. Mondrian masks are procedurally generated via `scripts/generate_mondrians.py` (deterministic, 100 unique 256×256 PNGs) and committed so the task is reproducible without regenerating.

### Task 04: Mind-State Switching

Three sequential blocks in **one Pygame window** (no PsychoPy/Pygame switching mid-task): gameplay → break → meditation. Total ~11 minutes.

**Block 1 — Gameplay (5 min):**

| Parameter | Value |
|-----------|-------|
| Framework | Pygame (fullscreen at 1280×720 logical, scaled to display) |
| Game | Custom side-scrolling rhythm-runner (Geometry Dash analog) |
| Controls | Spacebar only: tap = short jump, hold = high jump (variable height proportional to hold duration, clamped to `jump_hold_max_ms`) |
| Visuals | Colored player square, parallax-scrolling background (far + near layers), flat ground with grid lines, three obstacle types (spike / tall_rect / low_barrier) in contrasting colors, particle burst + white screen flash on collision |
| Audio | **None** — no sound effects, no music. Critical for clean EEG comparison between gameplay and meditation blocks. |
| Difficulty | Speed ramps linearly from `game_start_speed` (1.0×) to `game_max_speed` (2.5×) over 5 minutes at `game_speed_increment_interval_s` (30 s) boundaries. Obstacle spawn interval shrinks with speed. |
| Collision handling | Remove colliding obstacle, teleport player back to spawn x, brief white-flash grace window, instant resume |
| Duration | Exactly 5 minutes regardless of player state |

**Transition — Break (1 min):**

Gray Pygame screen with a per-second countdown: *"Take a moment to relax and stretch. The meditation will begin in N seconds."* Auto-resumes; no keypress required.

**Block 2 — Meditation (5 min):**

| Parameter | Value |
|-----------|-------|
| Framework | Pygame (same window, no handoff) |
| Flow | Instructions screen → wait for spacebar → black screen + start gong → 5 min silent timer → end gong → completion screen |
| Audio | `assets/sounds/Simple_Gong.wav` at start and end (the only audio in Task 04) |
| Eyes | Closed during the silent period |
| Experience | None required — unguided anapanasati + body scan |

### LSL markers (all prefixed `task04_`, 19 distinct types)

| Phase | Markers |
|---|---|
| Session boundaries | `task04_start`, `task04_end` |
| Overall instructions | `task04_instructions_start`, `task04_instructions_end` |
| Game boundaries | `task04_game_start`, `task04_game_end` |
| In-game events | `task04_obstacle_appear`, `task04_jump_start`, `task04_jump_end`, `task04_collision`, `task04_speed_increase` |
| Break | `task04_break_start`, `task04_break_end` |
| Meditation | `task04_meditation_instructions_start`, `task04_meditation_instructions_end`, `task04_meditation_gong_start`, `task04_meditation_start`, `task04_meditation_gong_end`, `task04_meditation_end` |

**Design note — Pygame throughout.** The alternative (Pygame for the game, PsychoPy for the break and meditation screens) would require closing and reopening a display window mid-task, which is fragile (window-placement flicker, lost keyboard focus, LSL clock drift during re-init) and adds zero scientific value. Using Pygame for every phase — including the break countdown, meditation instruction screen, and all-black meditation screen — keeps the display lifecycle clean. `pygame.mixer` handles the gong. This is the only task in the suite that does not use PsychoPy at all.

**Primary endpoint**: SVM classification (gameplay vs. meditation) from Sentiometer, vs. permutation null, after partialling out HRV and motion.

### Task 05: SSVEP Frequency Ramp-Down

**Stimulation is delegated to the Vayl desktop app.** Our task code is a thin orchestrator: it shows instruction and completion screens in Pygame, tells Vayl when to start the ramp via the bridge's HTTP API, and emits seven coarse boundary markers on the shared `P013_Task_Markers` stream. All fine-grained frequency tracking lives in LSL streams that Vayl's bridge creates automatically.

| Parameter | Value |
|---|---|
| Stimulus | Full-screen pattern-reversal checkerboard rendered by Vayl directly on the GPU |
| Carrier ramp | 20.0 Hz → 0.5 Hz, linear, over 300 s |
| Effective SSVEP | 40 Hz → 1 Hz (= 2 × carrier — see "Carrier vs. effective" below) |
| Ramp shape | Continuous (no per-step gaps) |
| Task | Passive fixation, eyes open, no responses |
| Duration | ~5 min (300 s ramp + instruction/completion screens) |

### Carrier vs. effective frequency

Pattern-reversal checkerboards produce **two visual events per carrier cycle** (black → white and white → black), so the effective SSVEP stimulation frequency is `2 × carrier_hz`. `config/session_defaults.yaml` stores the **carrier** values so they match what gets POSTed to Vayl's API; the effective SSVEP rate is documented in this table and reported by Vayl's own streams. Our P013 marker stream only emits coarse boundaries — we do not duplicate the fine-grained frequency data.

### Architecture

- **Vayl desktop app** runs on the stimulus iMac and must be launched **before** Task 05 starts. The task checks connectivity via `bridge.status()` at startup and raises a clear error in production if the app is not reachable. In `--demo` mode a missing Vayl gracefully falls back to a `sleep(duration_s)` simulation so the orchestration code path is still exercised.
- **`vayl_lsl_bridge.VaylBridge`** (committed at `src/tasks/05_ssvep/vayl_lsl_bridge.py`, **do not modify**) is the Python client: it talks to Vayl's localhost HTTP API at `http://127.0.0.1:9471`, creates its own LSL outlets, and (when a ramp is active) runs a background thread pushing interpolated effective-frequency samples at 250 Hz to `VaylStim_Freq`.
- **Pygame** (not PsychoPy) handles the instruction and completion screens so there is exactly one display-framework stack alongside Vayl's overlay. Between those screens the Pygame window is `iconify`'d so Vayl's overlay is visible to the participant.

### LSL streams added by Task 05

| Stream name | Type | Rate | Content |
|---|---|---|---|
| `VaylStim` | Markers (string/JSON) | irregular | `ramp_start`, `ramp_stop`, `overlay_off` events with `wallTimeMs`, `stimFreqHz`, `stimFreqEndHz`, `carrierHz`, `carrierEndHz` |
| `VaylStim_Freq` | Stimulus (float32) | 250 Hz | Continuous interpolated effective SSVEP frequency; pushes 0.0 when overlay is off |

LabRecorder picks up both of these alongside `P013_Task_Markers`, the Sentiometer stream, the BrainVision EEG stream, and the CGX AIM-2 stream, producing a single XDF per session where every relevant signal is already aligned on the LSL clock.

### LSL markers (all prefixed `task05_`, **7 distinct types on `P013_Task_Markers`**)

| Phase | Markers |
|---|---|
| Session boundaries | `task05_start`, `task05_end` |
| Instructions | `task05_instructions_start`, `task05_instructions_end` |
| Ramp boundaries | `task05_ramp_begin` (just before `bridge.start_ramp()`), `task05_ramp_end` (after `bridge.wait_for_ramp()` returns) |
| Overlay off | `task05_overlay_off` (after `bridge.turn_off()` fades the overlay) |

**Primary endpoint**: No significant frequency-dependent modulation of the Sentiometer signal (stability test). EEG SSVEP entrainment on `VaylStim_Freq` confirms stimulus effectiveness.

**Exploratory**: Sentiometer amplitude during gamma (30–40 Hz effective) vs. delta (1–4 Hz effective) stimulation.

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

**The launcher is the single entry point for every session.** It is a terminal application built with `click` (CLI) and `rich` (formatted output). No GUI — just a Rich-rendered pre-flight flow and a clean CLI. `uv run python -m tasks.launcher --participant-id P001`.

### Pre-session flow (before any task runs)

1. **Config summary.** The launcher loads `config/session_defaults.yaml` and prints a Rich `Table` per task showing every parameter with its current value.
2. **Edit offer.** `Edit any parameters? (y/N)` — if yes, the launcher opens the YAML in `$EDITOR` (or `notepad` on Windows), waits for the editor to exit, and reloads the file. Edits apply to this session only (and persist on disk — there is no in-memory staging).
3. **Outlet creation.** Creates the `P013_Task_Markers` LSL outlet via `create_session_outlet(participant_id)` so LabRecorder can discover it.
4. **Pre-flight checklist** (Rich table, each row shows `OK` / `WARN` / `FAIL` plus a Notes column):
   - Participant ID present — hard-required.
   - P013 marker outlet created — hard-required.
   - EEG stream — any LSL stream with `type=EEG` (warn only; does not block).
   - Sentiometer stream — LSL stream named `IACS_Sentiometer` (warn only).
   - Vayl app reachable at `http://127.0.0.1:9471` (warn only; needed for Task 05 only).
5. **LabRecorder confirmation.** Manual — the launcher prompts "Is LabRecorder running and recording? (press Enter to confirm)".
6. **Go.** Final "Press Enter to begin session" confirmation.

All LSL checks and the Vayl check are skipped in `--demo` mode.

### Session runtime

1. Sends `session_start` on `P013_Task_Markers`.
2. Writes `data/{participant_id}/session_log.json` with a config snapshot, per-task start/end timestamps (updated as each task completes), and system info. The log is re-written after every task so a crash mid-session still leaves a partial record on disk.
3. Runs Tasks 01 → 05 in order. Each task module is loaded via `importlib.import_module("tasks.0N_name.task")` (digit-prefixed directory names block plain `import`) and called with `run(outlet, config, participant_id, demo, output_dir)`. Each task emits its own `task0N_start` / `task0N_end` markers internally — **the launcher does not duplicate them.**
4. Between tasks, the launcher shows a "Task X complete. (N done, M remaining.) Press Enter to continue" prompt.
5. On a per-task failure (Python exception inside `run()`), the task is marked `failed` in the session log, the error message is recorded, and the session continues to the next task. (`KeyboardInterrupt` is the exception — see below.)
6. After all tasks complete, sends `session_end` and closes the outlet.

### Graceful abort

On `KeyboardInterrupt` (Ctrl+C) at any point during the task loop:
1. `session_abort` marker sent on `P013_Task_Markers`.
2. Session log updated with `status=aborted`, `aborted_during=<task name>`, `abort_reason=KeyboardInterrupt (Ctrl+C)`, and the in-progress task marked `aborted`.
3. The partial log is persisted and the outlet is cleanly released.
4. Later tasks are simply absent from the log — they never entered the loop.

### CLI flags

- `--participant-id P001` / `-p P001` — participant ID. Prompted interactively if omitted.
- `--demo` — pass `demo=True` to every task and skip all pre-flight LSL/Vayl checks.
- `--skip-to N` — start from task N (1-5). Tasks 1..N-1 are marked `skipped` in the session log. Useful for recovery after a mid-session crash.
- `--config PATH` — override the session config YAML path.

### Headless / testable runtime

`launcher.run_session(participant_id, *, interactive=False, task_runner=..., ...)` is the testable entry point. `interactive=False` bypasses every `Prompt` / `Confirm` call so no stdin is read; `task_runner` injects a callable that stands in for `_run_task` so the orchestration logic (session log, skip-to, abort, demo propagation) can be exercised without importing and running the five task modules. Tests in `tests/test_launcher.py` use this to verify end-to-end behavior without PsychoPy, Pygame, Vayl, or real stimuli on disk.

---

## What NOT to Commit

- `config/local.yaml` (machine-specific serial ports, paths)
- `data/` (participant XDF files — these go to secure storage)
- `.venv/`
- `__pycache__/`
- Any file containing participant data or PII

Note: The KDEF neutral faces and procedurally-generated Mondrian masks for Task 03 **are** committed to this (private) repo so the task is fully reproducible without a separate download step. See `src/tasks/03_backward_masking/stimuli/README.md` for attribution and licensing notes.

---

## Development Workflow

1. Always work on a feature branch: `git checkout -b task/01-oddball`
2. Test with `--demo` mode before full runs
3. Run `uv run ruff check src/` before committing
4. Run `uv run pytest` before pushing
5. PR into `main` with description of what changed and why

**PsychoPy tasks only run on the macOS stimulus computer.** The Windows dev machine is used for device code, scaffolding, and non-display tests only.

---

## Hardware Reference

| Device | Role | Interface | Computer |
|--------|------|-----------|----------|
| Sentiometer | Optical consciousness signal | USB-Serial → Python → LSL | **Dedicated Sentiometer laptop** (runs `src/sentiometer/` code) |
| BrainVision 64-ch EEG | Gold-standard neural recording | BrainVision Recorder → LSL | EEG acquisition PC |
| CGX AIM-2 | EOG, chin EMG, HRV, respiration, GSR, SpO₂ | CGX software → LSL | CGX acquisition PC |
| 24" iMac | Stimulus display + task marker stream | PsychoPy/Pygame, `P013_Task_Markers` LSL | **Stimulus computer** (runs `src/tasks/` code) |
| Vayl desktop app | GPU-driven SSVEP checkerboard overlay for Task 05 | Localhost HTTP API (port 9471) + its own `VaylStim` / `VaylStim_Freq` LSL streams | Stimulus computer (must be launched before Task 05) |
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

---

## Data Analysis (reading this back when you have XDFs)

### TODO — prepare first when data starts arriving

**Build a synchronised per-domain time-series for each session.** Once the
RAs bring back the real P013 XDFs, the first analysis step is to pivot
every stream onto a common LSL time axis and write out one long-format
CSV (or parquet) per session that contains, column-wise:

- `t_lsl` — the synchronised LSL timestamp (`pylsl.local_clock()`)
- EEG channels (64) from `BrainAmpSeries-Dev_1` at 500 Hz
- CGX AIM-2 channels (13, with `ExGa 1-4` renamed to E1-M2 / E2-M1 /
  ChinZ-Chin1 / Chin2-Chin1prime per the P013 montage) at 500 Hz
- Sentiometer channels (5 photodiodes + device_ts) at 500 Hz
- **Vayl effective SSVEP Hz at each timepoint** — there is no
  dedicated continuous stream in the production config; compute it
  analytically from the `ramp_start` JSON marker on
  `P013_Task_Markers`. The formula is deterministic and exact (no
  extrapolation / no estimate): linear interpolation of the commanded
  ramp from `stimFreqHz` to `stimFreqEndHz` over `durationSeconds`,
  clipped to 0 outside the ramp window. Anchor to `wallTimeMs` (sub-ms
  GPU onset) rather than the LSL timestamp for sub-ms EEG alignment.
- Task marker columns (one-hot or categorical) derived from
  `P013_Task_Markers` so each epoch has the right paradigm / trial
  label attached.

Rationale we already discussed (2026-04-17): we decided NOT to push a
per-sample Hz tick onto `P013_Task_Markers` at stim time. The ramp is
fully specified by the `ramp_start` JSON, so reconstructing it at
preprocessing is mathematically identical to streaming it live and
keeps the XDF single-stream for LabRecorder. See
`README.md#marker-reference-for-data-analysis` → Task 05 for the
formula snippet; a `analysis/vayl_hz.py` helper is planned for when
the first real sessions come in (to wrap `load_ramp_spec(xdf)` +
`eff_hz_at(t, spec)` so every downstream script uses the same
canonical function).

Keep the preprocessing output under `outputs/<SUBJECT>/preprocessed/`
so it sits next to the Paller-handoff bundle produced by
`scripts/xdf_to_edf/run_all.py`.

### General analysis context

If you're returning to this project to analyse recorded sessions, the code
for *running* a session has done its job — every paradigm's stimulus + timing
is captured in `P013_Task_Markers` with marker strings that uniquely name
what happened and when. The README has a per-task marker reference with
expected deltas; that's the canonical spec. This section is the short
onramp for analysis work.

### Tooling already in the repo

- `scripts/verify_xdf.py` — Rich-formatted per-task coverage check against
  the spec below, plus a summary, a "suggested edits" panel for any gaps,
  and (always-on) a chronological marker timeline in Pacific time.
  Flags: `--no-timeline`, `--timeline-summary-only`.
- `scripts/timeline_xdf.py` — same chronology in standalone form.
- Both default to the single `.xdf` in `sampledata/` (gitignored) and
  accept a path argument for ad-hoc runs.

Install deps (pyxdf + numpy + rich) via `uv sync --extra dev`. Don't
`pip install pyxdf` into a global env — stay inside the project venv.

### Streams you should expect in a full-production XDF

1. `P013_Task_Markers` (stream type `Markers`, channel format `cf_string`,
   source_id **stable** `P013_Launcher`). The stable source_id is load-bearing:
   before the fix in commit `8094ab4` it swapped mid-session, which broke
   LabRecorder's subscription and silently produced an empty stream.
2. `VaylStim_Freq` (type `Stimulus`, 250 Hz float32, source_id
   `vayl_freq_VaylStim`). Continuous effective-SSVEP Hz from the bridge.
   **Not required** for analysis — the ramp is a linear function fully
   specified by the JSON `ramp_start` event on `P013_Task_Markers`.
3. `Sentiometer` (type `Misc`, 500 Hz float32, 6 channels: `device_ts`,
   `PD1`–`PD5`).
4. EEG — stream name is vendor-specific (`BrainAmpSeries-Dev_1` for
   BrainVision; `CGX AIM Phys. Mon. AIM-NNNN` for CGX). Both are `type=EEG`.
   CGX also emits an `Impedance` stream.
5. `VaylStim` — **no longer expected** in new recordings. The bridge's
   marker outlet is redirected to `P013_Task_Markers` so every marker lives
   on a single stream.

### Minimal XDF-loading recipe

```python
import numpy as np
import pyxdf

streams, header = pyxdf.load_xdf("sampledata/your_session.xdf")

def get_stream(name):
    return next(s for s in streams if s["info"]["name"][0] == name)

markers = get_stream("P013_Task_Markers")
marker_strings = [s[0] for s in markers["time_series"]]
marker_ts      = np.asarray(markers["time_stamps"], dtype=float)  # LSL seconds
```

All streams share the LSL clock (`pylsl.local_clock()`), so subtracting
timestamps between streams gives seconds directly.

### Epoching cheatsheet per task

The README's marker reference has the full spec. Quick reminders for the
most common epoching anchors:

- **Task 01 P300** — epoch on `task01_tone_deviant` vs. `task01_tone_standard`
  (main block only; practice tones are prefixed `task01_practice_*`). Lock
  to tone onset, window `[-200, 800] ms`.
- **Task 02 decoding** — one trial per `task02_color_{red,green,blue}`,
  duration runs until the following `task02_iti`. Exclude breaks
  (`task02_break_start` → `task02_break_end`).
- **Task 03 backward masking** — epoch on `task03_face_onset` (face-present
  trials only; catch trials use `task03_catch_trial` and never emit
  `task03_face_onset`). The SOA used on each face trial is in the
  `task03_soa_value_XXX` marker that immediately follows the response.
  Split epochs by the response label (`task03_response_seen` /
  `_unseen` / `_unsure` / `_timeout`) to get the hit / miss contrast.
- **Task 04 mind-state** — use `task04_game_start` → `task04_game_end` and
  `task04_meditation_start` → `task04_meditation_end` as the two
  condition windows. `task04_break_*` is transition, not a condition.
  For obstacle-aligned work use `task04_obstacle_appear` / `_jump_start` /
  `_collision`.
- **Task 05 SSVEP** — epoch inside `[task05_ramp_begin, task05_ramp_end]`
  and compute instantaneous effective frequency from the JSON
  `ramp_start` event:
  ```python
  progress = (t - ramp_start_lsl) / duration_s
  eff_hz   = stim_start_hz + progress * (stim_end_hz - stim_start_hz)
  ```
  For sub-ms EEG alignment use the `wallTimeMs` field inside the JSON
  instead of the LSL timestamp (the LSL one has ~1–5 ms HTTP round-trip
  latency; `wallTimeMs` is measured on Vayl's server at the GPU flip).

### Known quirks / gotchas

- **Frame-flip accuracy**: PsychoPy markers are taken right after
  `win.flip()` returns, which blocks until vsync (`waitBlanking=True`).
  Accuracy is one refresh, ~16 ms at 60 Hz. Fine for all our endpoints,
  NOT fine for sub-ms phase-locked work — use Vayl's `wallTimeMs` for
  that.
- **Practice phase has no response markers**: Task 01 and Task 03
  practice blocks emit tone/face/mask markers but do NOT emit
  `task01_response_*` / `task03_response_*`. The behavioral CSV written
  by each task (in `data/{pid}/task0N_*.csv`) records practice
  responses; the LSL stream intentionally doesn't.
- **Task 01 Sound.play() timing on pygame backend**: PsychoPy 2025's
  pygame `Sound.play()` has a broken `isPlaying` guard that would
  silently no-op every second call. `tasks/01_oddball/task.py` resets
  `sound._isPlaying = False` before every `.play()` — the workaround
  is committed but worth knowing if you clone the behavioral code.
- **Task 05 without the VaylStim_Freq stream** is still fully analysable:
  the JSON `ramp_start` event on `P013_Task_Markers` carries the full
  ramp spec (`stimFreqHz`, `stimFreqEndHz`, `durationSeconds`,
  `wallTimeMs`). The 250 Hz stream is just a pre-sampled linear
  interpolation of the same function.
- **participant_id**: stored as a marker string `participant_id:P001`
  right after `session_start`. Since commit `8094ab4` the outlet's
  `source_id` is the constant `P013_Launcher`, not `P013_P001`, to keep
  LabRecorder's subscription stable mid-session.
- **session_log.json** in `data/{pid}/session_log.json` is the ground
  truth for which tasks actually ran (vs. skipped / failed / aborted)
  and a full snapshot of the config used for this participant. Cross-
  reference against the XDF when reconciling odd recordings.

### Behavioural CSVs (per-task, alongside the XDF)

Each task writes a task-specific CSV to `data/{pid}/` on completion.
Timestamps in these CSVs are LSL seconds, same clock as the XDF, so
cross-referencing is a subtraction.

- `task01_oddball_*.csv` — `trial_number`, `phase` (practice/main),
  `practice_attempt`, `tone_type`, `tone_onset_time` (LSL), `response_time`,
  `response_type` (hit/miss/false_alarm/correct_rejection), `rt_ms`.
- `task02_rgb_illuminance_*.csv` — trial index, color, onset, duration.
- `task03_backward_masking_*.csv` — trial, phase, `face_id`, `soa_ms`,
  `response`, `rt_ms`, QUEST state.
- `task04_mind_state_events.csv` — phase, event type, timestamp.
- `task05_session_log.csv` — event + timestamp + details
  (Vayl connectivity, carrier frequencies, `wallTimeMs` per event).

Use these for per-participant behavioural summaries; use the XDF for
continuous / time-locked EEG + Sentiometer signals.

### PSG handoff pipeline (XDF → EDF+ + manifest for Paller)

When a sleep session needs to be sent to Dr. Ken Paller's lab for
scoring, run the full bundle pipeline:

```bash
# Drop any session XDF into sampledata/ — the newest file wins.
# BIDS-style "sub-<id>_…" filenames auto-extract the subject ID
# (e.g. "sub-Sam_…" → Sam); anything else falls back to the first
# underscore-delimited token of the filename stem ("S001_night2"
# → "S001"). Use names like Sam / S001 / PILOT_02 — keep it short
# and alphanumeric.

uv sync --extra dev                     # installs pyxdf + pyedflib + reportlab
uv run python scripts/xdf_to_edf/run_all.py
```

That runs four steps in order (each can also be invoked on its own —
they all autodetect the newest XDF and write to the same bundle):

1. `scripts/xdf_to_edf/01_inspect.py` — read-only stream inventory,
   cfg/rwksp cross-check, plus a **forensic on the first 120 s of
   the EEG stream** (timestamp continuity, saturation tally, flat
   windows, per-channel RMS heatmap, raw-trace PNG, hypothesis
   panel). This is where we first identified Yaya's rail-saturated
   peripheral channels.
2. `scripts/xdf_to_edf/01b_spectral.py` — Welch PSD (4 s / 50 %
   overlap) on a clean 60 s segment at t=300 s for Cz, Fp1, and the
   four CGX EXG AASM derivations; 60/120/180 Hz SNR per channel;
   EXG broadband ratio (30–100 vs 1–30 Hz); CGX impedance snapshot.
3. `scripts/xdf_to_edf/02_convert.py` — EDF+ write: align EEG +
   CGX on LSL timestamps, rename CGX ExGa → AASM (E1-M2, E2-M1,
   ChinZ-Chin1, Chin2-Chin1prime), keep all 64 EEG channels, flag
   bad ones via the EDF `prefilter` field (`BAD_RAIL_SAT`), write
   any marker stream as EDF+ annotations, spot-check 10 random
   samples per channel against the source XDF.
4. `scripts/xdf_to_edf/03_manifest.py` — ReportLab PDF manifest
   (quick-answers, data-quality summary with green/yellow/red
   tally, numbered channel table, AASM scoring workarounds,
   marker legend, technical notes) + recipient README.

All paths live in `scripts/xdf_to_edf/_common.py` — change
`find_xdf()` or `subject_from_xdf()` there if the filename
conventions ever change.

The entire bundle for a session goes under `outputs/<SUBJECT>/` and
that folder is what gets sent to Paller. `outputs/` is gitignored so
nothing large or participant-identifying lives in the repo; the
source XDF in `sampledata/` is also gitignored.

#### Known situational quirks captured in the bundle

- The CGX AIM-2 ExGa 1–4 ports carry **bipolar PSG derivations**
  in the P013 montage: EOG-L vs right mastoid, EOG-R vs left
  mastoid, chin-midline vs chin-upper, chin-lateral-R vs
  chin-lateral-L. The AASM scoring software receives these pre-
  referenced and they are immune to BrainVision cap-side
  electrode failure.
- The BrainVision cap's `TP9` / `TP10` are what we'd *normally*
  use as M1/M2 proxies for monopolar EEG derivations. If the
  Step 1 forensic flags them as rail-saturated, Section 4 of
  the manifest gives three workaround derivations (monopolar
  to FCz, common-average, contralateral homologs).
- CGX non-EEG channels (SpO2 / HR / GSR / Temp / Resp / PPG)
  come through LSL with their unit declared as `microvolts` even
  though the values are device-internal scaling. The converter
  preserves the raw values and the manifest flags the caveat.
- EDF+ 8-character `physical_min/max` fields overflow on the
  CGX non-EEG channels with large internal scaling; they're
  clamped to ±8,388,608 and the clip is documented in
  `conversion_log.txt`. Sleep scoring doesn't use absolute
  magnitudes on these channels so the clip is harmless.
- Unique `subject` ID per run — drop the next session's XDF in
  `sampledata/` and re-run `run_all.py`. Previous bundles stay
  untouched in their own folders.
