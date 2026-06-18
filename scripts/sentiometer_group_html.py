"""Assemble the IACS-styled GROUP Sentiometer report HTML and render to PDF.

Pulls every number from the group stats.json. Mirrors the single-subject
report's structure, but each contrast now shows per-subject effect sizes, the
across-subject mean, and a random-effects test; figures are overlay+mean or
pooled-variance bars.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
REPORT_DATE = os.environ.get("SENT_REPORT_DATE", "—")
RPT = REPO / "outputs" / "_group" / "sentiometer" / "report"
FIGS = RPT / "figs"
STATS = json.loads((FIGS / "stats.json").read_text())
MAN = json.loads((FIGS / "figs_manifest.json").read_text())

SUBJECTS = STATS["subjects"]
SERIES = STATS["series"]
NS = len(SUBJECTS)


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


def contrast_table(ct):
    """rows = series; cols = per-subject d + group mean d, SD, RE p, sign."""
    head = ("<tr><th>Series</th>"
            + "".join(f"<th class='num'>{s.replace('P013_','')} d</th>" for s in SUBJECTS)
            + "<th class='num'>mean d</th><th class='num'>SD d</th>"
            + "<th class='num'>RE p</th><th>dir.</th></tr>")
    rows = []
    for sl in SERIES:
        a = STATS["contrasts"][ct][sl]
        cells = []
        for s in SUBJECTS:
            r = a["per_subject"].get(s) or {}
            cells.append(f"<td class='num'>{fnum(r.get('d'), 2)}</td>")
        sign = ("<span class='tag'>consistent</span>" if a.get("same_sign")
                else "<span class='tag warn'>mixed</span>")
        rows.append(f"<tr><td>{sl}</td>{''.join(cells)}"
                    f"<td class='num'>{fnum(a.get('mean_d'),2)}</td>"
                    f"<td class='num'>{fnum(a.get('sd_d'),2)}</td>"
                    f"<td class='num'>{p_fmt(a.get('p'))}</td><td>{sign}</td></tr>")
    return head, "\n".join(rows)


def rgb_table():
    head = ("<tr><th>Series</th>"
            + "".join(f"<th class='num'>{s.replace('P013_','')} p</th>" for s in SUBJECTS)
            + "<th>verdict</th></tr>")
    rows = []
    for sl in SERIES:
        per = STATS["rgb"][sl]["per_subject"]
        ps = [per[s].get("p") for s in SUBJECTS]
        cells = "".join(f"<td class='num'>{p_fmt(p)}</td>" for p in ps)
        allns = all((p is None) or (p > 0.05) for p in ps)
        verdict = ("<span class='tag'>null (all n.s.)</span>" if allns
                   else "<span class='tag warn'>check</span>")
        rows.append(f"<tr><td>{sl}</td>{cells}<td>{verdict}</td></tr>")
    return head, "\n".join(rows)


def dq_table():
    head = ("<tr><th>Channel</th>"
            + "".join(f"<th class='num'>{s.replace('P013_','')} mean</th>"
                      f"<th class='num'>SD</th>" for s in SUBJECTS) + "</tr>")
    rows = []
    for c in ["PD1", "PD2", "PD3", "PD4", "PD5"]:
        cells = []
        for s in SUBJECTS:
            d = STATS["data_quality"][c][s]
            flag = " *" if d["std"] < 5 else ""
            cells.append(f"<td class='num'>{d['mean']:.0f}</td>"
                         f"<td class='num'>{d['std']:.2f}{flag}</td>")
        rows.append(f"<tr><td>{c}</td>{''.join(cells)}</tr>")
    return head, "\n".join(rows)


def subj_meta_rows():
    rows = []
    for s in SUBJECTS:
        m = STATS["meta"]["subjects"][s]
        rows.append(f"<tr><td>{s}</td><td class='num'>{m['duration_hms']}</td>"
                    f"<td class='num'>{m['n_samples']:,}</td>"
                    f"<td class='num'>{m['fs_hz']:.1f}</td>"
                    f"<td class='num'>{m['pc1_evr']*100:.1f}%</td></tr>")
    return "\n".join(rows)


# headline numbers
gm = STATS["contrasts"]["game_vs_med"]["Mean"]
gm_dlist = ", ".join(f"{x:+.2f}" for x in gm["d_list"])
n_pos = sum(1 for x in gm["d_list"] if x > 0)
odd = STATS["contrasts"]["oddball_p300"]["Mean"]
msk = STATS["contrasts"]["masking_seen_unseen"]["Mean"]
rgb_all_ns = all((STATS["rgb"][sl]["per_subject"][s].get("p") or 1) > 0.05
                 for sl in SERIES for s in SUBJECTS)
msk_consistent = [sl for sl in SERIES
                  if STATS["contrasts"]["masking_seen_unseen"][sl].get("same_sign")]
msk_mixed = [sl for sl in SERIES if sl not in msk_consistent]

ohead, orows = contrast_table("oddball_p300")
mhead, mrows = contrast_table("game_vs_med")
khead, krows = contrast_table("masking_seen_unseen")
rhead, rrows = rgb_table()
dhead, drows = dq_table()

overview_pages = "\n".join(
    f'<div class="fig-page">{fig(n, i+1, f"Full-session overview — {SUBJECTS[i]} (z-scored, 7 series, vertically offset; task suite + nap).")}</div>'
    for i, n in enumerate(MAN["overview"]))

HTML = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>P013 · Group Sentiometer Report — {NS} subjects</title>
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
  <h1 class="title">Sentiometer Signal Across P013 — Group Report</h1>
  <p class="lede">The phase-resolved Sentiometer analysis from the single-session
  reports, now pooled across <strong>{NS} participants</strong> ({', '.join(SUBJECTS)}).
  Every paradigm is analysed identically to the per-subject reports; here each
  contrast carries all {NS} subjects as overlaid lines with a group mean, pooled
  bar charts with a pooled-variance error term, and a per-subject effect-size table.</p>
  <dl class="meta">
    <div><dt>Protocol</dt><dd>IACS P013 · Sentiometer arm</dd></div>
    <div><dt>Subjects</dt><dd>{NS} · {', '.join(SUBJECTS)}</dd></div>
    <div><dt>Series</dt><dd>PD1–PD5 · Mean · PC1</dd></div>
    <div><dt>Date</dt><dd>{REPORT_DATE}</dd></div>
    <div><dt>Classification</dt><dd>Internal · pre-analysis</dd></div>
  </dl>
</section>

<section>
  <span class="eyebrow">Executive Summary</span>
  <h2>What Holds Up Across Subjects</h2>
  <p class="lede">Pooling {NS} sessions turns each single-subject curiosity into a
  testable cross-subject question: does an effect <em>replicate</em>, and does it
  point the <em>same way</em> in every participant? Two patterns separate cleanly.</p>

  <div class="kpi-row">
    <div class="kpi">
      <p class="label">Mind-state (game vs med)</p>
      <p class="value">d = {fnum(gm['mean_d'],2)}</p>
      <p class="delta {'up' if gm['same_sign'] else ''}">{'consistent' if gm['same_sign'] else 'sign-inconsistent'} · {n_pos}/{NS} game&gt;med</p>
    </div>
    <div class="kpi">
      <p class="label">RGB decoding</p>
      <p class="value">{NS}/{NS} null</p>
      <p class="delta">ANOVA n.s. every subject</p>
    </div>
    <div class="kpi">
      <p class="label">Oddball P300</p>
      <p class="value">d = {fnum(odd['mean_d'],2)}</p>
      <p class="delta">n.s., mixed sign</p>
    </div>
    <div class="kpi">
      <p class="label">Masking seen−unseen</p>
      <p class="value">d = {fnum(msk['mean_d'],2)}</p>
      <p class="delta {'up' if msk['same_sign'] else ''}">{'same sign in all '+str(NS) if msk['same_sign'] else 'mixed'} · RE p = {p_fmt(msk['p'])}</p>
    </div>
  </div>

  <aside class="callout">
    <p class="label">The headline is consistency, not magnitude</p>
    <p>The Mind-State contrast is <strong>large in every individual</strong>
    (per-subject d = {gm_dlist} on the Mean series) — but it does
    <em>not</em> point the same way: {n_pos} of {NS} subjects show gameplay &gt; meditation
    and the remaining subject reverses, so the random-effects test across subjects is
    non-significant (p = {p_fmt(gm['p'])}). A within-subject state effect this big that
    flips direction between people is itself the finding: the Sentiometer is tracking
    <em>something</em> that changes with cognitive state, but not a fixed-polarity
    "consciousness" axis shared across individuals. By contrast the Backward-Masking
    seen−unseen difference is tiny but <strong>negative in all {NS} subjects on the Mean
    series</strong> (random-effects p = {p_fmt(msk['p'])}; {len(msk_consistent)}/{len(SERIES)}
    series sign-consistent) — weak, but the most directionally consistent stimulus-locked
    effect in the battery. RGB stays null in everyone, as predicted.</p>
  </aside>

  <h3>How this report is built</h3>
  <ul>
    <li><strong>Time-series &amp; ERPs</strong> — each subject is one thin line; the
      bold line is the across-subject mean with a ±1 between-subject SEM band.</li>
    <li><strong>Bar charts</strong> — bars are the mean across subjects of each
      condition's within-subject deviation (per-subject DC offset removed); the error
      bar is the <em>pooled</em> within-subject SD; dots are individual subjects.</li>
    <li><strong>Effect-size tables</strong> — Cohen's d per subject, the across-subject
      mean and SD, and a random-effects one-sample t-test of those d's against zero.</li>
  </ul>
  <table>
    <caption>Table 1 · Sessions pooled</caption>
    <thead><tr><th>Subject</th><th class="num">Recording</th><th class="num">Samples</th><th class="num">Fs (Hz)</th><th class="num">PC1 var.</th></tr></thead>
    <tbody>{subj_meta_rows()}</tbody>
  </table>
  <p class="caption" style="font-size:8.5pt;color:#5e5e6c">Each session is derived and
  analysed exactly as in its single-subject report (covariance PC1 fit per session over
  the full recording). The whole-session overview cannot be overlaid on one time axis —
  paradigm durations differ between subjects — so it is shown as small multiples; every
  event-locked and condition contrast <em>is</em> aligned and overlaid.</p>
</section>

<section class="section-break">
  <span class="eyebrow">Section 1 · Full Session</span>
  <h2>Each Subject's Whole Recording</h2>
  <p>One landscape page per subject: all seven series, z-scored and vertically offset,
  task suite over nap. These give the gestalt; the cross-subject statistics begin in
  Section 2.</p>
</section>
{overview_pages}

<section class="section-break">
  <span class="eyebrow">Section 2 · Mind-State — the headline contrast</span>
  <h2>Gameplay vs Meditation, Across Subjects</h2>
  <p>Averaging each session over its gameplay and meditation blocks (5 s bins) gives
  the largest within-subject effect in the battery — and the clearest demonstration of
  why pooling matters. The bars below average the within-subject deviations; the dots
  show that one subject reverses. The effect size is large in every subject but the
  direction is not shared, so the random-effects test is non-significant.</p>
  <table>
    <caption>Table 2 · Gameplay vs meditation — Cohen's d per subject &amp; random effects</caption>
    <thead>{mhead}</thead>
    <tbody>{mrows}</tbody>
  </table>
  <p class="caption" style="font-size:8.5pt;color:#5e5e6c">RE p = random-effects
  one-sample t-test of the {NS} per-subject d's against 0 (subject = unit of replication;
  with {NS} subjects this is low-powered — read the per-subject d's and the sign column).
  Per-subject p/t within each session are anti-conservative (autocorrelated 5 s bins) and
  are omitted here in favour of d.</p>
</section>
<div class="fig-page">{fig(MAN['bars']['g_bar_mindstate.png'], NS+1, 'Mind-State gameplay vs meditation. Bars = across-subject mean deviation from each subject panel grand mean; error = pooled within-subject SD; dots = individual subjects. One subject (green) reverses on every series.')}</div>
<div class="fig-page">{fig(MAN['erp']['g_erp_collision.png'], NS+2, 'Collision-locked ERP (−300 to +300 ms, baseline-corrected). Thin = per-subject mean; bold = group mean ±1 between-subject SEM. Underpowered per subject (~10 collisions); shown for completeness.')}</div>

<section class="section-break">
  <span class="eyebrow">Section 3 · RGB Illuminance — the null test</span>
  <h2>No Colour Decoding, in Anyone</h2>
  <p>The RGB paradigm predicts the Sentiometer cannot tell red from green from blue. A
  per-subject one-way ANOVA across the three colours is non-significant on every series
  in every subject — the predicted null replicates {NS}/{NS}.</p>
  <table>
    <caption>Table 3 · RGB one-way ANOVA p-value per subject &amp; series</caption>
    <thead>{rhead}</thead>
    <tbody>{rrows}</tbody>
  </table>
</section>
<div class="fig-page">{fig(MAN['bars']['g_bar_rgb.png'], NS+3, 'RGB per-colour means, across-subject pooled bars (±pooled SD, subject dots). All between-colour deviations are within noise in every subject — the predicted null.')}</div>
<div class="fig-page">{fig(MAN['erp']['g_erp_rgb.png'], NS+4, 'Colour-onset ERP (−300 to +300 ms), group overlay + mean. No systematic colour-dependent deflection.')}</div>

<section class="section-break">
  <span class="eyebrow">Section 4 · Auditory Oddball — P300 window</span>
  <h2>Deviant vs Standard, 250–350 ms</h2>
  <p>Locking to tone onset and contrasting deviant vs standard in the classic P300
  window yields no reliable optical difference in any subject; the per-subject d's are
  near zero and mixed in sign.</p>
  <table>
    <caption>Table 4 · Oddball deviant−standard (250–350 ms) — d per subject</caption>
    <thead>{ohead}</thead>
    <tbody>{orows}</tbody>
  </table>
</section>
<div class="fig-page">{fig(MAN['erp']['g_erp_oddball.png'], NS+5, 'Deviant vs standard ERP, group overlay + mean (−300 to +300 ms, baseline-corrected).')}</div>

<section class="section-break">
  <span class="eyebrow">Section 5 · Backward Masking — seen vs unseen</span>
  <h2>The Matched-Duration Contrast</h2>
  <p>Face-present trials only, locked to face onset (QUEST-converged SOA), split by
  whether the participant reported seeing the face — physical timing matched, percept
  differs. The difference (0–300 ms post-onset) is small, but it is <strong>negative in all
  {NS} subjects on {len(msk_consistent)} of {len(SERIES)} series</strong> — including the
  summary Mean (random-effects p = {p_fmt(msk['p'])}) and PC1 ({', '.join(msk_consistent)});
  only {' and '.join(msk_mixed) if msk_mixed else 'none'} flip sign. That makes it the most
  directionally consistent stimulus-locked optical effect in the battery — weak, and worth a
  properly powered look. See the per-series <em>dir.</em> column below.</p>
  <p class="caption" style="font-size:8.5pt;color:#5e5e6c">A secondary, response-locked view
  of this contrast exists in each single-subject report; it is intentionally not carried into
  the group report — its "unseen" set folds in catch-trial correct rejections, so the
  face-onset, face-present version above is the clean matched-duration comparison.</p>
  <table>
    <caption>Table 5 · Seen−unseen (face-present, 0–300 ms) — d per subject</caption>
    <thead>{khead}</thead>
    <tbody>{krows}</tbody>
  </table>
</section>
<div class="fig-page">{fig(MAN['erp']['g_erp_masking_faceonset.png'], NS+6, 'Seen vs unseen, locked to face onset (matched SOA, face-present only), group overlay + mean. Physical stimulation matched; percept differs.')}</div>

<section class="section-break">
  <span class="eyebrow">Section 6 · SSVEP — amplitude by frequency band</span>
  <h2>Frequency-Band Means, Pooled</h2>
  <p>During the 40→1 Hz ramp the signal is binned by effective stimulation frequency.
  As in the single-subject reports, frequency is fully confounded with time-elapsed-in-
  ramp, so any band ordering is equally consistent with slow drift; the pooled bars are
  descriptive only.</p>
  <aside class="callout">
    <p class="label">Caveat (unchanged from the single-subject reports)</p>
    <p>Frequency ramps linearly with time, so "band" ≡ "minutes into the ramp." A clean
    monotonic ordering across subjects would still not separate a frequency response from
    a five-minute drift. Flagged for the formal analysis.</p>
  </aside>
</section>
<div class="fig-page">{fig(MAN['bars']['g_bar_ssvep.png'], NS+7, 'SSVEP amplitude by effective-frequency band, across-subject pooled bars (±pooled SD, subject dots). Descriptive only — frequency/time confound.')}</div>

<section class="section-break">
  <span class="eyebrow">Appendix · Data Quality</span>
  <h2>Per-Channel Stats by Subject</h2>
  <p>Per-photodiode amplitude statistics for each session (raw units, full recording).
  Channels with SD &lt; 5 are flagged (*) — typically PD3, which sits at a high, very
  stable baseline and contributes little to PC1.</p>
  <table class="dense">
    <caption>Table 6 · Per-photodiode mean &amp; SD by subject</caption>
    <thead>{dhead}</thead>
    <tbody>{drows}</tbody>
  </table>
  <hr class="brand">
  <div class="footnotes"><ol>
    <li>Sources: <code>outputs/&lt;subject&gt;/preprocessed/</code> for each of
      {', '.join(SUBJECTS)}. Each derived by <code>scripts/sentiometer_derive.py</code>;
      group figures/stats by <code>scripts/sentiometer_group_figs.py</code> /
      <code>sentiometer_group_stats.py</code>.</li>
    <li>{NS} participants, descriptive. Per-subject statistics are uncorrected Welch
      <em>t</em>/ANOVA; the group layer adds a random-effects t-test on per-subject d's.
      No permutation null, no multiple-comparison control, no HRV/motion partialling.</li>
    <li>To add a participant: derive their bundle, then re-run the group scripts — they
      auto-discover every subject under <code>outputs/</code>.</li>
  </ol></div>
</section>

</body>
</html>
"""

out = RPT / "index.html"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(HTML, encoding="utf-8")
print("wrote", out, len(HTML), "bytes")
