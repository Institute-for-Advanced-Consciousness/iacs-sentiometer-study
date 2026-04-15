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

**What**: 300 full-screen color trials (100 R / 100 G / 100 B), passive fixation, gray ITI between colors, two 30 s rest breaks splitting the run into three blocks. ~10 min total.

**Acceptance Criteria — wiring**:
- [ ] `run(outlet, config, ...)` accepts the shared session marker outlet; does NOT create or destroy any session-level LSL streams. If `outlet` is `None`, the task creates a temporary demo outlet via `create_demo_outlet()` and tears it down at the end.
- [ ] Task reads its config from `config/session_defaults.yaml` via `get_task_config(session_cfg, "task02_rgb_illuminance")`. The shipped section contains `trials_per_color`, `trial_duration_min_s`, `trial_duration_max_s`, `colors`, `iti_duration_ms`, `break_interval_trials`, and `break_duration_s`.
- [ ] Side-effecting I/O bundled into a `TaskIO` dataclass; PsychoPy imported lazily in `_build_psychopy_io`.

**Acceptance Criteria — stimuli & timing**:
- [ ] Full-screen pure colors: red `(255,0,0)`, green `(0,255,0)`, blue `(0,0,255)` rendered with `colorSpace='rgb255'` and full-screen rects in normalized coordinates.
- [ ] Central white fixation cross rendered on every color frame *and* every ITI frame.
- [ ] Trial duration jittered uniformly in `[trial_duration_min_s, trial_duration_max_s]` (defaults 1.2–2.0 s, mean ~1.6 s).
- [ ] After each color, a 200 ms medium-gray `(128,128,128)` screen with the fixation cross is shown — **not** black/blank — to suppress contrast flashes and afterimages.
- [ ] Trial sequence: pseudorandom via the `build_color_sequence` "max-remaining" greedy algorithm; no two consecutive same colors. Verified by tests across multiple seeds.

**Acceptance Criteria — breaks**:
- [ ] After every `break_interval_trials` trials (default 100), a break fires unless the trial is the last one. With 300 trials this gives breaks after trial 100 and trial 200 only.
- [ ] Break screen: gray background + fixation cross + "Take a moment to rest your eyes. The task will continue in N seconds." with a per-second countdown.
- [ ] Break is 30 s by default; auto-resumes (no keypress required).
- [ ] Demo mode uses an explicit `break_after_trials = [5]` override so exactly one break fires after trial 5 regardless of the modular interval.

**Acceptance Criteria — LSL markers** (all prefixed `task02_`, 10 distinct types):
- [ ] Session boundaries: `task02_start`, `task02_end`
- [ ] Instructions: `task02_instructions_start`, `task02_instructions_end`
- [ ] Stimulus per trial: `task02_color_red` / `task02_color_green` / `task02_color_blue` at the color flip, `task02_iti` at the gray ITI flip
- [ ] Breaks: `task02_break_start`, `task02_break_end`

**Acceptance Criteria — demo & logging**:
- [ ] `demo=True`: 5 trials per color (15 total), 1 break after trial 5, faster trial durations (0.5–0.8 s) and a 3 s break so the run completes in well under 30 s. Creates its own outlet if none is passed.
- [ ] Behavioral log saved to `data/{participant_id}/task02_rgb_illuminance_*.csv` with columns: `trial_number, color, color_rgb, onset_time, offset_time, duration_s, iti_onset_time`.
- [ ] `color_rgb` is logged as the literal `"r,g,b"` integer triple actually rendered; analysis code can audit pure-RGB compliance against this column.
- [ ] Display gamma / panel fidelity confirmation against the logged RGB values is deferred to Phase 3.3 (colorimeter).

**Acceptance Criteria — tests**:
- [ ] `tests/test_task02_rgb_illuminance.py` loads the task via `importlib.import_module("tasks.02_rgb_illuminance.task")`.
- [ ] `build_color_sequence` is unit-tested across multiple seeds for: per-color counts, length, no-consecutive constraint at full size and at demo size, deterministic seeding.
- [ ] `is_break_trial` is unit-tested for the explicit-list path, the modular-interval path (excluding the last trial), and the zero-interval no-op path.
- [ ] An end-to-end simulated run with a mock `TaskIO` exercises 9 trials with two explicit breaks, captures markers via a real LSL inlet, and asserts every marker type from the spec appears with the correct counts and ordering, that the recorded color sequence has no consecutive repeats, and that every CSV row's `color_rgb` is one of the three pure values.
- [ ] A demo-mode test verifies exactly one break fires (after trial 5) with 5 colors before and 10 after.

