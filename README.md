# IACS Sentiometer Study

All software for **IACS Protocol P013** — *Validation of an Optical Consciousness Detection Instrument via Concurrent EEG/Polysomnography in Healthy Adults.* The repo holds (a) the serial-to-LSL bridge that streams the Sentiometer's 6-channel optical signal on its dedicated laptop and (b) a five-paradigm task suite plus terminal launcher that runs on the stimulus iMac during a session. The task suite drives the participant through an auditory oddball (P300), a passive RGB illuminance null-hypothesis decoding test, a QUEST-adaptive backward-masking face-detection threshold, a mind-state contrast (Geometry Dash analog vs. unguided meditation), and a Vayl-driven SSVEP frequency ramp — all with LSL marker emission that aligns cleanly with the Sentiometer, EEG, and CGX AIM-2 streams in a single XDF per session via LabRecorder.

For the full study design, paradigm specifications, marker reference, and architectural rationale, see [**`CLAUDE.md`**](CLAUDE.md). For the build plan and acceptance criteria, see [**`ACTION_PLAN.md`**](ACTION_PLAN.md).

---

## Installation

```bash
git clone https://github.com/Institute-for-Advanced-Consciousness/iacs-sentiometer-study.git
cd iacs-sentiometer-study
uv sync --extra tasks
```

`uv sync --extra tasks` installs the full task-layer extra (PsychoPy, Pygame, numpy, pandas, scipy) in addition to the device-layer core (pylsl, pyserial, click, rich, pyyaml). On Windows, PsychoPy's transitive dependency `dukpy` requires MS Visual C++ Build Tools 14+; install it from <https://visualstudio.microsoft.com/visual-cpp-build-tools/> before the sync if it fails.

The device layer alone (without the task extras) installs with a bare `uv sync` — useful for the dedicated Sentiometer laptop, which does not need PsychoPy or Pygame.

---

## Quick start

### Pre-session validation (run once per setup)

```bash
uv run python scripts/validate_setup.py
```

Checks Python version, required packages, display resolution, audio output, LSL outlet round-trip, tone / face / mask stimulus files, and Vayl app connectivity. Prints a Rich-formatted summary table and exits `0` if every required check is OK. Warnings about PsychoPy, Pygame, or Vayl are expected on non-stimulus machines and don't block.

### Demo session

```bash
uv run python -m tasks.launcher --demo --participant-id DEMO001
```

Runs the full launcher flow in demo mode: abbreviated trial counts per task (oddball 20 main trials, RGB 15 trials with 1 break, masking 5 practice + 20 main trials with fixed SOAs, mind-state 30 s game + 10 s break + 30 s meditation, SSVEP 10 s ramp). Pre-flight LSL/Vayl checks are skipped. Use `DEMO001` or any placeholder ID — data goes into `data/DEMO001/`.

### Real session

```bash
uv run python -m tasks.launcher --participant-id P001
```

Walks the RA through the full pre-session flow:

1. Rich table per task showing every configurable parameter with its current value.
2. `Edit any parameters? (y/N)` — opens `config/session_defaults.yaml` in `$EDITOR` / `notepad` and reloads after save.
3. Creates the shared `P013_Task_Markers` LSL outlet so LabRecorder can see it.
4. Pre-flight checklist: participant ID, marker outlet, EEG stream, Sentiometer stream (`IACS_Sentiometer`), Vayl app (localhost:9471). Missing optional streams warn but do not block.
5. Manual LabRecorder confirmation.
6. Runs Tasks 01 → 05 in order with per-task "Press Enter to continue" gates between blocks.

On `Ctrl+C` the launcher sends a `session_abort` marker, records the in-progress task in `data/{participant_id}/session_log.json`, and exits cleanly. Use `--skip-to N` to resume from task N after a crash.

---

## Hardware requirements

