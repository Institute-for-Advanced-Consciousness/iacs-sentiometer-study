# Sentiometer Task Suite — Action Plan & Acceptance Criteria

## Overview

This document defines every action item for building the Sentiometer study task suite, organized in execution order. Each item has clear acceptance criteria so we know when it's done.

**Repo**: `Institute-for-Advanced-Consciousness/iacs-sentiometer-study`
**Target**: All tasks coded, tested, and ready for pilot by May 1, 2026 data collection launch.

---

## Phase 0: Repository Reorganization

### 0.1 — Update repo structure to match CLAUDE.md

**What**: Reorganize `src/tasks/` from the current flat layout to numbered directories. Create `src/tasks/common/`, `assets/`, and `scripts/` directories. Move existing device code if needed.

**Acceptance Criteria**:
- [ ] Directory tree matches CLAUDE.md structure exactly
- [ ] `src/tasks/common/` exists with `__init__.py`, `lsl_markers.py`, `display.py`, `instructions.py`, `config.py`
- [ ] All five task directories exist with `__init__.py`, `task.py`, `config.yaml`
- [ ] `assets/sounds/`, `assets/images/`, `assets/fonts/` directories exist
- [ ] `scripts/` directory exists with placeholder scripts
- [ ] Existing sentiometer device code (`src/sentiometer/`) is untouched and still functional
- [ ] `uv sync` succeeds; `uv run sentiometer --help` still works
- [ ] `.gitignore` updated: `data/`, `config/local.yaml`, `src/tasks/03_backward_masking/stimuli/`, `.venv/`, `__pycache__/`

### 0.2 — Add CLAUDE.md to repo root

**What**: Place the CLAUDE.md file at the repository root so Claude Code reads it automatically.

**Acceptance Criteria**:
- [ ] `CLAUDE.md` is at repo root
- [ ] Content matches the version we produced (full paradigm specs, LSL conventions, session flow)

### 0.3 — Update pyproject.toml dependencies

**What**: Ensure PsychoPy, Pygame, numpy, pandas, and any other task dependencies are in `[project.optional-dependencies.tasks]`.

**Acceptance Criteria**:
- [ ] `uv sync --extra tasks` installs PsychoPy, Pygame, numpy, pandas without errors
- [ ] `uv sync` (without extras) still works for device-only usage
- [ ] Python 3.11+ requirement is enforced

### 0.4 — Build common utilities (`src/tasks/common/`)

**What**: Implement the shared modules that every task depends on.

**Acceptance Criteria for `lsl_markers.py`**:
- [ ] `create_session_outlet(participant_id: str) -> StreamOutlet` creates the `P013_Task_Markers` stream (name=`P013_Task_Markers`, type=`Markers`, channel_format=`cf_string`, nominal_rate=0, source_id=`P013_{participant_id}`). Called once by the launcher at session start.
- [ ] `send_marker(outlet: StreamOutlet, marker: str)` sends a string marker with `local_clock()` timestamp
- [ ] Each task's `run()` function receives the outlet as a required parameter — tasks never create or destroy streams
- [ ] In `--demo` mode (standalone testing), a task can call `create_session_outlet("DEMO")` to create a temporary outlet for itself
- [ ] Unit test: create outlet, send markers from multiple "tasks" sequentially, verify all are receivable by a test inlet without dropping or reconnecting

**Acceptance Criteria for `display.py`**:
- [ ] `create_window(fullscreen: bool = True) -> visual.Window` creates a PsychoPy window on the 24" iMac
- [ ] Window defaults: 60 Hz, `waitBlanking=True`, background black
- [ ] `draw_fixation(win, color='white')` draws a standard fixation cross
- [ ] Escape key handling: pressing Escape shows confirmation dialog, then exits cleanly if confirmed
- [ ] All flip timestamps are logged for post-hoc timing verification

**Acceptance Criteria for `instructions.py`**:
- [ ] `show_instructions(win, text: str, wait_key='space')` renders instruction text and waits for keypress
- [ ] `show_countdown(win, seconds: int)` displays a countdown timer
- [ ] Text is readable (white on dark, ~24pt, centered)

