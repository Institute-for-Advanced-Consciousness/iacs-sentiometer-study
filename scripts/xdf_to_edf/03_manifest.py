"""Step 3 — generate the PDF channel manifest for this subject.

Produces ``outputs/<SUBJECT>/P013_<SUBJECT>_channel_manifest.pdf`` plus a
matching README. Every quality number in the PDF is computed from the
actual XDF for this subject — nothing is hard-coded from a prior pilot.

Sections (data-driven; items with no data to report are omitted):

1. Header band — subject, recording start/duration, channel count, etc.
2. Data-quality summary — per-subject rail-saturation tally (first 120 s),
   median 60 Hz line-noise SNR, median broadband RMS.
2b. (NiccoTest only) EOG derivation caveat — Fp1−TP9 / Fp2−TP10 offline.
3. Numbered channel table — EDF name, modality, reference, anatomy,
   quality flag from this recording's actual saturation percentage.
4. AASM scoring recommendations — TP9/TP10 workarounds shown only if the
   mastoid proxies are actually unusable in this recording.
5. Marker / annotation legend — only if the XDF has any markers.
6. Technical notes (compact).
7. Diagnostic figures — embeds the three general PNGs from
   ``diagnostics/`` plus, for NiccoTest, the extended-report figures.

Run after 02_convert.py (the manifest reads the EDF channel order) and
after 01_inspect.py / 01b_spectral.py (reads the diagnostic PNGs).
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pyedflib
import pyxdf
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from scipy import signal

from _common import (
    SAMPLE_DIR,
    diag_dir_for,
    edf_path_for,
    find_xdf as _find_xdf_common,
    manifest_path_for,
    readme_path_for,
    subject_from_xdf,
)
from _report_helpers import (
    EPOCH_S,
    SAMPLE_CHANNELS,
    pick_epoch_times,
    plot_sample_epochs,
)

# ----- Thresholds (in one place so future edits don't drift) ----------------
BRAINAMP_RAIL_UV = 3200.0
FIRST_WINDOW_S = 120.0       # window used for rail-saturation tally
SPECTRAL_SEG_START_S = 300.0  # window used for PSD / 60 Hz SNR
SPECTRAL_SEG_DUR_S = 60.0
SAT_RED_PCT = 50.0           # > this % saturated → RED
SAT_YELLOW_PCT = 10.0        # between yellow and red → YELLOW

# ----- Anatomy / channel descriptions (static, not subject-specific) --------
EEG_ANATOMY = {
    "Fp1": "Left frontopolar",
    "Fp2": "Right frontopolar",
    "Fpz": "Midline frontopolar",
    "AF3": "Left anterior frontal medial",
    "AF4": "Right anterior frontal medial",
    "AF7": "Left anterior frontal lateral",
    "AF8": "Right anterior frontal lateral",
    "F1": "Left frontal paramedian",
    "F2": "Right frontal paramedian",
    "F3": "Left frontal (AASM)",
    "F4": "Right frontal (AASM)",
    "F5": "Left frontal intermediate",
    "F6": "Right frontal intermediate",
    "F7": "Left inferior frontal",
    "F8": "Right inferior frontal",
    "Fz": "Midline frontal",
    "FC1": "Left frontocentral paramedian",
    "FC2": "Right frontocentral paramedian",
    "FC3": "Left frontocentral",
    "FC4": "Right frontocentral",
    "FC5": "Left frontocentral lateral",
    "FC6": "Right frontocentral lateral",
    "FT7": "Left frontotemporal",
    "FT8": "Right frontotemporal",
    "FT9": "Left inferior frontotemporal",
    "FT10": "Right inferior frontotemporal",
    "C1": "Left central paramedian",
    "C2": "Right central paramedian",
    "C3": "Left central (AASM)",
    "C4": "Right central (AASM)",
    "C5": "Left central lateral",
    "C6": "Right central lateral",
    "Cz": "Midline central (vertex)",
    "CP1": "Left centroparietal paramedian",
    "CP2": "Right centroparietal paramedian",
    "CP3": "Left centroparietal",
    "CP4": "Right centroparietal",
    "CP5": "Left centroparietal lateral",
    "CP6": "Right centroparietal lateral",
    "CPz": "Midline centroparietal",
    "T7": "Left mid-temporal",
    "T8": "Right mid-temporal",
    "TP7": "Left posterior temporal",
    "TP8": "Right posterior temporal",
    "TP9": "Left mastoid proxy (over mastoid process)",
    "TP10": "Right mastoid proxy (over mastoid process)",
    "P1": "Left parietal paramedian",
    "P2": "Right parietal paramedian",
    "P3": "Left parietal (AASM)",
    "P4": "Right parietal (AASM)",
    "P5": "Left parietal intermediate",
    "P6": "Right parietal intermediate",
    "P7": "Left inferior parietal",
    "P8": "Right inferior parietal",
    "Pz": "Midline parietal",
    "PO3": "Left parieto-occipital",
    "PO4": "Right parieto-occipital",
    "PO7": "Left parieto-occipital lateral",
    "PO8": "Right parieto-occipital lateral",
    "POz": "Midline parieto-occipital",
    "O1": "Left occipital (AASM)",
    "O2": "Right occipital (AASM)",
    "Oz": "Midline occipital",
    "Iz": "Midline inion (below occipital)",
}

CGX_DESC = {
    "E1-M2": "Left eye EOG (below-lateral outer canthus) vs right mastoid",
    "E2-M1": "Right eye EOG (above-lateral outer canthus) vs left mastoid",
    "ChinZ-Chin1": "Submental midline EMG (ChinZ upper, Chin1 ~2 cm below)",
    "Chin2-Chin1prime": "Submental lateral EMG (right-lower vs left-lower)",
    "ECG": "Cardiac lead (single-ended, AIM-2 reference)",
    "Resp.": "Respiration via bio-impedance (arbitrary units)",
    "PPG": "Fingertip photoplethysmograph (pulse wave)",
    "SpO2": "Pulse oximetry (internal AIM-2 scaling; see caveat)",
    "HR": "Device-side heart-rate estimate (internal scaling)",
    "GSR": "Galvanic skin response / EDA (internal scaling)",
    "Temp.": "Skin temperature (internal scaling)",
}


# ----- XDF traversal helpers ------------------------------------------------

def _get(d, *keys, default=""):
    cur = d
    for k in keys:
        if isinstance(cur, list) and cur and isinstance(cur[0], dict):
            cur = cur[0]
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k, default)
    if isinstance(cur, list) and cur and isinstance(cur[0], str):
        return cur[0]
    return cur


def _channel_labels(stream):
    info = stream.get("info") or {}
    desc = info.get("desc") or []
    desc0 = desc[0] if desc else {}
    if not isinstance(desc0, dict):
        return []
    chs = desc0.get("channels") or []
    chs0 = chs[0] if chs else {}
    if not isinstance(chs0, dict):
        return []
    entries = chs0.get("channel") or []
    if not isinstance(entries, list):
        entries = [entries]
    out = []
    for c in entries:
        if not isinstance(c, dict):
            continue
        lab = c.get("label", [""])
        out.append(lab[0] if isinstance(lab, list) else str(lab))
    return out


def _unique_markers(streams):
    out = set()
    for s in streams:
        if _get(s, "info", "type").lower() != "markers":
            continue
        samples_raw = s.get("time_series")
        samples = samples_raw if samples_raw is not None else []
        for v in samples:
            lab = v[0] if isinstance(v, (list, tuple)) and v else str(v)
            out.add(str(lab))
    return sorted(out)


# ----- Per-subject computations ---------------------------------------------

def _compute_saturation(bv_stream, bv_labels):
    """Percent of samples over the BrainAmp ±3200 µV rails in the first 120 s.

    Returns a dict ``{label: pct}``. The percentages are per-channel over
    however many samples landed inside the 120 s window.
    """
    ts = np.asarray(bv_stream.get("time_stamps", []), dtype=float)
    data = np.asarray(bv_stream.get("time_series", []), dtype=float)
    if ts.size == 0 or data.size == 0:
        return {}
    t0 = float(ts[0])
    mask = (ts >= t0) & (ts < t0 + FIRST_WINDOW_S)
    d = data[mask, :] if data.ndim == 2 else np.empty((0, 0))
    if d.size == 0:
        return {}
    sat_counts = (np.abs(d) > BRAINAMP_RAIL_UV).sum(axis=0)
    out = {}
    for i in range(min(len(bv_labels), d.shape[1])):
        lab = bv_labels[i]
        if lab.lower() in ("triggerstream", "trigger"):
            continue
        out[lab] = 100.0 * sat_counts[i] / d.shape[0]
    return out


def _welch(x, fs=500.0):
    nperseg = int(fs * 4.0)
    noverlap = int(nperseg * 0.5)
    return signal.welch(
        x, fs=fs, nperseg=nperseg, noverlap=noverlap, scaling="density"
    )


def _line_snr_db(f, pxx, target=60.0, half_bw=1.0, baseline_bw=4.0):
    peak_band = (f >= target - half_bw) & (f <= target + half_bw)
    lo_band = (f >= target - baseline_bw - half_bw) & (f < target - half_bw)
    hi_band = (f > target + half_bw) & (f <= target + baseline_bw + half_bw)
    if peak_band.sum() == 0 or (lo_band.sum() + hi_band.sum()) == 0:
        return float("nan")
    peak = float(np.max(pxx[peak_band]))
    base = float(np.mean(pxx[lo_band | hi_band]))
    if peak <= 0 or base <= 0:
        return float("nan")
    return 10.0 * np.log10(peak / base)


def _compute_spectral_summary(bv_stream, bv_labels):
    """Median 60 Hz peak-to-baseline SNR and median broadband RMS on
    a clean mid-recording segment."""
    ts = np.asarray(bv_stream.get("time_stamps", []), dtype=float)
    data = np.asarray(bv_stream.get("time_series", []), dtype=float)
    if ts.size == 0 or data.size == 0:
        return {"median_60hz_db": float("nan"),
                "median_rms_uv": float("nan"),
                "n_over_2mv": 0,
                "per_ch_60hz": {}, "per_ch_rms": {}}
    t0 = float(ts[0])
    mask = ((ts >= t0 + SPECTRAL_SEG_START_S)
            & (ts < t0 + SPECTRAL_SEG_START_S + SPECTRAL_SEG_DUR_S))
    d = data[mask, :] if data.ndim == 2 else np.empty((0, 0))
    per_60, per_rms = {}, {}
    n_ch = min(len(bv_labels), d.shape[1])
    for j in range(n_ch):
        lab = bv_labels[j]
        if lab.lower() in ("triggerstream", "trigger"):
            continue
        x = d[:, j]
        f, p = _welch(x)
        per_60[lab] = _line_snr_db(f, p, 60.0)
        per_rms[lab] = float(np.sqrt(np.mean(x.astype(float) ** 2)))
    snrs = [v for v in per_60.values() if np.isfinite(v)]
    rmss = [v for v in per_rms.values() if np.isfinite(v)]
    return {
        "median_60hz_db": float(np.median(snrs)) if snrs else float("nan"),
        "median_rms_uv": float(np.median(rmss)) if rmss else float("nan"),
        "n_over_2mv": int(sum(1 for v in rmss if v > 2000.0)),
        "per_ch_60hz": per_60,
        "per_ch_rms": per_rms,
    }


def _compute_cgx_impedance(streams):
    """Return dict ``{channel_label: mean_kOhm}`` extracted from the CGX
    Impedance stream. Empty dict if no such stream is present."""
    imp = next(
        (s for s in streams if str(_get(s, "info", "type")).lower() == "impeadance"),
        None,
    )
    if imp is None:
        return {}
    labels = _channel_labels(imp)
    data = np.asarray(imp.get("time_series", []), dtype=float)
    if data.size == 0 or not labels:
        return {}
    means = np.nanmean(data, axis=0) if data.ndim == 2 else np.array([])
    return {lab: float(m) for lab, m in zip(labels, means) if np.isfinite(m)}


# ----- Marker-meaning lookup (keep compact) ---------------------------------

def _marker_meaning(m):
    """Single-line meaning for a marker string. Unknown → empty string."""
    lut = {
        "session_start": "Launcher opened the session marker outlet",
        "session_end": "All tasks complete, launcher closed cleanly",
        "session_abort": "RA aborted via Ctrl+C / GUI stop",
    }
    if m in lut:
        return lut[m]
    # Task-prefixed patterns
    if m.startswith("task00_"):
        return "Pre-session questionnaire event"
    if m.startswith("task01_"):
        return "Auditory oddball (P300)"
    if m.startswith("task02_"):
        return "RGB illuminance passive viewing"
    if m.startswith("task03_"):
        return "Backward masking face detection"
    if m.startswith("task04_"):
        return "Mind-state switching (gameplay / meditation)"
    if m.startswith("task05_"):
        return "SSVEP frequency ramp-down"
    return ""


# ----- Main -----------------------------------------------------------------

def main() -> int:
    xdf_path = _find_xdf_common()
    subject = subject_from_xdf(xdf_path)
    edf_path = edf_path_for(subject)
    pdf_path = manifest_path_for(subject)
    readme_path = readme_path_for(subject)
    diag_dir = diag_dir_for(subject)
    print(f"Subject: {subject}")
    if not edf_path.exists():
        raise SystemExit(f"EDF missing — run 02_convert.py first ({edf_path})")

    streams, _ = pyxdf.load_xdf(str(xdf_path))

    edf = pyedflib.EdfReader(str(edf_path))
    try:
        labels = edf.getSignalLabels()
        fs = [edf.getSampleFrequency(i) for i in range(edf.signals_in_file)]
        units = [edf.getPhysicalDimension(i) for i in range(edf.signals_in_file)]
        start = edf.getStartdatetime()
        dur = edf.file_duration
    finally:
        edf.close()

    # ----- Compute everything from the actual XDF --------------------------
    bv = next(
        (s for s in streams if s["info"]["name"][0] == "BrainAmpSeries-Dev_1"),
        None,
    )
    if bv is None:
        raise SystemExit("No BrainAmpSeries-Dev_1 stream in XDF.")
    bv_labels_raw = _channel_labels(bv)
    bv_labels = [
        l for l in bv_labels_raw
        if l.lower() not in ("triggerstream", "trigger")
    ]
    print(f"  EEG channels seen: {len(bv_labels)}")

    sat_pct = _compute_saturation(bv, bv_labels_raw)
    bad_rail = {l: p for l, p in sat_pct.items() if p > SAT_RED_PCT}
    borderline = {
        l: p for l, p in sat_pct.items()
        if SAT_YELLOW_PCT <= p <= SAT_RED_PCT
    }
    spectral = _compute_spectral_summary(bv, bv_labels_raw)
    cgx_imp = _compute_cgx_impedance(streams)
    unique_markers = _unique_markers(streams)

    # ----- Sample-EEG epochs figure (task-engaged vs late/sleep) -----------
    sample_epochs_path = diag_dir / "sample_eeg_epochs.png"
    try:
        bv_ts = np.asarray(bv.get("time_stamps", []), dtype=float)
        bv_data = np.asarray(bv.get("time_series", []), dtype=float)
        if bv_ts.size and bv_data.size:
            t0 = float(bv_ts[0])
            duration_s = float(bv_ts[-1] - t0)
            # Collect task-marker times (relative to BV start) across all
            # Markers streams. Empty for pure-sleep recordings (Sam / Yaya).
            marker_times: list[float] = []
            for s in streams:
                if _get(s, "info", "type").lower() != "markers":
                    continue
                ts_m = np.asarray(s.get("time_stamps", []), dtype=float)
                if ts_m.size == 0:
                    continue
                marker_times.extend((ts_m - t0).tolist())
            cond_times = pick_epoch_times(duration_s, marker_times)
            # Label convention: late bucket is always "Sleep" because for
            # pure-sleep recordings (Sam / Yaya) that's literal sleep, and
            # for task recordings (Nicco) the late window is the post-task
            # laying-down period ~15 min after the last task marker, which
            # is the sleep-onset / quiet-rest state the protocol targets.
            if not marker_times:
                early_label = "Early"
            else:
                early_label = "Task"
            late_label = "Sleep"
            plot_sample_epochs(
                bv_data, fs=500.0, labels=bv_labels_raw,
                condition_epochs=[
                    (early_label, cond_times["task"]),
                    (late_label,  cond_times["late"]),
                ],
                save_path=sample_epochs_path,
                title=(
                    f"Sample EEG — {subject}: "
                    f"{early_label} vs {late_label} "
                    f"(6 channels, {int(EPOCH_S)} s epochs)"
                ),
            )
            print(f"  wrote {sample_epochs_path}")
    except Exception as exc:  # noqa: BLE001
        print(f"  sample-epoch figure skipped: {exc}")
        sample_epochs_path = None

    n_red = sum(1 for lab in labels if lab in bad_rail)
    n_yellow = sum(1 for lab in labels if lab in borderline)
    n_green = len(labels) - n_red - n_yellow

    # ----- PDF styles ------------------------------------------------------
    styles = getSampleStyleSheet()
    h1, h2, h3 = styles["Heading1"], styles["Heading2"], styles["Heading3"]
    body = styles["BodyText"]
    small = ParagraphStyle(
        "small", parent=body, fontSize=8, leading=10, spaceAfter=4
    )
    mono = ParagraphStyle(
        "mono", parent=body, fontName="Courier", fontSize=8, leading=10
    )

    doc = SimpleDocTemplate(
        str(pdf_path), pagesize=letter,
        rightMargin=0.55 * inch, leftMargin=0.55 * inch,
        topMargin=0.55 * inch, bottomMargin=0.55 * inch,
    )
    story = []

    # ----- Header ----------------------------------------------------------
    story.append(Paragraph(f"P013 Channel Manifest — {subject}", h1))
    header_rows = [
        ["Subject", subject,
         "Recording start", start.strftime("%Y-%m-%d %H:%M:%S")],
        ["Duration (min)", f"{dur/60:.2f}",
         "Sample rate", "500 Hz"],
        ["Channels in EDF", str(len(labels)),
         "Source XDF", xdf_path.name],
    ]
    header_tbl = Table(header_rows, hAlign="LEFT",
                       colWidths=[1.1*inch, 2.3*inch, 1.1*inch, 2.8*inch])
    header_tbl.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f0f0f0")),
        ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#f0f0f0")),
    ]))
    story.append(header_tbl)
    story.append(Spacer(1, 0.15 * inch))

    # ----- Section 1: Data quality (computed from THIS XDF) ---------------
    story.append(Paragraph("1. Data quality (this recording)", h2))
    qtbl_rows = [
        ["Metric", "Value", "Notes"],
        ["Rail-saturated channels (>50% in first 120 s)",
         str(n_red),
         f"> {SAT_RED_PCT:.0f}% of samples hit ±{BRAINAMP_RAIL_UV:.0f} µV"],
        ["Borderline channels (10–50%)",
         str(n_yellow),
         "Inspect before using"],
        ["Clean channels", str(n_green), f"< {SAT_YELLOW_PCT:.0f}%"],
        ["Median 60 Hz peak-to-baseline SNR",
         f"{spectral['median_60hz_db']:.1f} dB",
         f"EEG channels, segment [t₀+{int(SPECTRAL_SEG_START_S)}, "
         f"+{int(SPECTRAL_SEG_START_S+SPECTRAL_SEG_DUR_S)}] s"],
        ["Median broadband RMS (µV)",
         f"{spectral['median_rms_uv']:.1f}",
         f"{spectral['n_over_2mv']}/{len(spectral['per_ch_rms'])} channels "
         "over 2000 µV"],
    ]
    qtbl = Table(qtbl_rows, hAlign="LEFT",
                 colWidths=[2.7*inch, 1.0*inch, 3.6*inch])
    qtbl.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BACKGROUND", (1, 1), (1, 1),
         colors.HexColor("#ffcdd2" if n_red else "#c8e6c9")),
        ("BACKGROUND", (1, 2), (1, 2),
         colors.HexColor("#fff59d" if n_yellow else "#c8e6c9")),
        ("BACKGROUND", (1, 3), (1, 3), colors.HexColor("#c8e6c9")),
    ]))
    story.append(qtbl)
    story.append(Spacer(1, 0.08 * inch))

    if bad_rail:
        story.append(Paragraph(
            f"<b>Rail-saturated ({len(bad_rail)}):</b> " +
            ", ".join(
                f"{lab} ({pct:.0f}%)"
                for lab, pct in sorted(bad_rail.items(), key=lambda kv: -kv[1])
            ),
            small,
        ))
    if borderline:
        story.append(Paragraph(
            f"<b>Borderline ({len(borderline)}):</b> " +
            ", ".join(
                f"{lab} ({pct:.0f}%)"
                for lab, pct in sorted(
                    borderline.items(), key=lambda kv: -kv[1])
            ),
            small,
        ))
    if not bad_rail and not borderline:
        story.append(Paragraph(
            "All EEG channels are within the clean-signal envelope for the "
            "first 120 s of recording.",
            small,
        ))
    story.append(Spacer(1, 0.12 * inch))

    # ----- Section 2 (NiccoTest only): EOG derivation caveat --------------
    if subject == "NiccoTest":
        story.append(Paragraph(
            "2. EOG / CGX-ExG caveat — NiccoTest session specifics", h2,
        ))
        story.append(Paragraph(
            "<b>This session did NOT use the standard CGX AIM-2 ExGa face "
            "leads for EOG.</b> The usual P013 setup places a '−' lead on "
            "the face near each outer canthus and references to the "
            "contralateral mastoid via the AIM-2, producing AASM-standard "
            "E1-M2 and E2-M1 derivations. For this session, those face "
            "leads were not attached; EOG is instead derived <b>offline</b> "
            "from the BrainVision cap as:",
            body,
        ))
        story.append(Paragraph(
            "&nbsp;&nbsp;• <b>EOG-L (left)</b> = Fp1 − TP9", body))
        story.append(Paragraph(
            "&nbsp;&nbsp;• <b>EOG-R (right)</b> = Fp2 − TP10", body))
        story.append(Paragraph(
            "<b>Implication for this EDF — EOG only.</b> The channels "
            "labeled <font face='Courier'>E1-M2</font> / "
            "<font face='Courier'>E2-M1</font> carry the raw ExGa 1 / 2 "
            "signals with the AASM naming applied mechanically, but "
            "because the face leads were not placed, they <b>do not "
            "represent the AASM EOG derivation</b>. Treat those two "
            "channels as floating / unused. The <b>chin EMG</b> channels "
            "<font face='Courier'>ChinZ-Chin1</font> (ExGa 3) and "
            "<font face='Courier'>Chin2-Chin1prime</font> (ExGa 4) were "
            "placed per the standard protocol and are <b>authoritative</b> "
            "for this session.",
            body,
        ))
        story.append(Paragraph(
            "<b>For scoring:</b> use Fp1, Fp2, TP9, TP10 from the cap and "
            "derive the offline EOG bipolars in your scoring software; "
            "use <font face='Courier'>ChinZ-Chin1</font> / "
            "<font face='Courier'>Chin2-Chin1prime</font> as-is for chin "
            "EMG. The extended report "
            "(<font face='Courier'>P013_NiccoTest_extended_report.pdf</font>) "
            "carries PSD + time-domain plots of these derivations.",
            body,
        ))
        story.append(Spacer(1, 0.12 * inch))

    story.append(PageBreak())

    # ----- Section 3: Channel table ---------------------------------------
    sec_num = 3 if subject == "NiccoTest" else 2
    story.append(Paragraph(f"{sec_num}. Numbered channel table", h2))

    table_rows = [[
        Paragraph("<b>#</b>", small),
        Paragraph("<b>EDF name</b>", small),
        Paragraph("<b>Modality</b>", small),
        Paragraph("<b>Type</b>", small),
        Paragraph("<b>Reference</b>", small),
        Paragraph("<b>fs</b>", small),
        Paragraph("<b>Unit</b>", small),
        Paragraph("<b>Anatomical description</b>", small),
    ]]
    for i, lab in enumerate(labels):
        is_eeg = lab in EEG_ANATOMY
        is_cgx = lab in CGX_DESC
        if is_eeg:
            modality = "EEG"; chtype = "Referential"
            ref = "FCz (online, hardware)"; anat = EEG_ANATOMY.get(lab, "")
        elif is_cgx:
            if lab in {"E1-M2", "E2-M1"}:
                modality = "EOG"; chtype = "Bipolar (AASM)"
                ref = ("M2 (right mastoid)" if lab == "E1-M2"
                       else "M1 (left mastoid)")
            elif lab in {"ChinZ-Chin1", "Chin2-Chin1prime"}:
                modality = "EMG"; chtype = "Bipolar"
                ref = ("Chin1 (submental)" if lab == "ChinZ-Chin1"
                       else "Chin1prime (submental)")
            elif lab == "ECG":
                modality = "ECG"; chtype = "Referential"
                ref = "AIM-2 internal"
            elif lab == "Resp.":
                modality = "Respiration"; chtype = "Bio-impedance"
                ref = "AIM-2 internal"
            elif lab in {"PPG", "SpO2"}:
                modality = "Oximetry"; chtype = "Optical"
                ref = "AIM-2 internal"
            elif lab == "GSR":
                modality = "EDA"; chtype = "Skin conductance"
                ref = "AIM-2 internal"
            elif lab == "HR":
                modality = "HR (derived)"; chtype = "Derived"
                ref = "AIM-2 internal"
            elif lab == "Temp.":
                modality = "Temp"; chtype = "Thermistor"
                ref = "AIM-2 internal"
            else:
                modality = chtype = ref = "?"
            anat = CGX_DESC.get(lab, "")
        else:
            modality = chtype = ref = "?"; anat = ""

        # NiccoTest: E1-M2 / E2-M1 face leads were not placed (see
        # Section 2). Chin EMG was placed per protocol, so leave those
        # channels untagged.
        if subject == "NiccoTest" and lab in {"E1-M2", "E2-M1"}:
            anat = (
                "<i>Face lead NOT placed this session — treat as floating. "
                "See Section 2.</i>  " + anat
            )

        # Subject-specific quality flag suffix, from computed saturation.
        if lab in bad_rail:
            name_cell = (
                f"<font color='#b71c1c'><b>{lab}</b></font>  "
                f"(RED, {bad_rail[lab]:.0f}%)"
            )
        elif lab in borderline:
            name_cell = (
                f"<font color='#f57c00'><b>{lab}</b></font>  "
                f"(YELLOW, {borderline[lab]:.0f}%)"
            )
        else:
            name_cell = lab

        table_rows.append([
            Paragraph(str(i + 1), small),
            Paragraph(name_cell, small),
            Paragraph(modality, small),
            Paragraph(chtype, small),
            Paragraph(ref, small),
            Paragraph(f"{int(fs[i])} Hz", small),
            Paragraph(units[i], small),
            Paragraph(anat, small),
        ])
    ch_tbl = Table(
        table_rows,
        colWidths=[0.30*inch, 1.25*inch, 0.7*inch, 0.85*inch,
                   1.2*inch, 0.45*inch, 0.35*inch, 2.3*inch],
        repeatRows=1,
    )
    ch_tbl.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, colors.HexColor("#f5f5f5")]),
    ]))
    story.append(ch_tbl)
    story.append(PageBreak())

    # ----- Section 4: AASM scoring recommendations (conditional) -----------
    sec_num += 1
    mastoid_bad = any(l in bad_rail for l in ("TP9", "TP10"))
    mastoid_border = any(l in borderline for l in ("TP9", "TP10"))
    story.append(Paragraph(
        f"{sec_num}. AASM scoring recommendations", h2,
    ))
    if subject == "NiccoTest":
        story.append(Paragraph(
            "ExGa-based EOG / chin EMG is unavailable (see Section 2). Derive "
            "EOG offline: <font face='Courier'>Fp1−TP9</font> (EOG-L), "
            "<font face='Courier'>Fp2−TP10</font> (EOG-R). Chin EMG was not "
            "captured via the ExGa 3/4 ports — the extended report has the "
            "relevant spectral diagnostic.",
            body,
        ))
    else:
        story.append(Paragraph(
            "<b>Primary derivations (CGX hardware-level, use as-is):</b> "
            "<font face='Courier'>E1-M2</font> (left EOG), "
            "<font face='Courier'>E2-M1</font> (right EOG), "
            "<font face='Courier'>ChinZ-Chin1</font> (primary chin EMG). "
            "Backup: <font face='Courier'>Chin2-Chin1prime</font>.",
            body,
        ))
    story.append(Spacer(1, 0.06 * inch))

    if mastoid_bad or mastoid_border:
        story.append(Paragraph(
            "<b>EEG referencing:</b> TP9 / TP10 (linked-mastoid proxies) are "
            f"{'rail-saturated' if mastoid_bad else 'borderline'} in this "
            "recording. Workarounds in order of preference:", body,
        ))
        story.append(Paragraph(
            "(a) Use the cap EEG channels monopolar-to-FCz "
            "(hardware online reference). Many research-grade scorers accept "
            "monopolar-to-reference EEG.",
            body,
        ))
        story.append(Paragraph(
            "(b) Common-average over clean central channels — e.g. "
            "<font face='Courier'>"
            "raw.set_eeg_reference(ref_channels=['C3','C4','Cz','CP1','CP2'])"
            "</font>.",
            body,
        ))
        story.append(Paragraph(
            "(c) Contralateral homologs: "
            "<font face='Courier'>C3-C4</font>, "
            "<font face='Courier'>F3-F4</font>. Non-standard but informative.",
            body,
        ))
    else:
        story.append(Paragraph(
            "<b>EEG referencing:</b> TP9 / TP10 are usable in this recording. "
            "Re-reference to them in your scoring tool, e.g. "
            "<font face='Courier'>"
            "raw.set_eeg_reference(ref_channels=['TP9', 'TP10'])</font>.",
            body,
        ))
    story.append(Spacer(1, 0.06 * inch))

    if cgx_imp:
        story.append(Paragraph(
            "<b>CGX impedance (mean across full recording):</b>  " +
            "  ·  ".join(
                f"<font face='Courier'>{lab}</font>={kohm:.1f} kΩ"
                for lab, kohm in cgx_imp.items()
            ),
            small,
        ))
    story.append(Spacer(1, 0.12 * inch))

    # ----- Section 5: Markers ---------------------------------------------
    if unique_markers:
        sec_num += 1
        story.append(Paragraph(
            f"{sec_num}. Marker / annotation legend  "
            f"({len(unique_markers)} unique strings)",
            h2,
        ))
        rows = [["#", "Marker string", "Meaning"]]
        for i, m in enumerate(unique_markers):
            rows.append([str(i + 1), m, _marker_meaning(m)])
        mk_tbl = Table(rows, colWidths=[0.35*inch, 2.6*inch, 4.2*inch])
        mk_tbl.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
            ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(mk_tbl)
        story.append(Spacer(1, 0.12 * inch))

    # ----- Section 6: Technical notes (compact) ---------------------------
    sec_num += 1
    story.append(Paragraph(f"{sec_num}. Technical notes", h2))
    story.append(Paragraph(
        "<b>Hardware.</b> BrainVision actiCAP 64-ch active cap + BrainAmp "
        "(AC-coupled, ~0.016 Hz hardware HP, ±3276.7 µV input range, 500 Hz "
        "ADC). Physiological side channels from CGX AIM-2. Both amplifiers "
        "stream via Lab Streaming Layer into a single LabRecorder. EDF+ is "
        "the export; XDF is preserved for re-runs.",
        body,
    ))
    story.append(Paragraph(
        "<b>Filters.</b> No software filtering before EDF export. Apply "
        "your scoring pipeline's own band-pass (typically 0.3–35 Hz EEG, "
        "0.3–10 Hz EOG, 10–100 Hz chin EMG).",
        body,
    ))
    story.append(Paragraph(
        "<b>Units.</b> CGX non-EEG channels (HR / GSR / Temp. / SpO₂ / "
        "Resp. / PPG) stream as <i>microvolts</i> but carry device-internal "
        "scaling. Absolute values aren't physical — read them qualitatively. "
        "EEG / EOG / EMG are in real µV.",
        body,
    ))
    story.append(Spacer(1, 0.12 * inch))

    # ----- Section 7: Diagnostic figures ----------------------------------
    sec_num += 1
    story.append(Paragraph(f"{sec_num}. Diagnostic figures", h2))
    story.append(Paragraph(
        "Generated by the 01_inspect / 01b_spectral steps from the same XDF "
        "that produced this EDF.", small,
    ))

    # Sample EEG epochs — full page (task-engaged vs sleep/late-recording).
    if sample_epochs_path is not None and sample_epochs_path.exists():
        story.append(PageBreak())
        story.append(Paragraph(
            "Sample EEG — task-engaged vs sleep / late-recording", h3,
        ))
        story.append(Paragraph(
            "Six representative channels (Fp1/Fp2 frontal, C3/C4 central, "
            f"O1/O2 occipital), {int(EPOCH_S)} s epochs. The first three "
            "columns sample the active / task-engaged portion of the "
            "recording; the last three are drawn from ~15 min after the "
            "final task marker (where applicable; see caption). Y-axis "
            "clipped at ±200 µV so saturated channels don't drown out the "
            "others.",
            small,
        ))
        story.append(Spacer(1, 0.05 * inch))
        story.append(Image(
            str(sample_epochs_path), width=7.3*inch, height=8.2*inch
        ))

    story.append(PageBreak())
    general_diag = [
        ("eeg_first_120s_rms.png",
         "Per-channel RMS heatmap over the first 120 s. Bright rows are "
         "rail-saturated / disconnected channels."),
        ("eeg_time_course_first_2min.png",
         "Raw traces (Fp1 / Cz / Oz / TP9) for the first 2 minutes."),
        ("psd_by_modality.png",
         f"Welch PSD (4 s / 50% overlap) for Cz, Fp1, and the four ExGa "
         f"derivations over a clean 60 s segment starting at t₀+{int(SPECTRAL_SEG_START_S)} s. "
         "Red shading marks 60 / 120 Hz bands."),
    ]
    for name, caption in general_diag:
        p = diag_dir / name
        if p.exists():
            story.append(Spacer(1, 0.06 * inch))
            story.append(Image(str(p), width=7.1*inch, height=2.8*inch))
            story.append(Paragraph(caption, small))

    # NiccoTest: additional embed of extended-report figures for convenience.
    if subject == "NiccoTest":
        nicco_extras = [
            ("xsubj_60hz_snr.png",
             "Cross-subject 60 Hz SNR (NiccoTest / Sam / Yaya)."),
            ("xsubj_broadband_rms.png",
             "Cross-subject broadband RMS."),
            ("nicco_on_off_fullspectrum_heatmap.png",
             "Sentiometer ON vs OFF — log₁₀(PSD_ON / PSD_OFF) heatmap across "
             "all EEG channels and 0.5–60 Hz. Red = louder ON; blue = "
             "louder OFF."),
            ("nicco_sento_on_off_psd.png",
             "Sentiometer ON vs OFF — PSDs at representative scalp sites."),
            ("nicco_on_off_sample_eeg.png",
             "Sentiometer ON vs OFF — sample EEG at the 6 representative "
             "channels, three epochs in each window."),
            ("nicco_sento_on_off_20hz_delta.png",
             "Sentiometer ON vs OFF — per-channel 20 Hz power delta "
             "(negative = quieter with device off)."),
            ("nicco_eog_traces.png",
             "Offline EOG derivations — 10 s mid-window traces."),
            ("nicco_eog_psd.png",
             "Offline EOG derivations — PSD comparison ON vs OFF."),
        ]
        for name, caption in nicco_extras:
            p = diag_dir / name
            if p.exists():
                story.append(Spacer(1, 0.06 * inch))
                story.append(Image(str(p), width=7.1*inch, height=3.4*inch))
                story.append(Paragraph(caption, small))

    doc.build(story)
    print(f"wrote {pdf_path}")

    # ----- Subject-specific README ----------------------------------------
    readme_path.write_text(_README_TEMPLATE.format(
        subject=subject,
        edf_name=edf_path.name,
        manifest_name=pdf_path.name,
        readme_name=readme_path.name,
        duration_min=f"{dur/60:.2f}",
        n_channels=len(labels),
        n_red=n_red,
        n_yellow=n_yellow,
        n_green=n_green,
        median_60=f"{spectral['median_60hz_db']:.1f}",
        median_rms=f"{spectral['median_rms_uv']:.1f}",
        nicco_note=(
            "\nEOG: cap-derived Fp1-TP9 / Fp2-TP10 (see Section 2 of the "
            "manifest).\n"
            if subject == "NiccoTest" else ""
        ),
    ))
    print(f"wrote {readme_path}")
    return 0


_README_TEMPLATE = """\
P013 {subject} — PSG handoff
============================

