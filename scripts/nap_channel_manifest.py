"""Build the shared one-page PSG channel manifest PDF for the P013 nap handoff.

One document, identical for every participant (the montage is the same). Reads
the channel labels from a nap EDF and the per-file durations from the nap
sidecars, renders a clean IACS-styled one-pager, and prints it to PDF with
headless Edge.

  uv run python scripts/nap_channel_manifest.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUTROOT = REPO / "outputs" / "_paller_nap"
DIAG = OUTROOT / "diagnostics"

EDGE = [Path(r"C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
        Path(r"C:/Program Files/Microsoft/Edge/Application/msedge.exe"),
        Path(r"C:/Program Files/Google/Chrome/Application/chrome.exe")]

# Peripheral channel descriptions (CGX AIM-2, P013 montage). Stable montage.
PERIPH = {
    "E1-M2": ("EOG (left)", "Left outer canthus &rarr; right mastoid", "&micro;V"),
    "E2-M1": ("EOG (right)", "Right outer canthus &rarr; left mastoid", "&micro;V"),
    "ChinZ-Chin1": ("Chin EMG", "Submental midline, bipolar (primary)", "&micro;V"),
    "Chin2-Chin1prime": ("Chin EMG", "Submental lateral, bipolar (backup)", "&micro;V"),
    "ECG": ("ECG", "Single-lead cardiac (AIM-2)", "&micro;V*"),
    "Resp.": ("Respiration", "Thoracic bio-impedance", "&micro;V*"),
    "PPG": ("PPG", "Photoplethysmograph (pulse)", "&micro;V*"),
    "GSR": ("EDA / GSR", "Electrodermal (uncalibrated device units)", "&micro;V*"),
}


def find_browser():
    for p in EDGE:
        if p.exists():
            return p
    sys.exit("No headless Edge/Chrome found.")


def main():
    import pyedflib
    edfs = sorted(OUTROOT.glob("P013_*_nap.edf"))
    if not edfs:
        sys.exit("No nap EDFs found — run nap_export.py first.")
    r = pyedflib.EdfReader(str(edfs[0]))
    labels = r.getSignalLabels()
    r.close()
    eeg = labels[:64]
    periph = labels[64:]

    # per-file durations from sidecars
    files = []
    for e in edfs:
        subj = e.stem.replace("_nap", "")
        sc = DIAG / f"{subj}_nap.json"
        meta = json.loads(sc.read_text()) if sc.exists() else {}
        files.append((subj, e.name, meta.get("nap_minutes"),
                      meta.get("settle_rel_min"), meta.get("removal_rel_min")))

    # EEG grid (numbered, acquisition order)
    cells = "".join(
        f'<span class="ch"><b>{i+1}</b>&nbsp;{lab}</span>' for i, lab in enumerate(eeg))
    # peripheral rows (channels 65..72)
    prows = ""
    for k, lab in enumerate(periph):
        typ, deriv, unit = PERIPH.get(lab, ("&mdash;", "&mdash;", "&micro;V"))
        prows += (f"<tr><td class='num'>{65+k}</td><td><code>{lab}</code></td>"
                  f"<td>{typ}</td><td>{deriv}</td><td>{unit}</td></tr>")
    frows = ""
    for subj, fn, mins, s0, s1 in files:
        mm = f"{mins:.0f} min" if mins is not None else "&mdash;"
        frows += (f"<tr><td><code>{fn}</code></td><td class='num'>{mm}</td>"
                  f"<td class='num'>{s0:.1f}&ndash;{s1:.1f} min</td></tr>")

    css = REPO / "outputs" / "_group" / "sentiometer" / "report" / "iacs-report.css"
    css_link = '<link rel="stylesheet" href="iacs-report.css">' if css.exists() else ""

    html = f"""<!doctype html><html><head><meta charset="utf-8">
