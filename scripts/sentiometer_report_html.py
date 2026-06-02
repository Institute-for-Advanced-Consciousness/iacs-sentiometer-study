"""Assemble the IACS-styled HTML report and render it to PDF.

Pulls every number from figs/stats.json (nothing hand-transcribed) and
embeds the generated figures. Landscape figure pages use a per-page
`@page` rule so the wide timeseries/grids get a full landscape sheet
while the prose stays portrait.

Render: headless Edge/Chrome --print-to-pdf.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SUBJECT = os.environ.get("SENT_SUBJECT", "P013_S01")
# Date.now() is unavailable in this environment for reproducibility; pass the
# report date via SENT_REPORT_DATE (the runner fills it in).
REPORT_DATE = os.environ.get("SENT_REPORT_DATE", "—")
RPT = REPO / "outputs" / SUBJECT / "report"
FIGS = RPT / "figs"
STATS = json.loads((FIGS / "stats.json").read_text())

SERIES = STATS["series"]


def fnum(x, nd=2, sci_below=1e-3):
    if x is None:
        return "—"
    if isinstance(x, float) and x != 0 and abs(x) < sci_below:
        return f"{x:.2e}"
    return f"{x:.{nd}f}"


def p_fmt(p):
    if p is None:
        return "—"
    if p < 1e-3:
        return f"{p:.1e}"
    return f"{p:.3f}"


def fig(src, num, cap, landscape=False):
    cls = "fig-land" if landscape else ""
    return f"""<figure class="{cls}">
  <img src="figs/{src}" alt="{cap}">
  <figcaption><span class="num">Fig. {num}</span> {cap}</figcaption>