**Acceptance Criteria for `config.py`**:
- [ ] `load_task_config(task_dir: str) -> dict` loads the task's `config.yaml`
- [ ] CLI overrides (passed as dict) merge on top of YAML defaults
- [ ] Missing config file raises a clear error

---

## Phase 1: Task Paradigms (Build in Order)

### 1.1 — Task 01: Auditory Oddball (P300)

**What**: Standard two-tone oddball with active button press. ~250 trials, ~5 min.

**Acceptance Criteria**:
- [ ] Task accepts `outlet: StreamOutlet` as a required parameter; does NOT create or destroy any LSL streams
- [ ] `config.yaml` contains all parameters from IRB (frequencies, durations, ISI range, trial counts, ratio)
- [ ] Pre-generated .wav files for 1000 Hz and 2000 Hz tones (50 ms, 5 ms rise/fall, 44.1 kHz, 16-bit) in `assets/sounds/`
- [ ] `scripts/generate_tones.py` produces these files reproducibly
- [ ] Trial sequence: pseudorandom with constraint that no more than 3 consecutive standards occur between deviants
- [ ] ISI jittered uniformly 1000–1200 ms
- [ ] LSL markers emitted (all prefixed `task01_`): `task01_start`, `task01_end`, `task01_tone_standard`, `task01_tone_deviant`, `task01_response_hit`, `task01_response_false_alarm`, `task01_response_miss`
- [ ] Marker timestamp is at tone onset (not after audio buffer fill)
- [ ] Button press within 200–1000 ms post-deviant = hit; button press after standard = false alarm
- [ ] `--demo` mode: 20 trials (16 standard + 4 deviant), completes in <30 seconds. Creates its own temporary outlet if none is passed.
- [ ] Behavioral log saved: CSV with columns `trial, tone_type, onset_time, response_time, response_type, rt_ms`
- [ ] Task exits cleanly and returns control to launcher

### 1.2 — Task 02: RGB Illuminance Test

**What**: 300 full-screen color trials (100 R / 100 G / 100 B), passive fixation. ~10 min.

**Acceptance Criteria**:
- [ ] Task accepts `outlet: StreamOutlet` as a required parameter; does NOT create or destroy any LSL streams
- [ ] `config.yaml` contains: trial counts per color, ISI range (1.6–2.6 s), constraint (no consecutive same color)
- [ ] Trial sequence: pseudorandom permutation with no-repeat constraint verified
- [ ] Full-screen solid color fills entire display (no borders, no taskbar)
- [ ] Central fixation cross rendered on all color screens (thin white cross)
- [ ] ISI jittered uniformly 1.6–2.6 s
- [ ] No black/blank screens between colors — direct color-to-color transitions
- [ ] LSL markers (all prefixed `task02_`): `task02_start`, `task02_end`, `task02_color_red`, `task02_color_green`, `task02_color_blue` at frame flip
- [ ] `--demo` mode: 15 trials (5 each), completes in <30 seconds. Creates its own temporary outlet if none is passed.
- [ ] Behavioral log: CSV with `trial, color, onset_time, duration_s`
- [ ] Color values are pure (R=255,0,0; G=0,255,0; B=0,0,255) — confirm on display with colorimeter or at minimum document the RGB values used

### 1.3 — Task 03: Backward Masking (Face Detection)

**What**: Adaptive staircase masking with KDEF faces. ~250–300 trials, ~10 min.

