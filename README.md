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
4. **Launch the Vayl desktop app** on the stimulus iMac so Task 05 can drive it. If you skip this, Tasks 01–04 still run, but Task 05 will fail in production.
5. **Start the launcher**: `uv run python -m tasks.launcher --participant-id P0XX` and walk through the pre-flight flow. The launcher prints a final "Is LabRecorder running and recording?" prompt before the first task.

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
