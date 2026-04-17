# PSG Analysis Guide

**Purpose** — hand off a recorded P013 session to Dr. Ken Paller (or any
sleep-scoring collaborator) as an EDF+ file with AASM-relevant channels
cleanly labelled. IACS records natively in XDF (Lab Streaming Layer's
multi-stream container) because the session fuses BrainVision EEG, a CGX
AIM-2 physiological monitor, the Sentiometer optical device, and task
markers into a single synchronised timeline. EDF+ is the interchange
format sleep scorers prefer, so we export a focused subset.

---

## What's in a session XDF

A typical multi-stream P013 recording contains up to five LSL streams:

| LSL stream name | Type | Rate | Channels | Use |
|---|---|---|---|---|
| `BrainAmpSeries-Dev_1` | EEG | 500 Hz | 64 | High-density EEG (BrainVision BrainAmp, 10-10 layout) |
| `CGX AIM Phys. Mon. AIM-NNNN` | EEG | 500 Hz | 13 | Peripheral physiology: 4 AUX (`ExGa 1–4`) + ECG / Resp / PPG / SpO₂ / HR / GSR / Temp / packet-counter / trigger |
| `CGX AIM Phys. Mon. AIM-NNNN Impedance` | Impedance | 500 Hz | 13 | Real-time channel impedance in kΩ (dropped in the EDF) |
| `Sentiometer` | Misc | 500 Hz | 6 | Study instrument under test (optical `PD1–PD5` + `device_ts`). Not used for sleep scoring |
| `P013_Task_Markers` | Markers | 0 (irregular) | 1 | Task event markers. Empty during pure sleep recordings |

LSL timestamps across streams share the same `pylsl.local_clock()`, so
all channels are aligned to <1 ms when written to EDF.

## How the CGX `ExGa 1–4` channels are wired for P013 PSG

The CGX AIM-2's four auxiliary inputs are user-configurable. **In the
P013 sleep montage** they carry:

| CGX input | P013 electrode | AASM role |
|---|---|---|
| `ExGa 1` | `EOG-L` (left outer canthus) | Left EOG |
| `ExGa 2` | `EOG-R` (right outer canthus) | Right EOG |
| `ExGa 3` | `EMG-Chin1` (submental #1) | Chin EMG (bipolar with Chin2) |
| `ExGa 4` | `EMG-Chin2` (submental #2) | Chin EMG (bipolar with Chin1) |

If the wiring ever changes, update `config/psg_mapping.yaml` before
running the converter (the `label` field for each `ExGa N` entry).

## Producing the EDF+ bundle (full Paller handoff)

There are two ways to produce an EDF+ from a session XDF:

### Option A — full Paller handoff pipeline (recommended)

One command generates the EDF + PDF manifest + README + diagnostics:

```bash
# 1. Drop the session XDF into sampledata/ (any filename; newest wins).
#    BIDS-style "sub-<id>_..." filenames auto-extract the subject ID;
#    anything else falls back to the first underscore-delimited token.
cp <path-to.xdf> sampledata/

# 2. One-shot runner — executes all four steps in order:
uv sync --extra dev
uv run python scripts/xdf_to_edf/run_all.py
```

The pipeline writes everything to `outputs/<SUBJECT>/`:

```
outputs/<SUBJECT>/
├── P013_<SUBJECT>_for_paller.edf          # 75-channel EDF+ at 500 Hz
├── P013_<SUBJECT>_channel_manifest.pdf    # numbered channel table + scoring notes
├── P013_<SUBJECT>_README.txt              # quickstart for the recipient
├── P013_<SUBJECT>_conversion_log.txt      # every decision the converter made
└── diagnostics/
    ├── eeg_first_120s_rms.png              # peripheral-saturation heatmap
    ├── eeg_time_course_first_2min.png      # Fp1 / Cz / Oz / TP9 raw traces
    ├── psd_by_modality.png                 # Welch PSD on a clean 60s window at t=300
    ├── psd_data.csv                        # same PSDs as CSV
    └── line_noise_report.txt               # 60/120/180 Hz SNR + broadband ratios + impedance
```

Run the individual steps if you want to review in isolation (each
script auto-detects the newest XDF and writes to the same bundle):

| Step | Command | What it does |
|---|---|---|
| 1 | `uv run python scripts/xdf_to_edf/01_inspect.py` | Read-only inventory + forensic on the first 120 s of EEG |
| 1b | `uv run python scripts/xdf_to_edf/01b_spectral.py` | Welch PSD + line-noise report + impedance snapshot |
| 2 | `uv run python scripts/xdf_to_edf/02_convert.py` | Write the EDF+ with a per-channel spot check |
| 3 | `uv run python scripts/xdf_to_edf/03_manifest.py` | Generate the PDF manifest and the recipient README |

To run on a specific XDF (not the newest in `sampledata/`), put just
that file in `sampledata/` or edit `find_xdf()` in `_common.py`.

### Option B — minimal ad-hoc converter

Skip the forensics and manifest; just convert an XDF with a PSG channel
subset. Good for quick iterations when you already trust the data:

```bash
uv run python scripts/xdf_to_edf.py \
    sampledata/<file>.xdf sampledata/<file>_PSG.edf \
    --mapping config/psg_mapping.yaml
```

Verify the result:

```bash
uv run python -c "
import pyedflib
f = pyedflib.EdfReader('sampledata/<file>_PSG.edf')
print(f.getStartdatetime(), f.file_duration/60, 'min')
print(f.getSignalLabels())
f.close()
"
```

## Channels in the exported EDF+

With `config/psg_mapping.yaml`, the EDF contains **19 channels** at 500 Hz:

### AASM sleep-scoring essentials (11)

| EDF label | Source | Notes |
|---|---|---|
| `F3`, `F4` | BrainVision cap | Frontal EEG |
| `C3`, `C4` | BrainVision cap | Central EEG (AASM primary) |
| `O1`, `O2` | BrainVision cap | Occipital EEG |
| `M1-TP9`, `M2-TP10` | BrainVision cap | **Mastoid proxies** — TP9/TP10 sit on the mastoid bone in 10-10 and are widely used as M1/M2 references. Re-reference in your scoring software (e.g. `C3-M2 = C3 − M2-TP10`) |
| `EOG-L`, `EOG-R` | CGX `ExGa 1` / `ExGa 2` | Standard EOG derivations |
| `EMG-Chin1`, `EMG-Chin2` | CGX `ExGa 3` / `ExGa 4` | Bipolar submental pair — take the difference in your scoring software |

### Cardio / respiratory (4)

| EDF label | Source |
|---|---|
| `ECG` | CGX lead-II |
| `Respiration` | CGX respiratory piezo belt |
| `PPG` | CGX fingertip photoplethysmograph |
| `SpO2` | CGX pulse-oximeter |

### Autonomic / derived (4)

| EDF label | Source | Notes |
|---|---|---|
| `HR` | CGX | Device-side HR estimate from ECG/PPG |
| `GSR` | CGX | Skin conductance |
| `Temp` | CGX | Skin temperature |

> **Units note.** Everything is written with the `dimension` string
> `"microvol"` that the CGX driver declared. The EEG/EOG/EMG channels
> are genuinely µV. Non-EEG channels (SpO₂, HR, Resp, etc.) are actually
> in device-specific internal units that the CGX calls "microvolts" — the
> µV label is cosmetic on those. If you need physical conversion (SpO₂
> in %, HR in bpm), contact the IACS team for the CGX calibration
> coefficients.

## Reference / montage conventions

- **Online reference:** BrainVision's hardware reference during
  acquisition (FCz unless noted). Re-reference as needed in your
  scoring software.
- **AASM derivations:** `C3-M2`, `C4-M1`, `F3-M2`, `F4-M1`, `O1-M2`,
  `O2-M1` can all be built from the 8 BrainVision channels + the two
  mastoid-proxy channels in the EDF. No hardware averaging was done
  on our side.
- **EOG:** the CGX `ExGa` inputs are bipolar-capable; our wiring
  records each EOG electrode against the CGX reference (Fpz by default
  on the AIM-2 cap, confirm with your RA). For the scoring-software
  montage you'll typically want `EOG-L − M2` and `EOG-R − M1`.
- **Chin EMG:** take the bipolar `EMG-Chin1 − EMG-Chin2` in your
  scoring software.

## Known caveats / quirks

- **No dedicated `M1` / `M2` electrodes.** The BrainVision 64-cap we use
  doesn't ship with mastoid leads, so `TP9` and `TP10` serve as mastoid
  proxies. These sit over the mastoid bone and are commonly used for
  research PSG, but they're NOT biometrically identical to a 10-20 M1/M2
  gel electrode. Flag this if you're comparing against a clinical PSG.
- **The Sentiometer is the study instrument under validation, not a
  scoring input.** It's excluded from the EDF by default. Full Sentiometer
  signals are kept in the original XDF if needed.
- **Session duration.** Pilot recordings include task markers from the
  wake-period paradigms (Tasks 01–05) in a `P013_Task_Markers` stream.
  For a pure sleep recording these markers are empty or absent — the
  converter drops the stream. If you want task markers preserved
  alongside the EDF, export separately (e.g. as a CSV of the marker
  times, see `scripts/timeline_xdf.py`).
- **Packet counter + trigger channels.** CGX's internal `Packet Counter`
  and `TRIGGER` are auto-dropped (they're administrative, not
  physiological).
- **Impedance stream.** CGX's `...Impedance` sibling stream (channel
  impedance in kΩ) is dropped by default. Useful for QC pre-recording,
  not for scoring.

## When the export looks wrong

`scripts/xdf_to_edf.py --list-channels path/to.xdf` prints a full
inventory of streams and channels (labels, types, units) without
writing anything. Use it first to confirm CGX ExGa labels match the
intended electrode wiring for the session in question — earlier
recordings before the standard P013 montage was finalised may have
different ExGa assignments.