**Acceptance Criteria**:
- [ ] Task accepts `outlet: StreamOutlet` as a required parameter; does NOT create or destroy any LSL streams
- [ ] `config.yaml` contains: target duration (1 frame = ~17 ms), mask duration (200 ms), fixation duration (500 ms), response window (~1 s), catch trial proportion (~17%), staircase parameters
- [ ] Adaptive staircase: 2-down/1-up or QUEST procedure converging on ~50% detection threshold
- [ ] SOA starts at a clearly visible level (e.g., 100 ms) and adapts per participant
- [ ] Stimuli: neutral KDEF faces loaded from `stimuli/` directory; clear error if directory is empty/missing
- [ ] `scripts/generate_mondrians.py` creates Mondrian mask images (random colored rectangles, specified size)
- [ ] Trial structure: Fixation (500 ms) → Face (1 frame) → Blank/gray (SOA – 17 ms) → Mask (200 ms) → Response screen
- [ ] Catch trials: ~17% of trials show mask only (no face), randomly interleaved
- [ ] Response: 3-button (Seen / Not Seen / Unsure) — keys clearly displayed on response screen
- [ ] LSL markers (all prefixed `task03_`): `task03_start`, `task03_end`, `task03_face_onset`, `task03_mask_onset`, `task03_catch_trial`, `task03_response_seen`, `task03_response_unseen`, `task03_response_unsure`, and `task03_soa_value_XX`
- [ ] Staircase state saved to log (all reversals, threshold estimate at end)
- [ ] `--demo` mode: 20 trials (fixed SOAs, no staircase), completes in <1 min. Creates its own temporary outlet if none is passed.
- [ ] Behavioral log: CSV with `trial, trial_type (face/catch), soa_ms, response, confidence, rt_ms, staircase_level`
- [ ] README in task directory documents KDEF license requirements and how to populate `stimuli/`

### 1.4 — Task 04: Mind-State Switching

**What**: 5-min gameplay → 1-min break → 5-min meditation. ~12 min total.

**Acceptance Criteria — General**:
- [ ] Task accepts `outlet: StreamOutlet` as a required parameter; does NOT create or destroy any LSL streams
- [ ] All LSL markers prefixed with `task04_`

**Acceptance Criteria — Game (Block 1)**:
- [ ] Custom Python rhythm-runner (Geometry Dash analog) implemented in Pygame or PsychoPy
- [ ] Game mechanics: character auto-scrolls right; spacebar = jump; obstacles at regular/irregular intervals
- [ ] Visuals: simple but engaging (geometric shapes, not placeholder rectangles)
- [ ] Speed increases gradually over 5 minutes to maintain engagement
- [ ] Collision detection: clear visual + audio feedback on collision, brief respawn
- [ ] LSL markers for every game event: `task04_game_start`, `task04_game_end`, `task04_obstacle_appear`, `task04_jump`, `task04_collision`, `task04_score_update`, `task04_speed_increase`
- [ ] Score displayed on screen
- [ ] Game ends automatically at 5 minutes regardless of state

**Acceptance Criteria — Break (Transition)**:
- [ ] 1-minute countdown displayed on screen
- [ ] Text: "Take a moment to relax and stretch. The next part will begin shortly."
- [ ] LSL markers: `task04_break_start`, `task04_break_end`

**Acceptance Criteria — Meditation (Block 2)**:
- [ ] Instruction screen displayed: "Close your eyes. Focus your attention on the sensation of your breath at the nostrils. When you feel settled, begin scanning through your body from head to toe. If you notice your mind wandering, gently return your attention to the breath."
- [ ] After participant presses spacebar to begin, screen dims to black (or very dark gray)
- [ ] 5-minute timer (not displayed to participant)
- [ ] Soft audio chime at end of meditation
- [ ] LSL markers: `task04_meditation_start`, `task04_meditation_end`

**Acceptance Criteria — Overall**:
- [ ] Task orchestrator (`task04_start`, `task04_end`) runs both blocks in fixed order with break between
- [ ] `--demo` mode: 30-second game, 10-second break, 30-second meditation. Creates its own temporary outlet if none is passed.
- [ ] Behavioral log: CSV with game events (timestamp, event_type, score, speed_level) and meditation metadata (start_time, end_time, total_duration)

### 1.5 — Task 05: SSVEP Frequency Ramp-Down

**What**: Flickering checkerboard ramping from 40 Hz → 1 Hz in 1-Hz steps. 5 min.

