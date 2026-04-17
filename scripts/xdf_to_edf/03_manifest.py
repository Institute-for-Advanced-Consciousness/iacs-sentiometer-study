"""Step 3 — generate the PDF channel manifest for Paller.

Produces ``outputs/P013_PILOT_01_channel_manifest.pdf``. Uses reportlab
for layout because the project doesn't carry a heavier PDF toolchain.

Sections:

1. Quick answers to Dr. Paller's direct questions (mastoid, EDF+
   compatibility, online reference).
2. Data quality summary with the pilot-phase acquisition context, a
   green/yellow/red channel-status count, and the headline TP9/TP10
   unusability note.
3. Numbered channel table (1 .. N) — EDF name, modality, type,
   reference, sample rate, unit, anatomical description.
4. AASM scoring recommendations with suggested workarounds for the
   peripheral-saturation situation.
5. Marker / annotation legend — scanned from the actual XDF.
6. Technical notes (hardware, filters, known caveats).
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
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
    PageBreak,
)

from _common import (
    SAMPLE_DIR,
    edf_path_for,
    find_xdf as _find_xdf_common,
    manifest_path_for,
    readme_path_for,
    subject_from_xdf,
)

# From Step 1 forensic.
BAD_RAIL = {
    "TP9": 97.92, "TP7": 96.18, "F8": 95.41, "AF7": 94.01, "PO8": 92.19,
    "Iz": 88.32, "FT10": 88.17, "FT7": 87.75, "P8": 87.74, "F6": 86.87,
    "FT8": 85.20, "TP8": 83.69, "T7": 76.75, "O1": 73.72, "T8": 68.02,
    "TP10": 58.26, "AF8": 54.77,
}
BORDERLINE = {"FT9": 16.46, "FC4": 9.46, "Fp2": 2.68, "O2": 0.0}

# From Step 1b impedance snapshot (mean across full recording).
CGX_IMP_KOHM = {
    "E1-M2": 11.4,
    "E2-M1": 10.9,
    "ChinZ-Chin1": 12.6,
    "Chin2-Chin1prime": 13.4,
}

# Anatomical descriptions for EEG channels (10-10 system).
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


def _find_xdf():
    return _find_xdf_common()


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


def main() -> int:
    xdf_path = _find_xdf()
    subject = subject_from_xdf(xdf_path)
    edf_path = edf_path_for(subject)
    pdf_path = manifest_path_for(subject)
    readme_path = readme_path_for(subject)
    print(f"Subject: {subject}")
    if not edf_path.exists():
        raise SystemExit(
            f"EDF missing — run 02_convert.py first ({edf_path})"
        )
    streams, _ = pyxdf.load_xdf(str(xdf_path))

    edf = pyedflib.EdfReader(str(edf_path))
    try:
        labels = edf.getSignalLabels()
        fs = [edf.getSampleFrequency(i) for i in range(edf.signals_in_file)]
        units = [edf.getPhysicalDimension(i) for i in range(edf.signals_in_file)]
        start = edf.getStartdatetime()
        dur = edf.file_duration
        technician = edf.getTechnician()
        prefilters = [edf.getPrefilter(i) for i in range(edf.signals_in_file)]
    finally:
        edf.close()

    unique_markers = _unique_markers(streams)

    # ----- Quality tally -----
    n_red = sum(1 for lab in labels if lab in BAD_RAIL)
    n_yellow = sum(1 for lab in labels if lab in BORDERLINE)
    n_green = len(labels) - n_red - n_yellow

    # ----- Build PDF -----
    styles = getSampleStyleSheet()
    h1 = styles["Heading1"]
    h2 = styles["Heading2"]
    h3 = styles["Heading3"]
    body = styles["BodyText"]
    small = ParagraphStyle(
        "small", parent=body, fontSize=8, leading=10, spaceAfter=4
    )
    mono = ParagraphStyle(
        "mono", parent=body, fontName="Courier", fontSize=8, leading=10
    )

    doc = SimpleDocTemplate(
        str(pdf_path),
        pagesize=letter,
        rightMargin=0.6 * inch,
        leftMargin=0.6 * inch,
        topMargin=0.6 * inch,
        bottomMargin=0.6 * inch,
    )
    story = []

    # ---- Header ----
    story.append(Paragraph(
        "P013 Sleep Recording — Channel Manifest for Scoring", h1
    ))
    story.append(Paragraph(
        f"Subject code: <b>{subject}</b>  "
        f"&nbsp;&nbsp; Protocol: <b>P013</b>", body
    ))
    story.append(Paragraph(
        f"Recording start: <b>{start.strftime('%Y-%m-%d %H:%M:%S')} (local "
        f"machine time)</b>", body
    ))
    story.append(Paragraph(
        f"Duration: <b>{dur/60:.2f} min</b> ({dur:.1f} s)  &nbsp;&nbsp; "
        f"Sample rate: <b>500 Hz</b> (all channels)", body
    ))
    story.append(Paragraph(
        f"Channels: <b>{len(labels)}</b> &nbsp;(64 EEG + 11 CGX = 75)", body
    ))
    story.append(Paragraph(
        "Prepared for: <b>Dr. Ken Paller</b>, Northwestern University", body
    ))
    story.append(Paragraph(
        "Prepared by: Institute for Advanced Consciousness Studies  "
        "(<i>nicco@advancedconsciousness.org</i>)", body
    ))
    story.append(Spacer(1, 0.15 * inch))

    # ---- Section 1: Quick answers ----
    story.append(Paragraph("1. Quick answers to your questions", h2))
    story.append(Paragraph(
        "<b>Mastoid = bony protrusion behind the ear</b> on the temporal "
        "bone (mastoid process). Yes — that is our EOG reference. E1 "
        "(left EOG) is referenced to M2 (right mastoid); E2 (right EOG) "
        "to M1 (left mastoid); both are AASM-standard derivations done "
        "at the CGX AIM-2 hardware level.",
        body,
    ))
    story.append(Paragraph(
        "<b>File format:</b> EDF+ with annotations embedded. Openable in "
        "EDFbrowser (free), Polyman, WonamBi, MNE-Python, EEGLAB, and "
        "most clinical PSG scoring software.",
        body,
    ))
    story.append(Paragraph(
        "<b>EEG online reference = FCz</b> (BrainVision actiCAP 64-ch "
        "default). <b>Ground = AFz.</b> Neither FCz nor AFz is a data "
        "channel in this recording — both are handled at the amplifier. "
        "<b>TP9 and TP10</b> are data channels positioned over the "
        "mastoid processes and would normally serve as offline "
        "linked-mastoid references for AASM derivations "
        "(C3-M2, C4-M1, F3-M2, F4-M1, O1-M2, O2-M1) — "
        "<b>however, see Section 2: both are rail-saturated in this "
        "pilot recording and should NOT be used.</b>",
        body,
    ))
    story.append(Spacer(1, 0.12 * inch))

    # ---- Section 2: Data-quality summary ----
    story.append(Paragraph("2. Data quality summary", h2))
    story.append(Paragraph(
        "<b>Recording context.</b> This recording was a deliberate pilot "
        "session conducted with an IACS staff member (not a study "
        "participant). Acquisition was intentionally accepted with "
        "lower-than-protocol impedance values due to hardware "
        "accommodations (hearing aids) and pilot-phase time "
        "constraints. The rail-saturated peripheral channels below "
        "reflect a known pilot-phase tradeoff to validate the full "
        "end-to-end pipeline — they are <b>not</b> representative of "
        "protocol acquisition quality.",
        body,
    ))

    quality_tbl = Table(
        [
            ["Quality", "Count", "Criterion"],
            ["GREEN  — usable for scoring", str(n_green),
             "<10% rail-saturation in first 120 s OR impedance within protocol"],
            ["YELLOW — inspect before using", str(n_yellow),
             "10–50% rail-saturation, or other caveat flagged below"],
            ["RED    — do not use", str(n_red),
             ">50% rail-saturation in first 120 s (sustained throughout recording)"],
        ],
        colWidths=[1.7 * inch, 0.8 * inch, 4.7 * inch],
    )
    quality_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("BACKGROUND", (0, 1), (0, 1), colors.HexColor("#c8e6c9")),
        ("BACKGROUND", (0, 2), (0, 2), colors.HexColor("#fff59d")),
        ("BACKGROUND", (0, 3), (0, 3), colors.HexColor("#ffcdd2")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
    ]))
    story.append(quality_tbl)
    story.append(Spacer(1, 0.1 * inch))

    story.append(Paragraph(
        f"<b>Rail-saturated channels ({len(BAD_RAIL)}):</b> " +
        ", ".join(f"{lab} ({pct:.0f}%)" for lab, pct in sorted(
            BAD_RAIL.items(), key=lambda kv: -kv[1])),
        small,
    ))
    story.append(Paragraph(
        f"<b>Borderline channels:</b> " +
        ", ".join(f"{lab} ({pct:.0f}%)" for lab, pct in BORDERLINE.items()),
        small,
    ))

    story.append(Paragraph(
        "<b>Key implication for AASM derivations:</b> the standard "
        "left-hemisphere montage (F3-M2, C3-M2, O1-M2) is unavailable "
        "because <b>TP9 (97.9% saturated)</b> and <b>TP10 (58.3% "
        "saturated)</b> cannot stand in for M1/M2 in this recording. "
        "The right-hemisphere EOG (E2-M1) is also compromised because "
        "the left-mastoid reference lives on the CGX AIM-2 which is "
        "physically separate from the cap — <b>the dedicated CGX "
        "montage does still produce a clean E1-M2 / E2-M1 pair</b>, "
        "since those mastoid leads are wired into the AIM-2 EXG ports "
        "directly rather than taken from the BrainVision cap. See "
        "Section 4 for suggested scoring workarounds.",
        body,
    ))
    story.append(Spacer(1, 0.15 * inch))

    story.append(PageBreak())

    # ---- Section 3: Channel table ----
    story.append(Paragraph("3. Numbered channel table", h2))
    story.append(Paragraph(
        "EEG channels in recording order match the BrainVision .cfg "
        "verified in Step 1 inspection. CGX channels follow the P013 "
        "AASM rename (E1-M2, E2-M1, ChinZ-Chin1, Chin2-Chin1prime) "
        "plus the AIM-2 peripheral stack.",
        small,
    ))

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
            modality = "EEG"
            chtype = "Referential"
            ref = "FCz (online, hardware)"
            anat = EEG_ANATOMY.get(lab, "")
        elif is_cgx:
            if lab in {"E1-M2", "E2-M1"}:
                modality = "EOG"
                chtype = "Bipolar (AASM)"
                ref = "M2 (right mastoid)" if lab == "E1-M2" else "M1 (left mastoid)"
            elif lab in {"ChinZ-Chin1", "Chin2-Chin1prime"}:
                modality = "EMG"
                chtype = "Bipolar"
                ref = ("Chin1 (submental)" if lab == "ChinZ-Chin1"
                       else "Chin1prime (submental)")
            elif lab == "ECG":
                modality = "ECG"; chtype = "Referential"; ref = "AIM-2 internal"
            elif lab == "Resp.":
                modality = "Respiration"; chtype = "Bio-impedance"; ref = "AIM-2 internal"
            elif lab in {"PPG", "SpO2"}:
                modality = "Oximetry"; chtype = "Optical"; ref = "AIM-2 internal"
            elif lab == "GSR":
                modality = "EDA"; chtype = "Skin conductance"; ref = "AIM-2 internal"
            elif lab == "HR":
                modality = "HR (derived)"; chtype = "Derived"; ref = "AIM-2 internal"
            elif lab == "Temp.":
                modality = "Temp"; chtype = "Thermistor"; ref = "AIM-2 internal"
            else:
                modality = chtype = ref = "?"
            anat = CGX_DESC.get(lab, "")
        else:
            modality = chtype = ref = "?"; anat = ""

        # Quality flag suffix
        if lab in BAD_RAIL:
            name_cell = f"<font color='#b71c1c'><b>{lab}</b></font> (RED)"
        elif lab in BORDERLINE:
            name_cell = f"<font color='#f57c00'><b>{lab}</b></font> (YELLOW)"
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
        colWidths=[0.30*inch, 1.05*inch, 0.7*inch, 0.85*inch,
                   1.25*inch, 0.45*inch, 0.35*inch, 2.3*inch],
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

    # ---- Section 4: AASM scoring recommendations ----
    story.append(Paragraph("4. AASM scoring channel recommendations", h2))
    story.append(Paragraph(
        "<b>Primary derivations (written directly into the EDF by the "
        "CGX AIM-2 hardware — use as-is):</b>",
        body,
    ))
    story.append(Paragraph(
        "<font face='Courier'>E1-M2</font> — left EOG (AASM-standard) &middot; "
        "<font face='Courier'>E2-M1</font> — right EOG (AASM-standard) &middot; "
        "<font face='Courier'>ChinZ-Chin1</font> — submental EMG (primary)",
        body,
    ))
    story.append(Paragraph(
        "<b>Backup:</b> <font face='Courier'>Chin2-Chin1prime</font> — "
        "lateral submental EMG pair, independent of the primary chin pair.",
        body,
    ))
    story.append(Spacer(1, 0.05 * inch))

    story.append(Paragraph(
        "<b>EEG re-reference for AASM C3-M2 / C4-M1 / F3-M2 / F4-M1 / "
        "O1-M2 / O2-M1:</b>",
        body,
    ))
    story.append(Paragraph(
        "The usual plan would be to re-reference the central / frontal / "
        "occipital cap electrodes against the contralateral mastoid proxy "
        "(TP9 or TP10) in your scoring tool, e.g. in MNE-Python:",
        body,
    ))
    story.append(Paragraph(
        "<font face='Courier'>raw.set_eeg_reference(ref_channels=['TP9', 'TP10'])</font>",
        mono,
    ))
    story.append(Paragraph(
        "<b>That will not work in this pilot recording.</b> TP9 and TP10 "
        "are both rail-saturated. Three workarounds, in order of "
        "preference:",
        body,
    ))
    story.append(Paragraph(
        "<b>(a)</b> Use the AIM-2 EOG / chin EMG channels as-is (they "
        "are correctly referenced at the hardware level) plus any of "
        "the clean central/frontal EEG channels monopolar against FCz "
        "(hardware online reference). Many scoring packages accept "
        "monopolar-to-reference EEG for research sleep scoring, even "
        "though the AASM strictly prefers the mastoid-referenced "
        "derivations.",
        body,
    ))
    story.append(Paragraph(
        "<b>(b)</b> Re-reference against a common-average of clean "
        "central channels (e.g. <font face='Courier'>'C3', 'C4', 'Cz', "
        "'CP1', 'CP2'</font>). This gives you research-grade "
        "C3-avg / C4-avg derivations. Example:",
        body,
    ))
    story.append(Paragraph(
        "<font face='Courier'>raw.set_eeg_reference(ref_channels="
        "['C3','C4','Cz','CP1','CP2'])</font>",
        mono,
    ))
    story.append(Paragraph(
        "<b>(c)</b> Use contralateral homologs if the cap still has "
        "clean opposite-side electrodes: e.g. <font face='Courier'>"
        "C3-C4</font>, <font face='Courier'>F3-F4</font>. These aren't "
        "standard AASM but give a bipolar waveform dominated by lateral "
        "asymmetries — often informative for sleep architecture.",
        body,
    ))
    story.append(Paragraph(
        "If any of these are unsuitable for your pipeline, tell us what "
        "you need and we'll re-export with the derivation of your choice "
        "— the original XDF is preserved.",
        body,
    ))
    story.append(Spacer(1, 0.1 * inch))

    story.append(Paragraph(
        "<b>Impedance values (mean across full recording, CGX AIM-2):</b>",
        small,
    ))
    for chan, kohm in CGX_IMP_KOHM.items():
        story.append(Paragraph(
            f"&nbsp;&nbsp;{chan}: <font face='Courier'>{kohm:.1f} kΩ</font>",
            small,
        ))
    story.append(Paragraph(
        "BrainVision actiCAP pre-recording impedance values were not "
        "logged into the XDF by this session; the corresponding saturation "
        "pattern in Section 2 is our best proxy.",
        small,
    ))
    story.append(PageBreak())

    # ---- Section 5: Markers / annotations ----
    story.append(Paragraph("5. Marker / annotation legend", h2))
    if unique_markers:
        story.append(Paragraph(
            f"{len(unique_markers)} unique marker strings in the XDF. "
            "Each is re-emitted as an EDF+ annotation with the same "
            "string at the corresponding LSL timestamp.",
            body,
        ))
        rows = [["#", "Marker string", "Meaning"]]
        for i, m in enumerate(unique_markers):
            meaning = _marker_meaning(m)
            rows.append([str(i + 1), m, meaning])
        tbl = Table(rows, colWidths=[0.35 * inch, 2.5 * inch, 4.3 * inch])
        tbl.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
            ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(tbl)
    else:
        story.append(Paragraph(
            "<b>No markers fired during this recording.</b> The "
            "<font face='Courier'>P013_Task_Markers</font> stream is "
            "empty — this session was a pure sleep recording with the "
            "task suite inactive, so no event annotations are present "
            "in the EDF+. A single <font face='Courier'>"
            "recording_start</font> annotation is emitted at t=0 as a "
            "sanity anchor.",
            body,
        ))
    story.append(Spacer(1, 0.15 * inch))

    # ---- Section 6: Technical notes ----
    story.append(Paragraph("6. Technical notes", h2))

    story.append(Paragraph("<b>Recording system</b>", h3))
    story.append(Paragraph(
        "BrainVision actiCAP 64-channel active-electrode cap driven by a "
        "BrainAmp Standard amplifier (AC-coupled, hardware high-pass "
        "~0.016 Hz = 10 s time constant, resolution 0 = 0.1 µV/LSB, "
        "16-bit signed → ±3276.7 µV input range). Physiological side "
        "channels recorded by a CGX AIM-2 (Ag/AgCl spot electrodes for "
        "EOG and chin EMG, plus onboard ECG/Resp/PPG/SpO₂/GSR/Temp "
        "sensors). Both amplifiers stream via Lab Streaming Layer to "
        "the same LabRecorder instance on a single workstation — XDF "
        "is our native container, EDF+ is the export for scoring.",
        body,
    ))

    story.append(Paragraph("<b>Hardware filters</b>", h3))
    story.append(Paragraph(
        "BrainAmp: AC-coupled, ~0.016 Hz hardware HP, no hardware LP "
        "(anti-alias is handled by the 500 Hz ADC).<br/>"
        "CGX AIM-2: device-internal filters not exposed in stream "
        "metadata; refer to the AIM-2 manual for exact cutoffs.<br/>"
        "<b>No software filters were applied before writing the EDF+.</b> "
        "Apply your scoring pipeline's own band-pass (typically "
        "0.3–35 Hz EEG / 0.3–10 Hz EOG / 10–100 Hz chin EMG) on the "
        "EDF.",
        body,
    ))

    story.append(Paragraph("<b>Known caveats</b>", h3))
    story.append(Paragraph(
        "• Peripheral EEG channels: rail-saturated (see Section 2). "
        "Pilot acquisition compromise.<br/>"
        "• TP9 / TP10 unusable as linked-mastoid references in this "
        "recording (see Section 4 workarounds).<br/>"
        "• CGX non-EEG channels (SpO₂, HR, GSR, Temp., Resp., PPG) "
        "come through the LSL stream with their unit declared as "
        "<i>microvolts</i>. Values on those channels are device-internal "
        "scaling, not physiological µV / % / kΩ. Consult CGX "
        "calibration if you need physical conversion; for sleep "
        "scoring, the absolute scale on these channels doesn't "
        "matter (you read them qualitatively).<br/>"
        "• EDF+ header contains the minimum metadata permitted by "
        "the 80-char combined limit; this document is the "
        "authoritative source for session context.<br/>"
        "• No software pre-processing applied. Re-run from the "
        "preserved XDF if a different export is needed.",
        body,
    ))

    story.append(Paragraph("<b>Diagnostic artefacts generated alongside this EDF</b>", h3))
    story.append(Paragraph(
        "<font face='Courier'>outputs/diagnostics/eeg_first_120s_rms.png</font> — "
        "per-channel RMS heatmap for the first 120 s.<br/>"
        "<font face='Courier'>outputs/diagnostics/eeg_time_course_first_2min.png"
        "</font> — Fp1 / Cz / Oz / TP9 raw traces.<br/>"
        "<font face='Courier'>outputs/diagnostics/psd_by_modality.png</font> — "
        "Welch PSD for Cz / Fp1 / the four AIM-2 EXG derivations over a "
        "clean 60 s segment at t=300 s.<br/>"
        "<font face='Courier'>outputs/diagnostics/psd_data.csv</font> — "
        "the same PSDs in CSV form.<br/>"
        "<font face='Courier'>outputs/diagnostics/line_noise_report.txt"
        "</font> — per-channel 60 / 120 / 180 Hz SNR, broadband ratio, "
        "and impedance snapshot.<br/>"
        "<font face='Courier'>outputs/conversion_log.txt</font> — "
        "assumptions and decisions from the conversion run.",
        small,
    ))

    doc.build(story)
    print(f"wrote {pdf_path}")

    # ----- Subject-specific README (regenerated alongside the PDF) -----
    n_red = sum(1 for lab in labels if lab in BAD_RAIL)
    readme_path.write_text(_README_TEMPLATE.format(
        subject=subject,
        edf_name=edf_path.name,
        manifest_name=pdf_path.name,
        readme_name=readme_path.name,
        duration_min=f"{dur/60:.2f}",
        n_channels=len(labels),
        n_red=n_red,
    ))
    print(f"wrote {readme_path}")
    return 0


_README_TEMPLATE = """\
P013 {subject} — PSG handoff for Dr. Ken Paller
================================================