| Device | Role | Interface | Computer |
|---|---|---|---|
| Sentiometer | Optical consciousness signal | USB-serial → Python → LSL | Dedicated Sentiometer laptop (runs `src/sentiometer/`) |
| BrainVision 64-ch EEG | Gold-standard neural recording | BrainVision Recorder → LSL | EEG acquisition PC |
| CGX AIM-2 | EOG, chin EMG, HRV, respiration, GSR, SpO₂ | CGX software → LSL | CGX acquisition PC |
| 24" iMac | Stimulus display + task marker stream | PsychoPy/Pygame, `P013_Task_Markers` LSL | **Stimulus computer** (runs `src/tasks/`) |
| Vayl desktop app | GPU-driven SSVEP checkerboard overlay for Task 05 | Localhost HTTP API (port 9471) + `VaylStim` / `VaylStim_Freq` LSL streams | Stimulus computer (must be launched before Task 05) |
| Sony XBA-100 in-ear headphones | Audio delivery for oddball tones | 3.5 mm jack | Stimulus computer |
| Ozlo Sleepbuds | Sleep-phase audio (nap only) | Bluetooth | — |
| Mavogel eye mask | Light blocking during nap | — | — |
| LabRecorder | XDF recorder | Any machine on the same LSL network | Any |

All LSL streams are discoverable on the same local network; LabRecorder picks up `P013_Task_Markers`, `Sentiometer`, the BrainVision EEG stream, the CGX stream, and (during Task 05) `VaylStim` + `VaylStim_Freq` into one XDF per session.

---

## Pre-session checklist (run on the stimulus iMac before each participant)

1. **Validate setup**: `uv run python scripts/validate_setup.py` — all green required checks.
2. **Launch the Sentiometer** on the dedicated laptop (`uv run sentiometer run`) and confirm it's pushing samples to the network.
3. **Launch LabRecorder** and confirm it sees `Sentiometer`, the EEG stream, the CGX stream. You'll also tick the `P013_Task_Markers` stream once the launcher creates it.
4. **Launch the Vayl desktop app** on the stimulus iMac so Task 05 can drive it. If Vayl isn't installed yet, grab the installer from [Google Drive](https://drive.google.com/file/d/1M2d2mBBurxoeG5HmfJ9E4XABVvfsc3JX/view?usp=drive_link) or contact the Vayl team directly. Vayl must be running **before** launching the session. If you skip this, Tasks 01–04 still run, but Task 05 will fail in production.
5. **Start the launcher**: `uv run python -m tasks.launcher --participant-id P0XX` and walk through the pre-flight flow. The launcher prints a final "Is LabRecorder running and recording?" prompt before the first task.

---

## Marker reference (for data analysis)

Every timestamp below is captured with `pylsl.local_clock()` immediately after
the event's decisive side-effect — `win.flip()` return (PsychoPy, frame-flip
accurate to <1 refresh = ~16 ms at 60 Hz), `Sound.play()` return (pygame
backend, sub-ms), or the HTTP round-trip to Vayl (Task 05 only; prefer the
`wallTimeMs` payload for sub-ms GPU-flip precision). All markers flow
through the single **`P013_Task_Markers`** LSL stream so one LabRecorder
tick captures everything.

### Session-level

| Marker | When it fires | Notes |
|---|---|---|
| `session_start` | First marker, right after the outlet is created | 1 per session |
| `participant_id:{pid}` | Immediately after `session_start` | Carries the ID you typed into the GUI so the XDF alone tells you who this run is |
| `session_end` | After the last task's `_end` marker, before the outlet is released | 1 per completed session |
| `session_abort` | On Ctrl+C / Escape-confirmed abort | Present instead of (or in addition to) `session_end` |

### Task 01 — Auditory Oddball (P300)

**Trial structure:** tone onset → up to `response_window_ms` for a keypress → jittered ISI.