<title>P013 Nap PSG &mdash; Channel Manifest</title>{css_link}
<style>
  @page {{ size: Letter portrait; margin: 0.45in; }}
  body {{ font-size: 9pt; line-height: 1.2; }}
  h1 {{ margin: 0 0 1pt; font-size: 15pt; }}
  .sub {{ color:#5e5e6c; margin:0 0 6pt; font-size:8.7pt; }}
  .eyebrow {{ letter-spacing:.08em; text-transform:uppercase; font-size:7.4pt;
             color:#5d5179; font-weight:700; margin-top:7pt; margin-bottom:1pt; }}
  .box {{ border:1px solid #d9d9e0; border-radius:5px; padding:5pt 8pt; margin-top:2pt; }}
  .specline {{ font-size:8.6pt; }}
  .specline b {{ color:#141437; }}
  .grid {{ display:flex; flex-wrap:wrap; gap:2pt 3pt; margin-top:5pt; }}
  .ch {{ display:inline-block; width:11.6%; font-size:7.5pt; padding:1.2pt 2pt;
         border:1px solid #ededf1; border-radius:3px; background:#faf8f4; }}
  .ch b {{ color:#9a9aab; font-size:6.4pt; }}
  table {{ width:100%; border-collapse:collapse; margin-top:2pt; font-size:8.5pt; }}
  th, td {{ padding:1.4pt 5pt !important; line-height:1.1 !important;
            border-bottom:1px solid #f0f0f3; background:#fff !important; }}
  th {{ text-align:left; border-bottom:1px solid #d9d9e0; color:#5e5e6c; font-weight:600; }}
  td.num, th.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
  code {{ font-size:8.3pt; }}
  .note {{ color:#5e5e6c; font-size:7.8pt; margin-top:3pt; }}
  .ref {{ display:inline-block; background:#5d5179; color:#fff; border-radius:3px;
          padding:1pt 6pt; font-weight:700; margin-right:6pt; font-size:8.3pt; }}
</style></head><body>

<h1>P013 Nap PSG &mdash; Channel Manifest</h1>
<p class="sub">Raw overnight-nap montage for sleep scoring. <b>Identical for all
participants</b> ({', '.join(s for s,_,_,_,_ in files)}). No filtering, re-referencing,
or other preprocessing &mdash; EDF+, 500&nbsp;Hz, one continuous epoch per file.</p>

<div class="eyebrow">Scalp EEG &mdash; channels 1&ndash;64</div>
<div class="box">
  <div class="specline"><span class="ref">REF&nbsp;FCz</span><span class="ref">GND&nbsp;AFz</span>
  <b>64</b> ch &middot; 10&ndash;20 / 10&ndash;10 &middot; BrainVision actiCAP Ag/AgCl &middot;
  &micro;V &middot; 500&nbsp;Hz &middot; AC-coupled (~0.016&nbsp;Hz HP) &middot;
  online reference <b>FCz</b>, ground <b>AFz</b> (neither is a recorded channel).</div>
  <div class="grid">{cells}</div>
</div>

<div class="eyebrow">Peripheral &mdash; CGX AIM-2 (EOG / EMG / ECG / autonomic), channels 65&ndash;72</div>
<table>
  <thead><tr><th class="num">#</th><th>EDF label</th><th>Type</th><th>Derivation / placement</th><th>Unit</th></tr></thead>
  <tbody>{prows}</tbody>
</table>
<p class="note">EOG and chin-EMG are pre-referenced bipolar derivations (AASM montage),
immune to scalp-cap reference. <b>*</b> ECG / Resp / PPG / GSR carry device-internal
scaling reported as &micro;V (not calibrated &micro;V); use waveform shape, not absolute
amplitude. Peripheral channels are resampled onto the EEG clock (sample-aligned).</p>

<div class="eyebrow">Not included</div>
<p class="note">SpO&#8322;, HR, Temp &mdash; not populated this session (flat).
EDA / GSR &mdash; uncalibrated device offset overflows the EDF numeric range (kept in the
source XDF). Packet counter &amp; trigger &mdash; housekeeping.</p>

<div class="eyebrow">Files &amp; nap epoch</div>
<table>
  <thead><tr><th>File</th><th class="num">Duration</th><th class="num">Kept window (from task end)</th></tr></thead>
  <tbody>{frows}</tbody>
</table>
<p class="note">Each epoch is the settled nap, auto-bookended by a movement detector:
the span between the participant settling in bed and movement resuming (gear removal) /
recording end. Movement <i>during</i> the nap is retained. Per-file segmentation plots
accompany the bundle.</p>

</body></html>"""

    out_html = OUTROOT / "P013_channel_manifest.html"
    out_html.write_text(html, encoding="utf-8")
    if css.exists():
        (OUTROOT / "iacs-report.css").write_bytes(css.read_bytes())
    pdf = OUTROOT / "P013_channel_manifest.pdf"
    browser = find_browser()
    uri = "file:///" + str(out_html).replace("\\", "/")
    subprocess.run([str(browser), "--headless", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={pdf}", uri], check=True)
    print(f"wrote {pdf} ({pdf.stat().st_size/1024:.0f} KB)")


if __name__ == "__main__":
    main()