**Acceptance Criteria**:
- [ ] Task accepts `outlet: StreamOutlet` as a required parameter; does NOT create or destroy any LSL streams
- [ ] `config.yaml` contains: frequency range (40–1), step duration (7.5 s), total steps (40), total duration (300 s)
- [ ] Flickering checkerboard pattern with fixation cross overlaid
- [ ] Flicker is frame-accurate: for each target frequency, compute the optimal on/off frame pattern given 60 Hz refresh
- [ ] **Known limitation documented**: frequencies above 30 Hz cannot be accurately rendered on a 60 Hz display. Document which frequencies are achievable and which are approximated. Consider: at 60 Hz refresh, 40 Hz flicker is physically impossible (Nyquist). Note this in config and in the CLAUDE.md.
- [ ] Continuous transitions — no gap between frequency steps
- [ ] LSL markers (all prefixed `task05_`): `task05_start`, `task05_end`, `task05_freq_step_XX` at each frequency transition
- [ ] Fixation cross visible throughout
- [ ] `--demo` mode: 3 steps (40, 20, 1 Hz), 3 seconds each, completes in <10 seconds. Creates its own temporary outlet if none is passed.
- [ ] Behavioral log: CSV with `step, frequency_hz, onset_time, offset_time, actual_frame_count`
- [ ] Timing log: actual flip timestamps per frame for post-hoc verification of achieved flicker frequency

**⚠️ CRITICAL NOTE ON DISPLAY REFRESH**: The IRB protocol specifies 40 Hz → 1 Hz, but the 24" iMac runs at 60 Hz. Frequencies above 30 Hz cannot be presented at true temporal frequency on a 60 Hz display. Options:
  1. Present the nearest achievable frequency and document the actual achieved frequency
  2. Use a higher-refresh monitor (120 Hz or 240 Hz)
  3. Accept the limitation and note it in the manuscript

This must be discussed with Nicco before implementation. For now, implement with frame-accurate patterns and log actual achieved frequencies.

---

## Phase 2: Session Launcher

**Note on ordering**: The launcher GUI scaffold (2.1) is built *before* the individual tasks in Phase 1 so that every task can be exercised end-to-end through the real launcher flow from day one. The GUI is the only supported entry point for running a session — there is no hand-run task script in production.

### 2.1 — Build the session launcher GUI + runtime

**What**: Tkinter-based GUI launcher (`src/tasks/launcher_gui.py`) wired to a session runtime (`src/tasks/launcher.py`). The GUI is the single entry point; no session ever starts without it. See the **Session Launcher** section of `CLAUDE.md` for the full spec.

**Acceptance Criteria — entry & GUI layout**:
- [ ] Entry point: `uv run python -m tasks.launcher` opens the GUI with no CLI args required
- [ ] GUI is built with Tkinter (stdlib) — no new dependency
- [ ] GUI code lives in `src/tasks/launcher_gui.py`; session-runtime code lives in `src/tasks/launcher.py`. Runtime is importable and testable without a display.
- [ ] GUI fields: Participant ID (required, validated as `P\d{3}`), session date (auto-filled, editable), experimenter initials, notes (free text)
- [ ] Optional CLI flags pre-fill the GUI: `--participant-id P001`, `--demo`, `--skip-to N`. `--no-gui` runs headless (CI only).

**Acceptance Criteria — stream setup & checks panel**:
- [ ] **Create marker stream** button: calls `create_session_outlet(participant_id)` to create the `P013_Task_Markers` outlet with source ID `P013_{participant_id}`. After creation, the GUI displays stream name + source ID + a green "LIVE" indicator so RAs on the LabRecorder machine can locate it.
- [ ] **Sentiometer check** button: scans the network for the Sentiometer LSL stream and reports stream found (Y/N), sample rate, last sample timestamp, channel count. Turns green on success.
- [ ] **EEG check** button: scans for the BrainVision LSL stream, reports name + status.
- [ ] **CGX AIM-2 check** button: scans for the CGX LSL stream, reports name + status.
- [ ] **LabRecorder confirmation** checkbox: manual tick to confirm LabRecorder is recording all four streams. GUI prints the exact stream names next to the checkbox so RAs can cross-reference them.
- [ ] All stream checks re-runnable without restarting the GUI.

