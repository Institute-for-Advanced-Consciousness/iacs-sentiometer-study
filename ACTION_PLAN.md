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
- [ ] All configurable task parameters live in a single master file: `config/session_defaults.yaml`. There are no per-task config files.
- [ ] `load_session_config(config_path=".../session_defaults.yaml") -> dict` loads the master config from disk. Relative paths resolve against the repo root. Missing file raises a clear error.
- [ ] `get_task_config(session_config, task_name, overrides=None) -> dict` returns a **copy** of one task's section so caller mutations (e.g. demo overrides) don't leak into the master dict. Unknown `task_name` raises `KeyError` listing available sections.
- [ ] `overrides` dict merges on top of the task section; `None` values in the overrides dict are skipped (so unprovided CLI flags don't clobber defaults).
- [ ] `session_defaults.yaml` ships with the repo; its default values match the IRB protocol exactly and contain a section for every task (`task01_oddball` … `task05_ssvep`) plus a top-level `session:` block.

---

## Phase 1: Task Paradigms (Build in Order)

### 1.1 — Task 01: Auditory Oddball (P300)

**What**: Standard two-tone oddball with active button press, preceded by a self-paced practice gate. ~250 main trials, ~5–8 min depending on practice attempts. Stimulus and timing parameters match the **ERP CORE** standardized auditory oddball protocol (Kappenman et al., 2021, *NeuroImage*).

**Acceptance Criteria — wiring**:
- [ ] `run(outlet, config, ...)` accepts the shared session marker outlet; does NOT create or destroy any session-level LSL streams. If `outlet` is `None` (standalone testing) the task creates a temporary demo outlet via `create_demo_outlet()` and tears it down at the end.
- [ ] Task reads its config from `config/session_defaults.yaml` via `get_task_config(session_cfg, "task01_oddball")` — no per-task config file.
- [ ] Side-effecting I/O (display, audio, keyboard, sleep) is bundled into a `TaskIO` dataclass so the task can be driven headlessly by tests; PsychoPy is imported lazily inside the production `_build_psychopy_io` builder.

**Acceptance Criteria — stimuli & timing**:
- [ ] Pre-generated .wav files for 1000 Hz and 2000 Hz tones (100 ms total, 10 ms raised-cosine rise/fall, 44.1 kHz, 16-bit) in `assets/sounds/`
- [ ] `scripts/generate_tones.py` produces these files reproducibly
- [ ] Trial sequence: pseudorandom via stratified placement honoring `max_consecutive_standards` (warns and relaxes to the minimum feasible value if the requested constraint is mathematically impossible for the configured ratio)
- [ ] ISI jittered uniformly 1100–1500 ms
- [ ] Marker timestamp stamped with `local_clock()` immediately after `Sound.play()` (as close to onset as PsychoPy + audio backend allow)

**Acceptance Criteria — practice gate**:
- [ ] Before the main task, a 10-trial practice block runs (8 standard + 2 deviant by default; configurable via `practice_trials` / `practice_deviants`)
- [ ] After each practice block, hit rate and false-alarm rate are computed and shown to the participant ("You detected X of Y target tones (Z%). You need at least 75% to continue.")
- [ ] Pass criteria: `hit_rate >= practice_hit_threshold` (default 0.75) **and** `fa_rate <= practice_fa_ceiling` (default 0.50). On pass, show "Great job!" feedback and start main task on next spacebar.
- [ ] Fail: show retry message, repeat the practice block. **No cap on practice attempts.** Each attempt is logged.
- [ ] In `demo=True` mode, the practice gate always passes after attempt 1 regardless of accuracy.
- [ ] Escape key triggers graceful exit via the common `check_escape` handler (raises `EscapePressedError`)

**Acceptance Criteria — LSL markers** (all prefixed `task01_`):
- [ ] Session boundaries: `task01_start`, `task01_end`
- [ ] Instructions: `task01_instructions_start`, `task01_instructions_end`
- [ ] Practice: `task01_practice_start`, `task01_practice_end`, `task01_practice_attempt_N` (one per attempt), `task01_practice_passed`, `task01_practice_tone_standard`, `task01_practice_tone_deviant`
- [ ] Main stimulus: `task01_tone_standard`, `task01_tone_deviant`
- [ ] Main response: `task01_response_hit` (button press within response window after deviant), `task01_response_false_alarm` (button press after standard), `task01_response_miss` (no press after deviant)
- [ ] Practice-phase responses are tracked in the CSV but **not** emitted as response markers (clean separation between practice and main-task response events)
- [ ] Correct rejections (no press on standard) emit no marker — they are the silent default

**Acceptance Criteria — demo & logging**:
- [ ] `demo=True`: 1 practice block (always passes) + 20 main trials. Creates its own temporary outlet if no outlet is passed.
- [ ] Behavioral log saved to `data/{participant_id}/task01_oddball_*.csv` with columns: `trial_number, phase, practice_attempt, tone_type, tone_onset_time, response_time, response_type, rt_ms`
- [ ] Task exits cleanly via `try/finally` (window closed, outlet cleaned up if owned) and returns the log path

**Acceptance Criteria — tests**:
- [ ] `tests/test_task01_oddball.py` loads the task via `importlib.import_module("tasks.01_oddball.task")` (the directory name starts with a digit and isn't a valid identifier for `import` statements)
- [ ] A simulated end-to-end run with a mock `TaskIO` exercises practice failure → retry → pass → main, captures markers via a real LSL inlet, and asserts every marker type from the spec appears
- [ ] Pure helpers (`build_trial_sequence`, `compute_practice_metrics`) have unit tests including the infeasible-constraint case

### 1.2 — Task 02: RGB Illuminance Test

**What**: 300 full-screen color trials (100 R / 100 G / 100 B), passive fixation. ~10 min.

**Acceptance Criteria**:
- [ ] Task accepts `outlet: StreamOutlet` as a required parameter; does NOT create or destroy any LSL streams
- [ ] Task reads its config section from `config/session_defaults.yaml` via `get_task_config(session_cfg, "task02_rgb_illuminance")`; the shipped section contains `trials_per_color`, `trial_duration_min_s`, `trial_duration_max_s`, and `colors`. The no-consecutive-same-color constraint is hardcoded because it defines the paradigm.
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
- [ ] Task reads its config section from `config/session_defaults.yaml` via `get_task_config(session_cfg, "task03_backward_masking")`; the shipped section contains `total_trials`, `catch_trial_proportion`, `mask_duration_ms`, `fixation_duration_ms`, `response_window_ms`, and all staircase parameters (`staircase_start_soa_ms`, `staircase_step_down_ms`, `staircase_step_up_ms`, `target_threshold`). The 1-frame target duration is hardcoded because it's dictated by the display refresh rate.
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
- [ ] Task reads its config section from `config/session_defaults.yaml` via `get_task_config(session_cfg, "task04_mind_state")`; the shipped section contains `game_duration_s`, `break_duration_s`, `meditation_duration_s`, `game_start_speed`, and `game_max_speed`. Block order (game → break → meditation), game mechanics, and meditation instruction text are hardcoded because they define the paradigm.
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
- [ ] Task reads its config section from `config/session_defaults.yaml` via `get_task_config(session_cfg, "task05_ssvep")`; the shipped section contains `freq_start_hz`, `freq_end_hz`, `freq_step_hz`, and `step_duration_s`. Total step count and total duration are derived at runtime.
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

**Acceptance Criteria — session config panel**:
- [ ] Launcher loads `config/session_defaults.yaml` at startup via `load_session_config()` and displays a summary table of all task parameters grouped by task, with the current values.
- [ ] An **Edit** affordance (inline table or modal) lets the RA modify any value before the session begins. Edited values are typed-checked against the defaults (int/float/bool/list) and used for *this session only*; the on-disk defaults file is never overwritten.
- [ ] Applying `--demo` overrides short-form values (e.g. `total_trials = 20` for oddball, `step_duration_s = 1.0` for SSVEP) on top of the loaded config without mutating the master dict.
- [ ] The per-task dict is passed into each task's `run()` function so tasks never reload the YAML themselves.

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
- [ ] `test_task_configs.py`: verify `config/session_defaults.yaml` loads, that every task section is present, and that overrides merge without mutating the master dict
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