### 1.3 — Task 03: Backward Masking (Face Detection)

**What**: QUEST-driven adaptive staircase backward masking with all 28 KDEF-cropped neutral faces and 100 pre-generated Mondrian masks. 275 main trials preceded by an 8-trial familiarization block (no accuracy gate). ~10 min.

**Acceptance Criteria — wiring**:
- [ ] `run(outlet, config, ...)` accepts the shared session marker outlet; does NOT create or destroy any session-level LSL streams. Demo mode creates a temporary outlet if none is passed.
- [ ] Task reads its config from `config/session_defaults.yaml` via `get_task_config(session_cfg, "task03_backward_masking")`.
- [ ] Side-effecting I/O bundled into a `TaskIO` dataclass; PsychoPy imported lazily in `_build_psychopy_io`.
- [ ] Staircase is behind a `Staircase` protocol so tests inject `FixedSoaStaircase` and demo mode uses a fixed `[150, 100, 80, 60, 40]` ms staircase instead of QUEST.

**Acceptance Criteria — stimuli (committed to repo)**:
- [ ] `src/tasks/03_backward_masking/stimuli/faces/` contains all 28 KDEF-cropped neutral faces (20 female, 8 male). Filenames contain the KDEF expression code `NE`.
- [ ] `src/tasks/03_backward_masking/stimuli/masks/` contains 100 procedurally generated Mondrian masks (256×256 PNG).
- [ ] `scripts/generate_mondrians.py` produces the masks deterministically (seeded per-mask index) so regeneration is byte-identical.
- [ ] `src/tasks/03_backward_masking/stimuli/README.md` documents the KDEF-cropped source, citation (Dawel et al. 2017; Lundqvist, Flykt & Öhman 1998), licensing status, and that the repo is private until publication.
- [ ] `.gitignore` does NOT exclude `src/tasks/03_backward_masking/stimuli/`.
- [ ] At startup, `scan_face_directory` filters to `*NE*.png`, logs the count, and raises `RuntimeError` with a clear message if fewer than `min_face_identities` (default 10) are found.
- [ ] Each face is resized to `face_size_px × face_size_px` (default 256) at load time for display consistency.

**Acceptance Criteria — QUEST staircase**:
- [ ] Uses `psychopy.data.QuestHandler` with `pThreshold=0.50`, configurable `beta`, `delta`, `gamma`, `grain`.
- [ ] Starting SOA from `soa_start_ms` (default 100); SOA clamped to `[soa_min_ms, soa_max_ms]` (defaults 17, 500).
- [ ] Updates only on **main-task face-present trials**. Practice trials and catch trials never update the staircase.
- [ ] "Seen" response = correct (pass 1 to QUEST); "Not Seen" / "Unsure" / timeout = incorrect (pass 0).
- [ ] Current threshold estimate is logged per main face-present trial in the behavioral CSV.

**Acceptance Criteria — trial structure & timing**:
- [ ] Fixation (500 ms) → Face for 1 frame (~17 ms, hardcoded from display refresh) → Gray + fixation gap of `max(0, SOA − 17)` ms → Mondrian mask (200 ms) → Response prompt (up to `response_window_ms`, default 1500).
- [ ] On catch trials the 1-frame face slot shows gray + fixation with no face image drawn; timing is identical.
- [ ] Catch proportion: ~17% of total trials (`round(total_trials * catch_trial_proportion)`), shuffled into the trial sequence.
- [ ] Face scheduling via `build_face_schedule` cycles through shuffled copies of the face ID list so all 27 identities are used roughly equally (228 face trials / 27 faces ≈ 8.4 each).

**Acceptance Criteria — practice (familiarization only)**:
- [ ] 8-trial practice block: `practice_face_trials` face-present trials at `practice_soa_ms` (default 200 ms, clearly visible) + `practice_catch_trials` catch trials, shuffled.
- [ ] No accuracy gate; practice always ends after one block.
- [ ] Practice trials emit `task03_practice_*` markers instead of the main-task stimulus / response markers.
- [ ] After practice, the "Practice complete. In the real task, the faces will sometimes be very brief..." screen is shown; participant presses spacebar to start main.

**Acceptance Criteria — response keys**:
- [ ] Three-alternative response: `F` = Seen, `J` = Not Seen, `Spacebar` = Unsure. All three keys are configurable via `response_key_seen` / `response_key_unseen` / `response_key_unsure`.
- [ ] Response prompt screen displays the exact key legend: `F = Yes    |    Spacebar = Unsure    |    J = No`.
- [ ] If no response within `response_window_ms`, the trial is logged as `timeout` and emits `task03_response_timeout`.