| Marker | When it fires | How to use |
|---|---|---|
| `task01_start` / `task01_end` | Task bracket | 1 each per run |
| `task01_instructions_start` / `_end` | Instruction screen shown / dismissed | `_end - _start` = RA read time |
| `task01_practice_start` / `_end` | Each practice attempt | Appears once per attempt |
| `task01_practice_attempt_N` | With the attempt's start | `N` counts from 1; resets only on a fresh run |
| `task01_practice_passed` | Emitted iff the participant cleared the 75% hit / ≤50% FA gate | Absence ⇒ RA aborted after a failed attempt |
| `task01_practice_tone_standard` / `_deviant` | At the `Sound.play()` call for each practice tone | Practice-phase tones only — never emit main-phase response markers |
| `task01_tone_standard` | At `Sound.play()` for a 1000 Hz standard tone in the main block | One per main-task standard |
| `task01_tone_deviant` | At `Sound.play()` for a 2000 Hz deviant tone | One per main-task deviant — **lock EEG/Sentiometer epochs to this** for P300 |
| `task01_response_hit` | Spacebar within the response window after a deviant | Use `response_hit_time − tone_deviant_time` = RT |
| `task01_response_miss` | Deviant received no response within the window | No subsequent response marker |
| `task01_response_false_alarm` | Spacebar during a standard tone's response window | Use `(response_time − tone_standard_time)` to quantify false-alarm RT |

**Primary endpoint:** epoch on `task01_tone_deviant` vs. `task01_tone_standard` and compare the Sentiometer difference at 250–350 ms post-tone to the EEG P300 at Pz/Cz.

### Task 02 — RGB Illuminance (null-hypothesis decoder)

**Trial structure:** one color flash of jittered duration → 200 ms gray ITI + fixation → next color.

| Marker | When it fires | How to use |
|---|---|---|
| `task02_start` / `task02_end` | Task bracket | |
| `task02_instructions_start` / `_end` | Instruction screen | |
| `task02_color_red` / `_green` / `_blue` | Frame-flip to the pure-color screen | **Anchor for SVM/decoding.** Use `(next_iti_time − color_time)` as the color-on-screen duration (1200–2000 ms in production, 500–800 ms in demo) |
| `task02_iti` | Frame-flip to the gray + fixation screen | `(next_color_time − iti_time)` ≈ 200 ms; the 200 ms gray screen prevents color-to-color retinal afterimages from contaminating the optical signal |
| `task02_break_start` / `_end` | 30 s rest every 100 trials (after trial 100, 200) | Use to exclude these windows from decoding |

**Primary endpoint:** train an SVM on Sentiometer features epoched at each `task02_color_*` vs. a 10,000-iteration permutation null. EEG is the positive control. **Prediction is null** — the Sentiometer should *not* decode color.

### Task 03 — Backward Masking (QUEST)

**Trial structure (main block):** 500 ms fixation → 1-frame face (or gray for catch) → (SOA − 17 ms) gray + fixation gap → 200 ms Mondrian mask → 1500 ms response prompt.

| Marker | When it fires | How to use |
|---|---|---|
| `task03_start` / `task03_end` | Task bracket | |
| `task03_instructions_start` / `_end` | Instruction screen | |
| `task03_practice_start` / `_end` | Practice block | 8 trials (6 face + 2 catch), familiarization only, no performance gate |
| `task03_practice_face_onset` / `_catch` / `_mask_onset` | Practice stimuli | No response or SOA markers are emitted during practice |
| `task03_fixation_onset` | Main-block fixation flip | Per-trial; marks the start of each main-task trial |
| `task03_face_onset` | Main-block 1-frame face flip | Face-present trials only. **Epoch Sentiometer / EEG on this marker** for threshold-locked VAN / P3b analysis |
| `task03_catch_trial` | Main-block mask-only (no face) trial | Catch trials do NOT emit `task03_face_onset`; use this to mark the distractor condition |
| `task03_mask_onset` | Main-block Mondrian flip | Every main-block trial gets one (face + catch). `mask_onset − face_onset` ≈ the current SOA |
| `task03_response_seen` / `_unseen` / `_unsure` | Keypress within 1500 ms | Maps to F / J / Space. `(response_time − mask_onset)` = RT |
| `task03_response_timeout` | No keypress within 1500 ms | |
| `task03_soa_value_XXX` | After every main-block face-present response | `XXX` is the zero-padded SOA in ms (e.g. `task03_soa_value_067`). One per face trial; catch + practice trials never emit this |