CONTENTS
--------
{edf_name}           EDF+, {n_channels} channels, 500 Hz, {duration_min} min
{manifest_name}      Numbered channel table, quality, markers, figures
{readme_name}        This file

diagnostics/
  eeg_first_120s_rms.png         Per-channel RMS heatmap (first 120 s)
  eeg_time_course_first_2min.png Raw traces (Fp1 / Cz / Oz / TP9)
  psd_by_modality.png            Welch PSD at t=300 s
  psd_data.csv                   PSDs as CSV
  line_noise_report.txt          60 / 120 / 180 Hz SNR per channel


QUALITY (computed from this XDF)
--------------------------------
Rail-saturated channels (RED, >50% sat in first 120 s): {n_red}
Borderline channels    (YELLOW, 10-50%):                {n_yellow}
Clean channels         (GREEN):                         {n_green}
Median 60 Hz SNR (EEG, mid-recording 60 s):   {median_60} dB
Median broadband RMS (EEG, mid-recording):    {median_rms} µV
{nicco_note}

HOW TO OPEN
-----------
Any EDF+ reader:
  EDFbrowser (free):  https://www.teuniz.net/edfbrowser/
  MNE-Python:         mne.io.read_raw_edf("{edf_name}")
  EEGLAB:             pop_biosig("{edf_name}")


CONTACT
-------
Nicco Reggente, Ph.D.
Institute for Advanced Consciousness Studies
nicco@advancedconsciousness.org
"""


if __name__ == "__main__":
    raise SystemExit(main())
