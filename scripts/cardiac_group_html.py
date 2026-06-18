"""Assemble the IACS-styled GROUP cardiac/HRV report HTML and render to PDF.

Mirrors sentiometer_group_html.py. Numbers from the cardiac group stats.json;
the cross-signal section uses the Fisher-z mean-r matrices from the figs manifest.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
REPORT_DATE = os.environ.get("SENT_REPORT_DATE", "—")
RPT = REPO / "outputs" / "_group" / "cardiac" / "report"
FIGS = RPT / "figs"
STATS = json.loads((FIGS / "stats.json").read_text())
MAN = json.loads((FIGS / "figs_manifest.json").read_text())
XCORR = MAN.get("xcorr")

SUBJECTS = STATS["subjects"]
METRICS = STATS["metrics"]
ULAB = STATS["metric_labels"]
UNIT = STATS["metric_units"]
PHASES = STATS["phases"]
NS = len(SUBJECTS)
PHASE_TITLES = {"task00": "Questionnaire", "task01": "Oddball", "task02": "RGB",
                "task03": "Masking", "task04": "Mind-State", "task05": "SSVEP", "nap": "Nap"}
SENT_COLS = ["PD1", "PD2", "PD3", "PD4", "PD5", "Mean", "PC1"]


def fnum(x, nd=2):
    if x is None or (isinstance(x, float) and x != x):
        return "—"
    if isinstance(x, float) and x != 0 and abs(x) < 1e-3:
        return f"{x:.2e}"
    return f"{x:.{nd}f}"


def p_fmt(p):
    if p is None or (isinstance(p, float) and p != p):
        return "—"
    return f"{p:.1e}" if p < 1e-3 else f"{p:.3f}"


def fig(src, num, cap, landscape=True):
    cls = "fig-land" if landscape else ""
    return (f'<figure class="{cls}"><img src="figs/{src}" alt="{cap}">'
            f'<figcaption><span class="num">Fig. {num}</span> {cap}</figcaption></figure>')


def gvm_table():
    head = ("<tr><th>Metric</th>"
            + "".join(f"<th class='num'>{s.replace('P013_','')} d</th>" for s in SUBJECTS)
            + "<th class='num'>mean d</th><th class='num'>SD d</th>"
            + "<th class='num'>RE p</th><th>dir.</th></tr>")
    rows = []
    for c in METRICS:
        a = STATS["game_vs_med"][c]
        cells = "".join(f"<td class='num'>{fnum((a['per_subject'].get(s) or {}).get('d'),2)}</td>"
                        for s in SUBJECTS)
        sign = ("<span class='tag'>consistent</span>" if a.get("same_sign")
                else "<span class='tag warn'>mixed</span>")
        rows.append(f"<tr><td>{ULAB[c]} ({UNIT[c]})</td>{cells}"
                    f"<td class='num'>{fnum(a.get('mean_d'),2)}</td>"
                    f"<td class='num'>{fnum(a.get('sd_d'),2)}</td>"
                    f"<td class='num'>{p_fmt(a.get('p'))}</td><td>{sign}</td></tr>")
    return head, "\n".join(rows)


def phase_table():
    head = "".join(f"<th class='num'>{ULAB[c]}</th>" for c in METRICS)
    rows = []
    for pk in PHASES:
        cells = []
        for c in METRICS:
            gm = STATS["phase_means"][c][pk]["group_mean"]
            cells.append(f"<td class='num'>{fnum(gm,1)}</td>")
        rows.append(f"<tr><td>{PHASE_TITLES[pk]}</td>{''.join(cells)}</tr>")
    return head, "\n".join(rows)


def subj_meta_rows():
    rows = []
    for s in SUBJECTS:
        m = STATS["meta"]["subjects"][s]
        rows.append(f"<tr><td>{s}</td><td class='num'>{m['median_hr_bpm']:.0f}</td>"
                    f"<td class='num'>{m['n_beats']:,}</td>"
                    f"<td class='num'>{m['artifact_fraction']*100:.2f}%</td>"
                    f"<td class='num'>{m['recording_span_min']:.0f}</td></tr>")
    return "\n".join(rows)


# cross-signal highlights from the mean-r whole matrix
def rmean(metric_idx, sent_label):
    if not (XCORR and XCORR.get("values", {}).get("whole")):
        return None
    R = XCORR["values"]["whole"]["r_mean"]
    j = SENT_COLS.index(sent_label)
    v = R[metric_idx][j]
    return None if v is None else float(v)


hr_mean = rmean(0, "Mean")     # HR x Mean
sdnn_mean = rmean(1, "Mean")   # SDNN x Mean
hf_mean = rmean(3, "Mean")     # HF x Mean
lfhf_mean = rmean(4, "Mean")   # LF/HF x Mean

# headline contrast numbers
lfhf = STATS["game_vs_med"]["LF_HF"]
resp = STATS["game_vs_med"]["RespRate"]
hr_c = STATS["game_vs_med"]["HR"]
nap_hr = STATS["phase_means"]["HR"]["nap"]["group_mean"]
q_hr = STATS["phase_means"]["HR"]["task00"]["group_mean"]
nap_sdnn = STATS["phase_means"]["SDNN"]["nap"]["group_mean"]
q_sdnn = STATS["phase_means"]["SDNN"]["task00"]["group_mean"]

ghead, grows = gvm_table()
phead, prows = phase_table()

overview_pages = "\n".join(
    f'<div class="fig-page">{fig(n, i+1, f"Full-session cardiac overview — {SUBJECTS[i]} (6 metrics z-scored, vertically offset; task suite + nap).")}</div>'
    for i, n in enumerate(MAN["overview"]))


# cross-signal per-block matrix pages (two per landscape page)
def xcorr_block_pages(start_num):
    if not (XCORR and XCORR.get("blocks")):
        return "", start_num
    order = ["task00", "task01", "task02", "task03", "task04", "task05", "nap"]
    items = [(k, XCORR["blocks"][k]) for k in order if k in XCORR["blocks"]]
    html, num = [], start_num
    for i in range(0, len(items), 2):
        cells = []
        for key, meta in items[i:i + 2]:
            cells.append(
                f'<figure style="margin:0;width:48%;display:inline-block;vertical-align:top">'
                f'<img src="figs/{meta["name"]}" style="width:100%" alt="{meta["title"]}">'
                f'<figcaption><span class="num">Fig. {num}</span> {meta["title"]} '
                f'(mean r across {meta["n_subjects"]} subjects).</figcaption></figure>')
            num += 1
        html.append('<div class="fig-page" style="text-align:center">'
                    + '&nbsp;'.join(cells) + '</div>')
    return "\n".join(html), num


# figure numbering: 1..NS overview, then sequential
n = NS
def nx():
    global n
    n += 1
    return n


fnum_mind_bar = nx(); fnum_mind_hr = nx()
fnum_phases = nx()
fnum_ssvep = nx()
fnum_mask = nx(); fnum_rgb = nx(); fnum_odd = nx()
fnum_xwhole = nx()
xblock_html, _ = xcorr_block_pages(start_num=n + 1)

HTML = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>P013 · Group Cardiac / HRV Report — {NS} subjects</title>
<link rel="stylesheet" href="iacs-report.css">
<style>
  @page land {{ size: Letter landscape; margin: 0.5in 0.5in 0.6in 0.5in; }}
  .fig-page {{ page: land; page-break-before: always; page-break-after: always; }}
  .fig-page figure {{ margin: 0; }}
  .fig-page img {{ width: 100%; height: auto; max-height: 7.0in; object-fit: contain; }}
  table.dense td, table.dense th {{ padding: 2.5px 6px; font-size: 8.4pt; }}
</style>
</head>
<body>

<section class="report-cover">
  <header class="brand">
    <img src="assets/IACS_Logo.svg" alt="IACS">
    <span class="org">Institute for Advanced Consciousness Studies</span>
  </header>
  <h1 class="title">Cardiac &amp; HRV Across P013 — Group Report</h1>
  <p class="lede">The phase-resolved cardiac analysis from the single-session reports,
  pooled across <strong>{NS} participants</strong> ({', '.join(SUBJECTS)}). Each metric
  carries all {NS} subjects as overlaid lines with a group mean, pooled bars with a
  pooled-variance error term, and — for the cross-signal section — the mean correlation
  averaged across subjects in Fisher-z space.</p>
  <dl class="meta">
    <div><dt>Protocol</dt><dd>IACS P013 · Cardiac arm</dd></div>
    <div><dt>Subjects</dt><dd>{NS} · {', '.join(SUBJECTS)}</dd></div>
    <div><dt>Metrics</dt><dd>HR · SDNN · LF · HF · LF/HF · Resp</dd></div>
    <div><dt>Date</dt><dd>{REPORT_DATE}</dd></div>
    <div><dt>Classification</dt><dd>Internal · pre-analysis</dd></div>
  </dl>
</section>

<section>
  <span class="eyebrow">Executive Summary</span>
  <h2>What the Heart Does, Across People</h2>
  <p class="lede">Where the optical signal's state effect flips direction between
  subjects, the heart is far more consistent: the autonomic story repeats across all
  {NS} participants, and two contrasts even survive a random-effects test on {NS} subjects.</p>

  <div class="kpi-row">
    <div class="kpi">
      <p class="label">Meditation LF/HF</p>
      <p class="value">d = {fnum(lfhf['mean_d'],2)}</p>
      <p class="delta up">higher in med · all {NS} · RE p = {p_fmt(lfhf['p'])}</p>
    </div>
    <div class="kpi">
      <p class="label">Respiration slows in med</p>
      <p class="value">d = {fnum(resp['mean_d'],1)}</p>
      <p class="delta up">faster in gameplay · all {NS}</p>
    </div>
    <div class="kpi">
      <p class="label">Nap HR drop</p>
      <p class="value">−{fnum(q_hr-nap_hr,1)}</p>
      <p class="delta">bpm vs questionnaire (group)</p>
    </div>
    <div class="kpi">
      <p class="label">Nap SDNN rise</p>
      <p class="value">{fnum(nap_sdnn/q_sdnn,1)}×</p>
      <p class="delta up">vs questionnaire (group)</p>
    </div>
  </div>

  <aside class="callout">
    <p class="label">The autonomic story replicates</p>
    <p>Meditation raises LF/HF in <strong>all {NS} subjects</strong>
    (d = {fnum(lfhf['mean_d'],2)}, random-effects p = {p_fmt(lfhf['p'])}) — and the
    respiration channel shows why: breathing slows markedly from gameplay to meditation
    in every subject (d = {fnum(resp['mean_d'],1)}), concentrating HRV power in the LF
    band. This is the slow-paced-breathing signature, not sympathetic arousal. Heart rate
    itself does <em>not</em> separate gameplay from meditation consistently
    (d = {fnum(hr_c['mean_d'],2)}, mixed sign). The nap shows the textbook sleep signature
    in the group mean: heart rate falls {fnum(q_hr-nap_hr,1)} bpm and SDNN rises from
    {fnum(q_sdnn,0)} to {fnum(nap_sdnn,0)} ms. ECG quality is excellent in every session
    (see Table 1).</p>
  </aside>

  <h3>How this report is built</h3>
  <ul>
    <li><strong>Event-locked instantaneous HR</strong> — each subject one thin line; the
      bold line is the across-subject mean (±1 between-subject SEM band).</li>
    <li><strong>Bar charts</strong> — bars are the mean across subjects of each
      condition's within-subject deviation; error bars are the <em>pooled</em>
      within-subject SD; dots are individual subjects. Condition contrasts use beats from
      inside each block in non-overlapping 30 s sub-windows.</li>
    <li><strong>Correlation matrices</strong> — the mean Pearson r across subjects
      (Fisher-z averaged, back-transformed); ‡ marks cells where every subject agrees in
      sign.</li>
  </ul>
  <table>
    <caption>Table 1 · Sessions pooled (ECG quality)</caption>
    <thead><tr><th>Subject</th><th class="num">Median HR</th><th class="num">Beats</th><th class="num">Artifact</th><th class="num">Span (min)</th></tr></thead>
    <tbody>{subj_meta_rows()}</tbody>
  </table>
</section>

<section class="section-break">
  <span class="eyebrow">Section 1 · Full Session</span>
  <h2>Each Subject's Whole Recording</h2>
  <p>One landscape page per subject: the six cardiac metrics, z-scored and vertically
  offset, task suite over nap. Whole-session timelines aren't overlaid (paradigm
  durations differ between subjects); every contrast below is aligned.</p>
</section>
{overview_pages}

<section class="section-break">
  <span class="eyebrow">Section 2 · Mind-State — gameplay vs meditation</span>
  <h2>The Autonomic Contrast, Across Subjects</h2>
  <p>Computed from beats inside each block in non-overlapping 30 s sub-windows. LF/HF and
  respiration carry the signal; heart rate does not separate the two states consistently.</p>
  <table>
    <caption>Table 2 · Gameplay vs meditation — Cohen's d per subject &amp; random effects</caption>
    <thead>{ghead}</thead>
    <tbody>{grows}</tbody>
  </table>
  <p class="caption" style="font-size:8.5pt;color:#5e5e6c">RE p = random-effects one-sample
  t-test of the {NS} per-subject d's against 0. With {NS} subjects this is low-powered, yet
  LF/HF reaches it (all subjects same sign). Positive d = higher in gameplay.</p>
</section>
<div class="fig-page">{fig(MAN['bars']['g_bar_mindstate.png'], fnum_mind_bar, 'Mind-State cardiac metrics, gameplay vs meditation. Bars = across-subject mean deviation from each subject grand mean; error = pooled within-subject SD; dots = subjects.')}</div>
<div class="fig-page">{fig(MAN['erp']['g_hr_collision.png'], fnum_mind_hr, 'Collision-locked instantaneous HR (−10 to +20 s), group overlay + mean. Exploratory (~10 collisions/subject).')}</div>

<section class="section-break">
  <span class="eyebrow">Section 3 · Cardiac State Across Phases</span>
  <h2>Phase Means, Pooled</h2>
  <p>Group-mean of every metric in every phase (windowed series; each subject's phase
  mean weighted equally). The nap and backward-masking columns carry the autonomic story:
  heart rate falls and variability rises into sleep.</p>
  <table class="dense">
    <caption>Table 3 · Group-mean phase values for every metric</caption>
    <thead><tr><th>Phase</th>{phead}</tr></thead>
    <tbody>{prows}</tbody>
  </table>
</section>
<div class="fig-page">{fig(MAN['bars']['g_bar_phases.png'], fnum_phases, 'Cardiac metrics by session phase, across-subject pooled bars (±pooled SD, subject dots).')}</div>

<section class="section-break">
  <span class="eyebrow">Section 4 · SSVEP — by frequency band</span>
  <h2>Frequency-Band Cardiac Means, Pooled</h2>
  <p>As in the single-subject reports, frequency is confounded with time-in-ramp and the
  bands are very unequal in duration, so slow metrics (LF/HF) cannot be estimated in the
  short high-frequency bands. Exploratory.</p>
</section>
<div class="fig-page">{fig(MAN['bars']['g_bar_ssvep.png'], fnum_ssvep, 'SSVEP cardiac metrics by effective-frequency band, across-subject pooled bars. Frequency/time confound; short bands lack LF/HF. Exploratory.')}</div>

<section class="section-break">
  <span class="eyebrow">Section 5 · Timescale-limited paradigms</span>
  <h2>Masking, RGB &amp; Oddball — instantaneous HR</h2>
  <p>HRV cannot resolve a single brief stimulus, but beat-to-beat instantaneous HR can
  carry a transient. Included for parity with the optical report; all exploratory and at
  noise across subjects. The single-subject reports' per-colour cardiac <em>bar</em> is
  intentionally dropped here: the three colours interleave at ~1.6 s over the same minutes,
  so a tens-of-seconds HRV estimate cannot be attributed to a colour — it is not a valid
  contrast, only the colour-onset instantaneous-HR overlay below is shown.</p>
</section>
<div class="fig-page">{fig(MAN['erp']['g_hr_masking.png'], fnum_mask, 'Face-onset instantaneous HR, seen vs unseen (matched SOA), group overlay + mean. Exploratory.')}</div>
<div class="fig-page">{fig(MAN['erp']['g_hr_rgb.png'], fnum_rgb, 'Colour-onset instantaneous HR, group overlay + mean. No systematic colour-dependent deflection. Exploratory.')}</div>
<div class="fig-page">{fig(MAN['erp']['g_hr_oddball.png'], fnum_odd, 'Tone-locked instantaneous HR, deviant vs standard, group overlay + mean. Exploratory.')}</div>

<section class="section-break">
  <span class="eyebrow">Section 6 · Cross-Signal Correlation</span>
  <h2>Cardiac &amp; Respiration vs the Sentiometer — Mean r</h2>
  <p>Each cardiac/respiratory metric is correlated against all seven Sentiometer series
  on the shared LSL clock (every Sentiometer series averaged over the same 120 s trailing
  window as the cardiac metrics). The matrix below is the <strong>mean Pearson r across
  {NS} subjects</strong>, averaged in Fisher-z space; ‡ marks cells where every subject
  agrees in sign.</p>
  <p>A coherent, replicated pattern emerges: higher Sentiometer signal goes with higher
  HF-HRV (r = {fnum(hf_mean,2)}) and higher SDNN (r = {fnum(sdnn_mean,2)}) and lower LF/HF
  (r = {fnum(lfhf_mean,2)}) — all sign-consistent across subjects — i.e. the optical signal
  rises with a calmer, more parasympathetic autonomic state. Notably, the heart-rate
  coupling that looked striking in a single session <em>washes out</em> across subjects
  (HR × mean r = {fnum(hr_mean,2)}, sign-inconsistent), a clean example of why pooling
  matters.</p>
  <aside class="callout">
    <p class="label">Still co-movement, not causation</p>
    <p>These are slow, state-level associations on overlapping windows, now averaged over
    {NS} sessions. Sign-consistency (‡) is encouraging but is not a significance test;
    formal inference needs a per-subject permutation null and more participants. Read the
    pattern, not any single cell.</p>
  </aside>
</section>
<div class="fig-page">{fig(XCORR['whole'], fnum_xwhole, 'Cardiac/respiration × Sentiometer — mean r across subjects (Fisher-z averaged), whole session. ‡ = all subjects agree in sign.')}</div>
{xblock_html}

<section class="section-break">
  <span class="eyebrow">Appendix · Notes</span>
  <h2>Quality &amp; Caveats</h2>
  <ul>
    <li>ECG quality is excellent in every session (Table 1) — see per-subject artifact
      fractions; R-peaks via neurokit2.</li>
    <li>Continuous series use a 120 s trailing window stepped 5 s (overlapping →
      autocorrelated); condition contrasts use non-overlapping 30 s beat-based
      sub-windows so SEM is valid and no pre-block history bleeds across boundaries.</li>
    <li>The group layer adds a random-effects t-test on the per-subject d's and, for the
      correlation matrices, a Fisher-z mean across subjects with a sign-agreement flag.</li>
    <li>{NS} participants, descriptive. No permutation null, no multiple-comparison
      control. Effect sizes and cross-subject consistency are the robust quantities.</li>
  </ul>
  <hr class="brand">
  <div class="footnotes"><ol>
    <li>Sources: <code>outputs/&lt;subject&gt;/cardiac/</code> for each of
      {', '.join(SUBJECTS)} (CGX ECG + Resp.). Group figures/stats by
      <code>scripts/cardiac_group_figs.py</code> / <code>cardiac_group_stats.py</code>.</li>
    <li>To add a participant: derive their cardiac bundle, then re-run the group scripts —
      they auto-discover every subject under <code>outputs/</code>.</li>
  </ol></div>
</section>

</body>
</html>
"""

out = RPT / "index.html"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(HTML, encoding="utf-8")
print("wrote", out, len(HTML), "bytes")