ONE-LINE ANSWER
---------------
Mastoid = the bony protrusion BEHIND the ear (the mastoid process of the
temporal bone). Yes — that's our EOG reference. E1 referenced to M2,
E2 referenced to M1, at the CGX AIM-2 hardware level, AASM-standard.


CONTENTS
--------
{edf_name}              EDF+, {n_channels} channels, 500 Hz, {duration_min} min
{manifest_name}    Numbered channel table + scoring notes
{readme_name}              This file (quickstart)
P013_{subject}_conversion_log.txt    Decisions + assumptions from conversion

diagnostics/
  eeg_first_120s_rms.png             Peripheral saturation heatmap
  eeg_time_course_first_2min.png     Raw traces of Fp1 / Cz / Oz / TP9
  psd_by_modality.png                PSD of key channels (60 s, t=300)
  psd_data.csv                       PSDs as CSV
  line_noise_report.txt              60/120/180 Hz SNR per channel


HOW TO OPEN
-----------
Any EDF+ reader works. Tested:
  - EDFbrowser (free, cross-platform)   https://www.teuniz.net/edfbrowser/
  - Polyman / WonamBi                   (your existing scoring workflow)
  - MNE-Python:   mne.io.read_raw_edf("{edf_name}")
  - EEGLAB:       pop_biosig("{edf_name}")