**Acceptance Criteria — LSL markers** (all prefixed `task03_`):
- [ ] Session boundaries: `task03_start`, `task03_end`.
- [ ] Instructions: `task03_instructions_start`, `task03_instructions_end`.
- [ ] Practice: `task03_practice_start`, `task03_practice_end`, `task03_practice_face_onset`, `task03_practice_catch`, `task03_practice_mask_onset`.
- [ ] Main stimulus per trial: `task03_fixation_onset`, `task03_face_onset` (face-present) or `task03_catch_trial` (mask-only), `task03_mask_onset`.
- [ ] Main response: `task03_response_seen`, `task03_response_unseen`, `task03_response_unsure`, `task03_response_timeout`.
- [ ] Staircase tracking: `task03_soa_value_XXX` (3-digit zero-padded ms, e.g. `task03_soa_value_067`) — one per main face-present trial after the response is classified.

**Acceptance Criteria — demo & logging**:
- [ ] `demo=True`: 5 practice + 20 main trials (17 face + 3 catch), fixed SOAs cycling through `[150, 100, 80, 60, 40]` ms (no QUEST), placeholder face stimuli so the task runs without KDEF files on disk. Completes in under 1 minute.
- [ ] Behavioral log saved to `data/{participant_id}/task03_backward_masking_*.csv` with columns: `trial_number, phase, trial_type, face_id, mask_id, soa_ms, response, rt_ms, quest_threshold_estimate`.
- [ ] Catch rows log `face_id = "none"`.
- [ ] Practice rows log an empty `quest_threshold_estimate`; main face-present rows log the QUEST (or FixedSoa) mean.

**Acceptance Criteria — tests**:
- [ ] `tests/test_task03_backward_masking.py` loads the task via `importlib.import_module("tasks.03_backward_masking.task")`.
- [ ] Pure helpers have unit tests: `scan_face_directory` (NE filter, min-identities error, missing-dir error), `build_trial_types` (catch proportion at full and demo sizes), `build_face_schedule` (cycles through all faces), `FixedSoaStaircase` (cycling, threshold-as-mean).
- [ ] An end-to-end simulated run creates temp face and mask directories, uses a `MockTaskIO` and `FixedSoaStaircase`, exercises every response category (seen/unseen/unsure/timeout), captures markers via a real LSL inlet, and asserts every marker type from the spec appears with correct counts (17 SOA markers for 17 main face trials, 3 catch markers, etc.), that catch trials never update the staircase, and that the CSV schema + catch `face_id="none"` invariant hold.
- [ ] A response-key-mapping test verifies the configured `response_key_*` values propagate into the `key_map` passed to `wait_for_response` (not hardcoded F/J/Space).

### 1.4 — Task 04: Mind-State Switching

**What**: 5-min gameplay → 1-min break → 5-min meditation. ~11 min total. Three blocks share one Pygame window and one mixer (no PsychoPy / Pygame handoff mid-task).

**Acceptance Criteria — architecture & wiring**:
- [ ] `run(outlet, config, ...)` orchestrates three sequential blocks via helper functions in `src/tasks/04_mind_state/game.py` and `src/tasks/04_mind_state/meditation.py`. The task directory starts with a digit, so sibling-module imports inside `task.py` go through `importlib.import_module("tasks.04_mind_state.game")` / `... .meditation"` rather than the `from .` syntax.
- [ ] Task reads config from `config/session_defaults.yaml` via `get_task_config(session_cfg, "task04_mind_state")`. Shipped keys: `game_duration_s`, `break_duration_s`, `meditation_duration_s`, `game_start_speed`, `game_max_speed`, `game_speed_increment_interval_s`, `jump_min_height`, `jump_max_height`, `jump_hold_max_ms`, `obstacle_types`, `gong_file`.
- [ ] Side-effecting I/O bundled into a `TaskIO` dataclass; Pygame imported lazily inside `_build_pygame_io`. PsychoPy is **not** imported anywhere in this task.
- [ ] `game.update_game_state` is a pure function (state, dt_s, input_state, config, rng → events list) so the frame loop can be driven by a mock TaskIO in tests without Pygame.