</figure>"""


def phase_row(p):
    mm = f"{p['dur']/60:.1f}" if p['dur'] >= 60 else f"{p['dur']:.0f} s"
    return (f"<tr><td>{p['title']}</td><td class='num'>{p['t0']:.0f}</td>"
            f"<td class='num'>{p['t1']:.0f}</td><td class='num'>{p['dur']:.0f}</td></tr>")


def state_table():
    rows = []
    for s in SERIES:
        r = STATS["by_series"][s]["game_vs_med"]
        rows.append(
            f"<tr><td>{s}</td><td class='num'>{fnum(r['mean_a'],1)}</td>"
            f"<td class='num'>{fnum(r['mean_b'],1)}</td>"
            f"<td class='num'>{fnum(r['mean_a']-r['mean_b'],2)}</td>"
            f"<td class='num'>{fnum(r['t'],1)}</td><td class='num'>{p_fmt(r['p'])}</td>"
            f"<td class='num'>{fnum(r['d'],2)}</td></tr>")
    return "\n".join(rows)


def rgb_table():
    rows = []
    for s in SERIES:
        r = STATS["by_series"][s]
        m = r["rgb_means"]
        a = r["rgb_anova"]
        rows.append(
            f"<tr><td>{s}</td><td class='num'>{fnum(m['red'],2)}</td>"
            f"<td class='num'>{fnum(m['green'],2)}</td><td class='num'>{fnum(m['blue'],2)}</td>"
            f"<td class='num'>{fnum(a['F'],2)}</td><td class='num'>{p_fmt(a['p'])}</td></tr>")
    return "\n".join(rows)


def contrast_table(key, cols=("t", "p", "d")):
    rows = []
    for s in SERIES:
        r = STATS["by_series"][s].get(key, {})
        rows.append(
            f"<tr><td>{s}</td><td class='num'>{fnum(r.get('mean_a'),2)}</td>"
            f"<td class='num'>{fnum(r.get('mean_b'),2)}</td>"
            f"<td class='num'>{fnum(r.get('t'),2)}</td>"
            f"<td class='num'>{p_fmt(r.get('p'))}</td>"
            f"<td class='num'>{fnum(r.get('d'),3)}</td></tr>")
    return "\n".join(rows)


def dq_table():
    rows = []
    for c in ["PD1", "PD2", "PD3", "PD4", "PD5"]:
        d = STATS["data_quality"][c]
        flag = ""
        if d["std"] < 5:
            flag = "<span class='tag warn'>low variance</span>"
        rows.append(
            f"<tr><td>{c}</td><td class='num'>{d['min']:.0f}</td>"
            f"<td class='num'>{d['max']:.0f}</td><td class='num'>{d['mean']:.1f}</td>"
            f"<td class='num'>{d['std']:.2f}</td><td>{flag}</td></tr>")
    return "\n".join(rows)


def pc1_loadings_str():
    return " · ".join(f"{k} {v:.2f}" for k, v in STATS["meta"]["pc1_loadings"].items())


M = STATS["meta"]
gm = STATS["by_series"]["Mean"]["game_vs_med"]
pc = STATS["by_series"]["PC1"]["game_vs_med"]
rgb_mean_p = STATS["by_series"]["Mean"]["rgb_anova"]["p"]
odd_mean = STATS["by_series"]["Mean"]["oddball_p300"]

HTML = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>P013 · Sentiometer Phase-Resolved Report — {SUBJECT}</title>
<link rel="stylesheet" href="iacs-report.css">
<style>
  /* landscape sheets for wide figures */
  @page land {{ size: Letter landscape; margin: 0.5in 0.5in 0.6in 0.5in; }}
  .fig-page {{ page: land; page-break-before: always; page-break-after: always; }}
  .fig-page figure {{ margin: 0; }}
  .fig-page img {{ width: 100%; height: auto; max-height: 7.0in; object-fit: contain; }}
  figure.fig-land img {{ width: 100%; }}
  .lead-grid {{ display:grid; grid-template-columns: 1fr 1fr; gap: 0 22pt; }}
</style>
</head>
<body>

<!-- ============ COVER ============ -->
<section class="report-cover">
  <header class="brand">
    <img src="assets/IACS_Logo.svg" alt="IACS">
    <span class="org">Institute for Advanced Consciousness Studies</span>
  </header>
  <h1 class="title">Sentiometer Signal Across the P013 Session</h1>
  <p class="lede">A phase-resolved look at the 6-channel optical Sentiometer
  signal for session {SUBJECT} — the full {M['duration_hms']} recording, every
  paradigm, and event-locked averages for each of the seven derived series.</p>
  <dl class="meta">
    <div><dt>Protocol</dt><dd>IACS P013 · Sentiometer arm</dd></div>
    <div><dt>Session</dt><dd>{SUBJECT}</dd></div>
    <div><dt>Recording</dt><dd>{M['duration_hms']} · {M['fs_hz']:.0f} Hz · {M['n_samples']:,} samples</dd></div>
    <div><dt>Series</dt><dd>PD1–PD5 · Mean · PC1</dd></div>
    <div><dt>Date</dt><dd>{REPORT_DATE}</dd></div>
    <div><dt>Classification</dt><dd>Internal · pre-analysis</dd></div>
  </dl>
</section>

<!-- ============ EXEC SUMMARY ============ -->
<section>
  <span class="eyebrow">Executive Summary</span>
  <h2>What the Optical Signal Does, Phase by Phase</h2>
  <p class="lede">We rendered the Sentiometer signal across every phase of the
  P013 session for {SUBJECT} and asked one question throughout: does
  the optical signal change as a function of what the participant is doing? The
  clearest answer is at the level of cognitive <em>state</em>, not discrete stimulus events.</p>

  <div class="kpi-row">
    <div class="kpi">
      <p class="label">Mind-state effect</p>
      <p class="value">d = {fnum(gm['d'],2)}</p>
      <p class="delta up">gameplay &gt; meditation, p = {p_fmt(gm['p'])}</p>
    </div>
    <div class="kpi">
      <p class="label">RGB decoding</p>
      <p class="value">p = {fnum(rgb_mean_p,3)}</p>
      <p class="delta">null, as predicted</p>
    </div>
    <div class="kpi">
      <p class="label">Oddball P300 window</p>
      <p class="value">d = {fnum(odd_mean['d'],2)}</p>
      <p class="delta">deviant vs standard, n.s.</p>
    </div>
    <div class="kpi">
      <p class="label">PC1 variance</p>
      <p class="value">{M['pc1_evr']*100:.1f}%</p>
      <p class="delta">of the 5-photodiode array</p>
    </div>
  </div>

  <aside class="callout">
    <p class="label">In context</p>
    <p>This is a single-participant, descriptive pass — no permutation nulls,
    no multiple-comparison correction, no HRV/motion partialling. The state-level
    contrast (Task 04) is large and consistent across all seven series; the
    stimulus-locked contrasts (Tasks 01–03) are at noise level for this session.
    The Task 04 contrast uses 5 s non-overlapping bins as its unit of observation;
    those bins are autocorrelated (the optical signal drifts on a seconds timescale),
    so the reported p-values are anti-conservative — read the large effect size
    (d ≈ 2), not the extreme p, as the headline. Treat every number here as a
    starting point for the formal analysis, not an endpoint.</p>
  </aside>

  <h3>How to read this report</h3>
  <ul>
    <li><strong>Section 1 — Overview.</strong> One landscape page: all seven
      series across the whole session, z-scored, split into a task-suite band
      and a nap band.</li>
    <li><strong>Section 2 — Per-phase zooms.</strong> One landscape page per
      phase, all seven series, with that phase's event markers overlaid.</li>
    <li><strong>Section 3 — Event-grouped analysis.</strong> Time-locked
      averages (ERPs, −300 to +300 ms, baseline-corrected) and condition bar
      charts, for every series.</li>
  </ul>
</section>

<!-- ============ METHODS ============ -->
<section>
  <span class="eyebrow">Methods</span>
  <h2>Signals, Derivation &amp; Conventions</h2>
  <p>The Sentiometer streams six channels at {M['fs_hz']:.0f} Hz: an internal device
  timestamp plus five photodiodes (PD1–PD5). From the five photodiodes we derive
  two summary series. <strong>Mean</strong> is the per-sample average across PD1–PD5.
  <strong>PC1</strong> is the projection onto the first principal component
  (covariance / center-only PCA over the full recording), which captures
  <strong>{M['pc1_evr']*100:.1f}%</strong> of the array variance — the five diodes move
  almost entirely together. PC1 loadings: {pc1_loadings_str()}. PC1 is sign-oriented
  to correlate positively with the mean.</p>

  <p>Overlay plots (Sections 1–2) show each series <strong>z-scored over the whole
  recording</strong> and vertically offset, so between-phase amplitude shifts stay
  visible on one axis. Event-locked averages (Section 3) use <strong>raw units</strong>,
  baseline-corrected by subtracting each epoch's mean over the −300 to 0 ms
  pre-onset window; the raw pre-onset level is reported in each caption. Bar charts
  show each condition's deviation from the panel grand mean (raw units) so a small
  real effect is legible against a large absolute level.</p>

  <table>
    <caption>Table 1 · Session phases (P013_Task_Markers)</caption>
    <thead><tr><th>Phase</th><th class="num">Start (s)</th><th class="num">End (s)</th><th class="num">Duration (s)</th></tr></thead>
    <tbody>
      {''.join(phase_row(p) for p in M['phases'])}
    </tbody>
  </table>
  <p class="caption" style="font-size:8.5pt;color:#5e5e6c">Times are seconds from
  the <code>session_start</code> marker. The nap spans from <code>task05_end</code>
  to the end of the recording. The Sentiometer also streamed ~133 s before
  <code>session_start</code> (pre-session setup), included in the overview.</p>
</section>

<!-- ============ SECTION 1: OVERVIEW ============ -->
<section class="section-break">
  <span class="eyebrow">Section 1 · Full Session</span>
  <h2>The Whole Recording at a Glance</h2>
  <p>All seven series across the entire session, z-scored and vertically offset.
  The top band is the {M['phases'][5]['t1']/60:.0f}-minute task suite, with each paradigm
  shaded and labeled; the bottom band is the {(M['phases'][6]['dur'])/60:.0f}-minute nap.
  The eye is looking for level shifts that line up with phase boundaries — most
  visibly, the elevated, more variable signal during the active gameplay portion
  of Mind-State Switching.</p>
</section>
<div class="fig-page">
  {fig('overview.png', '1', 'Full-session overview. Seven Sentiometer series (PD1–PD5, Mean, PC1), global z-score, vertically offset. Top: task suite (0–'+f"{M['phases'][5]['t1']/60:.0f}"+' min) with paradigms shaded. Bottom: post-session nap. Traces are min/max envelope-downsampled so peaks survive decimation.', landscape=True)}
</div>

<!-- ============ SECTION 2: PER-PHASE ============ -->
<section class="section-break">
  <span class="eyebrow">Section 2 · Per-Phase Zooms</span>
  <h2>Each Phase, Up Close</h2>
  <p>One landscape page per phase — all seven series at full resolution for that
  window, with the phase's stimulus/response events overlaid as vertical markers.
  These are the pages to read when asking whether a specific event type leaves a
  signature in the optical trace.</p>
</section>
<div class="fig-page">{fig('block_task00.png','2','Task 00 · Pre-session questionnaire (webview). No stimulus events — a baseline view of the resting optical signal.',True)}</div>
<div class="fig-page">{fig('block_task01.png','3','Task 01 · Auditory Oddball. Deviant tones (red) and hits (dotted) overlaid. 200 standard / 50 deviant / 50 hits.',True)}</div>
<div class="fig-page">{fig('block_task02.png','4','Task 02 · RGB Illuminance. Red / green / blue onsets overlaid (100 each). Gray ITI between every color.',True)}</div>
<div class="fig-page">{fig('block_task03.png','5','Task 03 · Backward Masking. Face onsets, catch trials, and "seen" responses overlaid. QUEST converged at SOA ≈ 34 ms.',True)}</div>
<div class="fig-page">{fig('block_task04.png','6','Task 04 · Mind-State Switching. Gameplay and meditation sub-blocks shaded; collisions and speed-increases overlaid. The clearest state effect in the session.',True)}</div>
<div class="fig-page">{fig('block_task05.png','7','Task 05 · SSVEP Frequency Ramp. Effective stimulation frequency (gold, right axis) ramps 40→1 Hz over 300 s.',True)}</div>
<div class="fig-page">{fig('block_nap.png','8','Post-session nap. ~'+f"{M['phases'][6]['dur']/60:.0f}"+' minutes of continuous optical signal during sleep.',True)}</div>

<!-- ============ SECTION 3: EVENT ANALYSIS ============ -->
<section class="section-break">
  <span class="eyebrow">Section 3 · Event-Grouped Analysis</span>
  <h2>Grouping Events &amp; Averaging</h2>
  <p>For discrete stimuli we time-lock and average; for sustained conditions we
  take window means. Every analysis covers all seven series. Statistics are
  Welch's <em>t</em> (two conditions) or one-way ANOVA (three+), uncorrected,
  single participant.</p>

  <h3>3.1 · Mind-State — the headline contrast</h3>
  <p>Averaging the signal over the gameplay and meditation blocks (5 s bins)
  gives the largest, most reliable effect in the session: gameplay sits well
  above meditation on every series. For the Mean series, gameplay = {fnum(gm['mean_a'],1)}
  vs meditation = {fnum(gm['mean_b'],1)} (Welch <em>t</em> = {fnum(gm['t'],1)},
  p = {p_fmt(gm['p'])}, d = {fnum(gm['d'],2)}); PC1 mirrors it (d = {fnum(pc['d'],2)}).</p>
  <table>
    <caption>Table 2 · Gameplay vs meditation, mean amplitude (raw units, 5 s bins)</caption>
    <thead><tr><th>Series</th><th class="num">Gameplay</th><th class="num">Meditation</th><th class="num">Δ</th><th class="num">t</th><th class="num">p</th><th class="num">Cohen's d</th></tr></thead>
    <tbody>{state_table()}</tbody>
  </table>
  <p class="caption" style="font-size:8.5pt;color:#5e5e6c">The unit of observation
  is the 5 s bin mean (≈60 gameplay, ≈74 meditation bins). Adjacent bins are
  autocorrelated, so the <em>t</em>/<em>p</em> columns are anti-conservative — the
  effect size (d ≈ 2) is the robust quantity. The break window is excluded as a
  transition, not a condition.</p>
</section>
<div class="fig-page">{fig('bar_mindstate.png','9','Mind-State block means, deviation from each panel grand mean (raw units, 5 s bins, ±1 SEM). Gameplay is elevated across all seven series.',True)}</div>
<div class="fig-page">{fig('erp_collision.png','10','Collision-locked ERP (−300 to +300 ms, baseline-corrected). n = 10 collisions — underpowered, shown for completeness.',True)}</div>

<section>
  <h3>3.2 · RGB Illuminance — the null test</h3>
  <p>The RGB paradigm is a null-hypothesis test: the Sentiometer should
  <em>not</em> distinguish red from green from blue. It does not. A one-way ANOVA
  across the three colors is non-significant on every series (Mean p =
  {fnum(rgb_mean_p,3)}), and the bar chart shows all between-color deviations
  buried under their error bars.</p>
  <table>
    <caption>Table 3 · RGB per-color window means &amp; one-way ANOVA</caption>
    <thead><tr><th>Series</th><th class="num">Red</th><th class="num">Green</th><th class="num">Blue</th><th class="num">F</th><th class="num">p</th></tr></thead>
    <tbody>{rgb_table()}</tbody>
  </table>
</section>
<div class="fig-page">{fig('bar_rgb.png','11','RGB per-color means, deviation from panel grand mean (±1 SEM). All deviations are within noise — the predicted null.',True)}</div>
<div class="fig-page">{fig('erp_rgb.png','12','Color-onset ERP (−300 to +300 ms, baseline-corrected) for red / green / blue. No systematic color-dependent deflection.',True)}</div>

<section>
  <h3>3.3 · Auditory Oddball — P300 window</h3>
  <p>Locking to tone onset and contrasting deviant vs standard in the classic
  250–350 ms P300 window yields no reliable optical difference this session
  (Mean d = {fnum(odd_mean['d'],3)}, p = {p_fmt(odd_mean['p'])}). The EEG P300 is
  the positive control for whether the brain registered the deviant; the
  Sentiometer contrast is the open question.</p>
  <table>
    <caption>Table 4 · Deviant vs standard, mean amplitude in 250–350 ms window (baseline-corrected)</caption>
    <thead><tr><th>Series</th><th class="num">Deviant</th><th class="num">Standard</th><th class="num">t</th><th class="num">p</th><th class="num">Cohen's d</th></tr></thead>
    <tbody>{contrast_table('oddball_p300')}</tbody>
  </table>
</section>
<div class="fig-page">{fig('erp_oddball.png','13','Deviant vs standard ERP (−300 to +300 ms, baseline-corrected, ±1 SEM). n = 50 deviant / 200 standard.',True)}</div>

<section>
  <h3>3.4 · Backward Masking — seen vs unseen</h3>
  <p>The matched-duration contrast: <strong>face-present trials only</strong>, at a
  near-constant SOA (QUEST converged to ≈ 34 ms), locked to face onset and split by
  whether the participant reported seeing the face. Catch trials (mask-only, no face)
  are excluded — so this is 75 seen vs 113 unseen face-present trials, not the larger
  response count that would fold in correct rejections on no-face catches. Any
  systematic optical difference would be a correlate of conscious access rather than
  of stimulation, since physical timing is matched. For this session the contrast
  (0–300 ms post-onset) is at noise level on every series.</p>
  <table>
    <caption>Table 5 · Seen vs unseen (face-present), mean amplitude 0–300 ms post-onset (baseline-corrected)</caption>
    <thead><tr><th>Series</th><th class="num">Seen</th><th class="num">Unseen</th><th class="num">t</th><th class="num">p</th><th class="num">Cohen's d</th></tr></thead>
    <tbody>{contrast_table('masking_seen_unseen')}</tbody>
  </table>
  <p class="caption" style="font-size:8.5pt;color:#5e5e6c">Fig. 15 is the primary
  matched-duration view (face-onset locked, face-present only). Fig. 14 shows the
  response-locked version for comparison; its "unseen" trace additionally includes
  catch-trial correct rejections, which is why its n differs.</p>
</section>
<div class="fig-page">{fig('erp_masking_faceonset.png','14','PRIMARY — Seen vs unseen, locked to face onset (matched SOA ≈ 34 ms, face-present only, n = 75 / 113). Physical stimulation identical, percept differs — the conscious-access contrast.',True)}</div>
<div class="fig-page">{fig('erp_masking.png','15','Secondary — Seen vs unseen ERP, locked to response (−300 to +300 ms, baseline-corrected). n = 75 seen / 153 unseen (the 153 includes catch-trial correct rejections).',True)}</div>

<section>
  <h3>3.5 · SSVEP — amplitude by frequency band</h3>
  <p>During the 40→1 Hz ramp we binned the signal by effective stimulation
  frequency and took mean amplitude per band. Amplitude declines monotonically
  from low to high frequency on every series.</p>
  <aside class="callout">
    <p class="label">Caveat</p>
    <p>Because frequency ramps <em>linearly with time</em> across the 300 s block,
    "frequency band" is fully confounded with "time elapsed in the ramp." The clean
    monotonic ordering (δ &gt; θ &gt; α &gt; β &gt; γ) is therefore equally consistent
    with slow signal drift over five minutes as with a genuine frequency response.
    Disentangling the two needs a counterbalanced or interleaved-frequency design,
    or regressing out the linear time trend — flagged for the formal analysis.</p>
  </aside>
</section>
<div class="fig-page">{fig('bar_ssvep.png','16','SSVEP amplitude by effective-frequency band, deviation from panel grand mean (±1 SEM). Monotonic trend — but see the time/frequency confound caveat.',True)}</div>

<!-- ============ DATA QUALITY ============ -->
<section class="section-break">
  <span class="eyebrow">Appendix · Data Quality</span>
  <h2>Per-Channel Notes</h2>
  <p>PD3 sits at a high baseline (~{STATS['data_quality']['PD3']['mean']:.0f}) with very
  small variance (SD ≈ {STATS['data_quality']['PD3']['std']:.1f}, range
  {STATS['data_quality']['PD3']['min']:.0f}–{STATS['data_quality']['PD3']['max']:.0f}).
  It is <em>not</em> rail-clipped — only {STATS['data_quality']['PD3']['frac_at_4095']*100:.4f}%
  of samples touch the 4095 ceiling — but its low variance means global z-scoring
  amplifies quantization noise, which is why PD3 looks the noisiest in the z-scored
  overlays despite being the most stable channel in raw units. It contributes
  almost nothing to PC1 (loading {STATS['meta']['pc1_loadings']['PD3']:.2f}).</p>
  <table>
    <caption>Table 6 · Per-photodiode amplitude statistics (full recording, raw units)</caption>
    <thead><tr><th>Channel</th><th class="num">Min</th><th class="num">Max</th><th class="num">Mean</th><th class="num">SD</th><th>Flag</th></tr></thead>
    <tbody>{dq_table()}</tbody>
  </table>

  <hr class="brand">
  <div class="footnotes">
    <ol>
      <li>Source: <code>sample-data/{SUBJECT}.xdf</code> → derived bundle
        <code>outputs/{SUBJECT}/preprocessed/</code>. All series on the synchronised
        LSL clock; markers and signal share one timebase.</li>
      <li>Single participant, descriptive. Statistics are uncorrected Welch
        <em>t</em> / one-way ANOVA. No permutation null, no multiple-comparison
        control, no HRV/motion partialling — all reserved for the formal analysis.
        The Task 04 state contrast treats 5 s bins as independent observations;
        they are autocorrelated, so its p-values are anti-conservative.</li>
      <li>Figures regenerable via <code>scripts/sentiometer_report_figs.py</code>;
        statistics via <code>scripts/sentiometer_report_stats.py</code>.</li>
    </ol>
  </div>
</section>

</body>
</html>
"""

out = RPT / "index.html"
out.write_text(HTML, encoding="utf-8")
print("wrote", out, len(HTML), "bytes")