RECOMMENDED SCORING CHANNELS
----------------------------
E1-M2                left EOG  (AASM)
E2-M1                right EOG (AASM)
ChinZ-Chin1          primary chin EMG
Chin2-Chin1prime     backup chin EMG

Cap EEG: Cz, C3, C4, Fz, F3, F4, Pz, P3, P4 are typically clean; see
Section 2 of the manifest PDF for this recording's per-channel quality
tally ({n_red} rail-saturated channels flagged RED in this run).

TP9 / TP10 mastoid proxies: check Section 2 — if they're flagged RED,
use one of the three workarounds in Section 4 (monopolar-to-FCz,
common-average, or contralateral homologs).


CONTACT
-------
Nicco Reggente, Ph.D.
Institute for Advanced Consciousness Studies
nicco@advancedconsciousness.org
"""


def _marker_meaning(m: str) -> str:
    """Translate a P013 marker string to a human-readable description."""
    if m == "session_start":
        return "Session wall-clock start"
    if m == "session_end":
        return "Session wall-clock end"
    if m == "session_abort":
        return "Session aborted (Ctrl+C / Escape)"
    if m.startswith("participant_id:"):
        return f"Participant identifier: {m.split(':', 1)[1]}"
    if m.startswith("task01"):
        return "Auditory Oddball (P300) — task / phase / tone / response event"
    if m.startswith("task02"):
        return "RGB Illuminance (null-hypothesis decoder) — color / ITI / break event"
    if m.startswith("task03"):
        return "Backward Masking (QUEST) — fixation / face / mask / response / SOA event"
    if m.startswith("task04"):
        return "Mind-State Switching — game / break / meditation event"
    if m.startswith("task05"):
        return "SSVEP Ramp — instruction / ramp / overlay event"
    if m.startswith("{") and '"event"' in m:
        return "Vayl JSON event (ramp_start / ramp_stop / overlay_off with wallTimeMs)"
    return "(see README or P013 spec)"


if __name__ == "__main__":
    raise SystemExit(main())