**Acceptance Criteria — Game (Block 1)**:
- [ ] Side-scrolling rhythm-runner (Geometry Dash analog) rendered via Pygame.
- [ ] Controls: spacebar only. Tap = short jump; hold = higher jump (extra upward acceleration while held, capped at `jump_hold_max_ms`).
- [ ] Visuals: colored player square, parallax-scrolling background (far + near layers), flat ground with vertical grid lines, three obstacle types (`spike`, `tall_rect`, `low_barrier`) in distinct contrasting colors, particle burst + white screen-flash on collision.
- [ ] **No audio at all during the game block.** `pygame.mixer` is not used in the game loop — the gong module only plays during meditation. This is critical for the EEG gameplay-vs-meditation comparison.
- [ ] Difficulty ramp: linear speed interpolation from `game_start_speed` to `game_max_speed` over `game_duration_s`, sampled at `game_speed_increment_interval_s` boundaries (one `task04_speed_increase` marker per step). Obstacle spawn interval shrinks linearly from 2.0 s to 0.8 s as speed ramps up.
- [ ] Collision: remove colliding obstacle, respawn player at spawn x, brief flash grace window, instant resume. Score is tracked and displayed in the HUD.
- [ ] Game ends at exactly `game_duration_s` regardless of player state.

**Acceptance Criteria — Break (Transition)**:
- [ ] Gray Pygame screen with per-second countdown: "Take a moment to relax and stretch. The meditation will begin in N seconds."
- [ ] Duration = `break_duration_s`. Participant does not need to press anything; auto-resumes.
- [ ] LSL markers: `task04_break_start`, `task04_break_end`.

**Acceptance Criteria — Meditation (Block 2)**:
- [ ] Instruction screen text (anapanasati + body scan) shown in the same Pygame window.
- [ ] After spacebar press: black screen → play `assets/sounds/Simple_Gong.wav` (via `pygame.mixer`) → silent `meditation_duration_s` timer → play gong again → completion screen → wait for spacebar.
- [ ] LSL markers: `task04_meditation_instructions_start`, `task04_meditation_instructions_end`, `task04_meditation_gong_start`, `task04_meditation_start`, `task04_meditation_gong_end`, `task04_meditation_end`.

**Acceptance Criteria — LSL markers** (all prefixed `task04_`, **19 distinct types**):
- [ ] Session boundaries: `task04_start`, `task04_end`.
- [ ] Overall instructions: `task04_instructions_start`, `task04_instructions_end`.
- [ ] Game boundaries: `task04_game_start`, `task04_game_end`.
- [ ] In-game events: `task04_obstacle_appear`, `task04_jump_start`, `task04_jump_end`, `task04_collision`, `task04_speed_increase`.
- [ ] Break: `task04_break_start`, `task04_break_end`.
- [ ] Meditation: `task04_meditation_instructions_start`, `task04_meditation_instructions_end`, `task04_meditation_gong_start`, `task04_meditation_start`, `task04_meditation_gong_end`, `task04_meditation_end`.

**Acceptance Criteria — demo & logging**:
- [ ] `demo=True`: 30 s game, 10 s break, 30 s meditation (gong still plays). Completes in ~70 s total. Creates its own temporary outlet if none is passed.
- [ ] Behavioral log saved to `data/{participant_id}/task04_mind_state_*.csv` with 4 columns: `timestamp, phase, event_type, details`. Game events include `score=`, `speed_level=`, `obstacle_type=`, `jump_height_ms=` in the details column; break/meditation rows carry their block-specific metadata.

**Acceptance Criteria — tests**:
- [ ] `tests/test_task04_mind_state.py` loads all three modules via `importlib.import_module("tasks.04_mind_state.{task,game,meditation}")`.
- [ ] Unit tests on `game.update_game_state`: init state shape, jump_start on space press (while grounded), jump_end on release mid-air, speed_increase at step boundaries, obstacle_appear after `initial_spawn_delay_s`, collision detected and player respawned (inject an obstacle at the player's x).
- [ ] `run_game_block` timer-accuracy test: with `duration_s=1.0` and mock tick returning 1/60 s, the loop exits at ~60 ticks (±2 for FP rounding).
- [ ] `run_meditation_block` standalone test: all six meditation markers emitted in order, two gong calls, one black-screen call, `wait(duration_s)` invoked once.
- [ ] Full-orchestrator end-to-end test: scripted input script presses space at frame 30-40 (jump before any obstacle arrives) then goes idle, letting the first spawned obstacle collide with the stationary player. Captures markers via a real LSL inlet and asserts all **19** marker types appear with correct ordering (`task04_start` first, `task04_end` last, instructions before game, game before break, break before meditation). Verifies the CSV schema has the 4 columns and that all three phase labels (`game`, `break`, `meditation`) appear in the rows.

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