**Primary endpoint:** epoch on `task03_face_onset`, split by response label (seen vs. unseen) at SOAs near threshold, compare Sentiometer amplitude. EEG VAN (~200 ms) and P3b (~350 ms) are positive controls.

### Task 04 — Mind-State Switching (Gameplay vs. Meditation)

**Block structure:** 5 min gameplay → 1 min break → 5 min meditation.

| Marker | When it fires | How to use |
|---|---|---|
| `task04_start` / `task04_end` | Task bracket | |
| `task04_instructions_start` / `_end` | Block instructions | |
| `task04_game_start` / `task04_game_end` | Gameplay block boundaries | **Use the window between these for "active/engaged" Sentiometer + EEG epochs** |
| `task04_obstacle_appear` | A new obstacle is spawned | Useful for aligning anticipation / response |
| `task04_jump_start` | Spacebar pressed (input KEYDOWN) | |
| `task04_jump_end` | Spacebar released (input KEYUP) | `(jump_end − jump_start)` = jump-hold duration in ms; drives the jump-height curve |
| `task04_collision` | Player hit an obstacle | Followed immediately by player respawn at the spawn x |
| `task04_speed_increase` | Every `game_speed_increment_interval_s` (30 s by default) | Use to segment game difficulty windows |
| `task04_break_start` / `_end` | 1 min countdown between blocks | Exclude from analysis — transitional, neither engaged nor meditating |
| `task04_meditation_instructions_start` / `_end` | Meditation instructions | |
| `task04_meditation_gong_start` / `_end` | Simple_Gong.wav plays at the boundary | The only audio in Task 04 — gong onset is the clean "close your eyes now" landmark |
| `task04_meditation_start` / `_end` | Silent 5 min | **Use this window for "meditative" Sentiometer + EEG epochs** |

**Primary endpoint:** SVM classification (gameplay vs. meditation) from Sentiometer, vs. permutation null, after partialling out HRV and motion (from CGX). EEG band-power in alpha is a positive control.

### Task 05 — SSVEP Frequency Ramp

**Block structure:** instructions → white pre-ramp flash → Vayl stroboscope (300 s linear sweep 40 → 1 Hz effective) → black post-ramp → end.

| Marker | When it fires | How to use |
|---|---|---|
| `task05_start` / `task05_end` | Task bracket | |
| `task05_instructions_start` / `_end` | Instruction screen | |
| `task05_ramp_begin` | Right before `bridge.start_ramp()` returns | LSL-clock onset of the ramp (has ~1-5 ms HTTP latency vs. the actual GPU flip) |
| `task05_ramp_end` | Right after `bridge.wait_for_ramp()` returns | LSL-clock offset of the ramp |
| `task05_overlay_off` | After `bridge.turn_off()` returns the fade-out | Marks the end of Vayl's 500 ms fade |
| `{"event":"ramp_start",...}` | JSON payload pushed by the Vayl bridge right after the ramp starts | **Key fields:** `stimFreqHz` (effective start Hz), `stimFreqEndHz` (effective end Hz), `durationSeconds`, `wallTimeMs` (sub-ms GPU-flip onset in server epoch ms) |
| `{"event":"overlay_off",...}` | JSON payload after the overlay fades out | `wallTimeMs` = sub-ms offset |