**Acceptance Criteria — session controls**:
- [ ] **Start Session** button disabled until: participant ID valid, marker stream created, Sentiometer check green, EEG check green, CGX check green, LabRecorder checkbox ticked
- [ ] On Start: GUI sends `session_start` marker, closes the setup window, and hands control to the session runtime
- [ ] Session runtime runs tasks 01–05 in order, passing the shared outlet to each task's `run()` function
- [ ] Between tasks: pauses with "Task X complete — Continue to Task Y?" prompt
- [ ] **Abort** available at any point: sends `session_end` marker, closes outlet, saves partial data, logs reason

**Acceptance Criteria — logging & recovery**:
- [ ] Session metadata written to `data/{participant_id}/session_log.json`: participant ID, experimenter, date, notes, task start/end times, abort reason (if any), list of streams detected at start
- [ ] `--skip-to N` starts from task N (crash recovery)
- [ ] `--demo` propagates to all tasks
- [ ] Graceful abort (Ctrl+C or Abort button) always sends `session_end` before exit

---

## Phase 3: Quality Assurance

### 3.1 — Unit tests

**Acceptance Criteria**:
- [ ] `test_markers.py`: verify each task's marker names match the spec in CLAUDE.md
- [ ] `test_task_configs.py`: verify each config.yaml loads and contains all required parameters
- [ ] `test_sentiometer.py`: existing device tests still pass
- [ ] All tests pass with `uv run pytest`

### 3.2 — Integration test (dry run)

**Acceptance Criteria**:
- [ ] Full session completes in `--demo` mode with no errors
- [ ] All expected LSL marker streams are created
- [ ] Behavioral logs are written for all tasks
- [ ] Session log JSON is valid and complete

### 3.3 — Timing validation

**Acceptance Criteria**:
- [ ] Run each task with a photodiode on the display to verify stimulus onset timing
- [ ] Compare PsychoPy flip timestamps against LSL marker timestamps — should be <2 ms discrepancy
- [ ] Document any systematic offsets (e.g., audio latency for oddball tones)

---

## Phase 4: Documentation & Polish

### 4.1 — Update README.md

**Acceptance Criteria**:
- [ ] Installation instructions (uv sync, extras)
- [ ] Quick-start guide for running a session
- [ ] Hardware requirements listed
- [ ] KDEF licensing note with setup instructions

### 4.2 — Stimulus preparation checklist

**Acceptance Criteria**:
- [ ] `scripts/generate_tones.py` runs and produces oddball .wav files
- [ ] `scripts/generate_mondrians.py` runs and produces mask images
- [ ] `scripts/validate_setup.py` checks: Python version, PsychoPy installed, LSL available, audio device present, display resolution correct
- [ ] KDEF download and placement instructions documented

---

## Execution Order for Claude Code

Tell Claude Code to execute these in order, one at a time:

```
1. Read CLAUDE.md (it does this automatically)
2. Phase 0.1: Reorganize repo structure
3. Phase 0.2: Place CLAUDE.md
4. Phase 0.3: Update pyproject.toml
5. Phase 0.4: Build common utilities + tests
6. Phase 2.1: Session Launcher GUI + runtime scaffold
   (moved before Phase 1 so every task is built against the
    real launcher flow from day one)
7. Phase 1.1: Task 01 — Auditory Oddball
8. Phase 1.2: Task 02 — RGB Illuminance
9. Phase 1.3: Task 03 — Backward Masking
10. Phase 1.4: Task 04 — Mind-State Switching
11. Phase 1.5: Task 05 — SSVEP Ramp-Down
12. Phase 3.1–3.2: Tests
13. Phase 4.1–4.2: Docs and scripts
```

Each phase should be a separate commit with a descriptive message.