**Reconstructing effective frequency at any sample time** (you don't need the separate `VaylStim_Freq` stream — the ramp is linear and fully specified by the `ramp_start` JSON):

```python
def freq_at(t_lsl, ramp_start_lsl, stim_start_hz=40, stim_end_hz=1, duration_s=300):
    if t_lsl < ramp_start_lsl or t_lsl > ramp_start_lsl + duration_s:
        return 0.0
    progress = (t_lsl - ramp_start_lsl) / duration_s
    return stim_start_hz + progress * (stim_end_hz - stim_start_hz)
```

For sub-ms alignment with EEG, use the `wallTimeMs` field from the `ramp_start` JSON as the ramp's onset, not the LSL timestamp.

**Primary endpoint:** *stability test* — no significant frequency-dependent modulation of the Sentiometer signal across the ramp. EEG entrainment at the commanded effective Hz is the positive control.

### Verifying + timeline-ing a recorded XDF

```bash
# Full marker coverage check + chronological timeline (Pacific time, AM/PM)
uv run python scripts/verify_xdf.py              # defaults to the single .xdf in sampledata/
uv run python scripts/verify_xdf.py path/to/session.xdf

# Just the timeline
uv run python scripts/timeline_xdf.py

# Quieter: phase summary only, no per-marker rows
uv run python scripts/verify_xdf.py --timeline-summary-only
```

`verify_xdf.py` emits a Rich table per task with OK/WARN/FAIL per marker, a suggestions panel for any gaps, and a chronological listing with PT wall clock + elapsed seconds + phase coloring. Exit code is 0 if every hard check passes, 1 otherwise (useful for CI / scripts).

---

## Running tests

```bash
# Full suite (85 tests, ~13 s)
uv run pytest

# Per-task
uv run pytest tests/test_task01_oddball.py -v

# Lint
uv run ruff check src/ tests/
```

The task-layer tests all use dependency-injected `TaskIO` mocks and do not require PsychoPy, Pygame, Vayl, or real stimuli on disk — they exercise every task's marker emission, CSV logging, and state transitions through real LSL roundtrips with mock rendering.

---

## Citations

### KDEF face stimuli (Task 03)

- Lundqvist, D., Flykt, A., & Öhman, A. (1998). *The Karolinska Directed Emotional Faces — KDEF.* Department of Clinical Neuroscience, Psychology Section, Karolinska Institutet. ISBN 91-630-7164-9.
- Dawel, A., Wright, L., Irons, J., Dumbleton, R., Palermo, R., O'Kearney, R., & McKone, E. (2017). *Perceived emotion genuineness: Normative ratings for popular facial expression stimuli and the development of perceived-as-genuine and perceived-as-fake sets.* Behavior Research Methods, 49(4), 1539–1562. (Introduces the **KDEF-cropped** set used in this study.)

The 28 neutral KDEF-cropped faces are committed to this (private) repo in `src/tasks/03_backward_masking/stimuli/faces/`. Redistribution of the KDEF images is subject to the upstream license; this repo will remain private until publication.

### ERP CORE (Task 01)

- Kappenman, E. S., Farrens, J. L., Zhang, W., Stewart, A. X., & Luck, S. J. (2021). *ERP CORE: An open resource for human event-related potential research.* NeuroImage, 225, 117465. (Defines the standardized auditory oddball timing — 100 ms tones with 10 ms rise/fall, 1100–1500 ms ISI — that Task 01 follows.)

---

## Device layer (Sentiometer serial → LSL bridge)

If you only need the device layer (on the dedicated Sentiometer laptop, no task suite), install without the tasks extra:

```bash
git clone https://github.com/Institute-for-Advanced-Consciousness/iacs-sentiometer-study.git
cd iacs-sentiometer-study
uv sync

# Copy the default config and set the correct COM port
cp config/sentiometer.yaml config/local.yaml
# Edit config/local.yaml

# Guided wizard (recommended for RAs)
uv run sentiometer run

# Direct stream (no wizard)
uv run sentiometer stream

# List serial ports
uv run sentiometer ports
```

The LSL stream exposed by the device layer is named `Sentiometer`, type `Misc`, 6 channels (`device_ts`, `PD1`–`PD5`), 500 Hz nominal. See `src/sentiometer/` for the source.

---

## Contact

**Nicco Reggente, Ph.D.** — PI / Lab Director, IACS
Questions, bug reports, and PRs should go to the lab, not Claude.
