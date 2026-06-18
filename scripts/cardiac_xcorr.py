"""Cross-signal correlation: cardiac/respiratory metrics vs Sentiometer.

Appendix analysis for the cardiac report. Correlates the six windowed
cardiac/respiratory metrics (HR, SDNN, LF, HF, LF/HF, RespRate) against all
seven Sentiometer series (PD1-PD5, mean, PC1) on the shared LSL clock —
once for the whole session and once per task block.

Alignment. Cardiac metrics live on a 120 s-trailing-window grid stepped
5 s. To compare like with like, each Sentiometer series is aggregated over
the SAME trailing 120 s window ending at each cardiac grid point (mean of
the raw 500 Hz samples). Both signals then describe "the last two minutes"
at every point, so a Pearson correlation across grid points is a
slow-timescale (state-level) association, not a sample-by-sample one.

Writes correlation matrices as IACS-styled heatmaps into the cardiac
report's figs/ and returns a manifest. Imported/called by
cardiac_report_figs.py.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap
from scipy import stats as sps

import cardiac_report_lib as L

SENT_COLS = ["PD1", "PD2", "PD3", "PD4", "PD5", "sent_mean", "pc1"]
SENT_LABELS = ["PD1", "PD2", "PD3", "PD4", "PD5", "Mean", "PC1"]
WIN_S = 120.0  # match the cardiac trailing window

# Significance thresholds for the asterisks, applied to FDR-adjusted q-values.
# More asterisks = stronger evidence (smaller q). Checked most-strict first.
STAR_LEVELS = [(0.001, "****"), (0.005, "***"), (0.01, "**"), (0.04, "*")]


def stars_for(q):
    if q is None or np.isnan(q):
        return ""
    for thresh, mark in STAR_LEVELS:
        if q < thresh:
            return mark
    return ""


def bh_fdr(pvals):
    """Benjamini-Hochberg FDR. Input: 1-D array of p (may contain NaN).
    Returns q-values aligned to input, NaN preserved."""
    p = np.asarray(pvals, float)
    out = np.full(p.shape, np.nan)
    finite = np.where(~np.isnan(p))[0]
    if finite.size == 0:
        return out
    pv = p[finite]
    order = np.argsort(pv)
    ranked = pv[order]
    m = len(ranked)
    q = ranked * m / (np.arange(1, m + 1))
    # enforce monotonicity (step-up)
    q = np.minimum.accumulate(q[::-1])[::-1]
    q = np.clip(q, 0, 1)
    out_finite = np.empty(m)
    out_finite[order] = q
    out[finite] = out_finite
    return out


def effective_n(x, y):
    """Autocorrelation-adjusted effective sample size (Pyper & Peterman 1998).
    The xcorr windows overlap 96% (120 s window, 5 s step), so the raw n wildly
    overstates the degrees of freedom. n_eff shrinks it using the series'
    own autocorrelations:  1/n_eff ≈ 1/n + (2/n) Σ_k r_xx(k) r_yy(k)."""
    n = len(x)
    if n < 10:
        return n
    xc = x - x.mean()
    yc = y - y.mean()
    denom_x = np.sum(xc * xc)
    denom_y = np.sum(yc * yc)
    if denom_x == 0 or denom_y == 0:
        return n
    kmax = n // 4  # cap lags at n/4 (standard)
    s = 0.0
    for k in range(1, kmax + 1):
        rxx = np.sum(xc[:-k] * xc[k:]) / denom_x
        ryy = np.sum(yc[:-k] * yc[k:]) / denom_y
        s += (n - k) / n * rxx * ryy
    inv = 1.0 / n + (2.0 / n) * s
    if inv <= 0:
        return n
    n_eff = 1.0 / inv
    return float(np.clip(n_eff, 3, n))


def corr_p(x, y):
    """Pearson r and a p-value computed against the autocorrelation-adjusted
    effective sample size (two-sided t-test on r with n_eff-2 df)."""
    r = np.corrcoef(x, y)[0, 1]
    n_eff = effective_n(x, y)
    df = n_eff - 2
    if df <= 0 or abs(r) >= 1.0:
        return r, np.nan, n_eff
    t = r * np.sqrt(df / (1 - r * r))
    p = 2 * sps.t.sf(abs(t), df)
    return r, float(p), n_eff

SENT_PARQUET = L.REPO / "outputs" / L.SUBJECT / "preprocessed" / "sentiometer_timeseries.parquet"

# diverging colormap in IACS tones: ink-navy (neg) -> paper (0) -> purple (pos)
_CMAP = LinearSegmentedColormap.from_list(
    "iacs_div", ["#141437", "#6b8fb5", "#faf8f4", "#9a86c4", "#5d5179"])


def _trailing_means(sent_t, sent_y, centres):
    """Mean of sent_y over (c-WIN_S, c] for each centre c, via cumsum."""
    cs = np.concatenate([[0.0], np.cumsum(sent_y)])
    a = np.searchsorted(sent_t, centres - WIN_S, "left")
    b = np.searchsorted(sent_t, centres, "left")
    n = b - a
    out = np.full(len(centres), np.nan)
    ok = n > 0
    out[ok] = (cs[b[ok]] - cs[a[ok]]) / n[ok]
    return out


def build_aligned(bundle):
    """Return a DataFrame with the 6 cardiac metrics + 7 trailing-window
    Sentiometer means + task label, one row per cardiac window."""
    if not SENT_PARQUET.exists():
        return None
    s = pd.read_parquet(SENT_PARQUET, columns=["t_lsl"] + SENT_COLS)
    st = s["t_lsl"].to_numpy()
    centres = bundle.ts["t_lsl"].to_numpy()
    data = {}
    for col in SENT_COLS:
        data[col] = _trailing_means(st, s[col].to_numpy(dtype=np.float64), centres)
    A = pd.DataFrame(data)
    for c in L.METRIC_COLS:
        A[c] = bundle.ts[c].to_numpy()
    A["task"] = bundle.ts["task"].to_numpy()
    A["t_rel_s"] = bundle.ts["t_rel_s"].to_numpy()
    return A


def corr_matrix(df):
    """Correlation analysis, cardiac rows x sentiometer cols.

    Returns (R, Q, Neff, n) where R is Pearson r, Q is the BH-FDR-adjusted
    q-value (FDR applied across all cells of THIS matrix), Neff the
    autocorrelation-adjusted effective sample size per cell, and n the number
    of aligned windows. p-values use n_eff because the 120 s/5 s windows
    overlap ~96%, so the raw n would make every cell spuriously significant."""
    sub = df.dropna(subset=L.METRIC_COLS + SENT_COLS)
    nrow, ncol = len(L.METRIC_COLS), len(SENT_COLS)
    R = np.full((nrow, ncol), np.nan)
    P = np.full((nrow, ncol), np.nan)
    Neff = np.full((nrow, ncol), np.nan)
    for i, rc in enumerate(L.METRIC_COLS):
        x = sub[rc].to_numpy()
        for j, sc in enumerate(SENT_COLS):
            y = sub[sc].to_numpy()
            if len(x) > 5 and x.std() > 0 and y.std() > 0:
                r, p, ne = corr_p(x, y)
                R[i, j], P[i, j], Neff[i, j] = r, p, ne
    Q = bh_fdr(P.ravel()).reshape(P.shape)
    return R, Q, Neff, len(sub)


def heatmap(M, Q, n, title, name):
    fig, ax = plt.subplots(figsize=(6.8, 5.4))
    fig.subplots_adjust(left=0.16, right=0.99, top=0.86, bottom=0.19)
    im = ax.imshow(M, cmap=_CMAP, vmin=-1, vmax=1, aspect="auto")
    ax.set_xticks(range(len(SENT_LABELS)))
    ax.set_xticklabels(SENT_LABELS, fontsize=8)
    ax.set_yticks(range(len(L.METRIC_COLS)))
    ax.set_yticklabels([L.METRIC_META[c][0] for c in L.METRIC_COLS], fontsize=8)
    ax.set_xlabel("Sentiometer series", fontsize=8.5)
    # annotate each cell: r value with FDR-significance asterisks as superscript
    for i in range(M.shape[0]):
        for j in range(M.shape[1]):
            v = M[i, j]
            if np.isnan(v):
                ax.text(j, i, "—", ha="center", va="center", fontsize=7, color=L.MUTED)
                continue
            tc = "white" if abs(v) > 0.55 else L.INK
            mark = stars_for(Q[i, j]) if Q is not None else ""
            ax.text(j, i, f"{v:.2f}", ha="center", va="center", fontsize=7.2, color=tc)
            if mark:
                ax.text(j + 0.30, i - 0.22, mark, ha="center", va="center",
                        fontsize=6.6, color=tc, fontweight="bold")
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_xticks(np.arange(-.5, len(SENT_LABELS), 1), minor=True)
    ax.set_yticks(np.arange(-.5, len(L.METRIC_COLS), 1), minor=True)
    ax.grid(which="minor", color="white", linewidth=1.2)
    ax.tick_params(which="both", length=0)
    cb = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.03, ticks=[-1, -0.5, 0, 0.5, 1])
    cb.ax.tick_params(labelsize=7)
    cb.outline.set_visible(False)
    cb.set_label("Pearson r", fontsize=7.5)
    ax.set_title(title, loc="left", fontsize=10, pad=10, color=L.INK, fontweight="bold")
    fig.text(0.16, 0.085,
             "FDR-corrected (BH) on autocorrelation-adjusted n_eff:  "
             "* q<0.04   ** q<0.01   *** q<0.005   **** q<0.001",
             fontsize=6.4, color=L.INK)
    fig.text(0.16, 0.045,
             f"n = {n} windows · 120 s trailing window · shared LSL clock",
             fontsize=7, color=L.MUTED)
    return L.savefig(fig, name)


# task display names for per-block matrices
BLOCK_TITLES = [
    ("task00", "Questionnaire"), ("task01", "Auditory Oddball"),
    ("task02", "RGB Illuminance"), ("task03", "Backward Masking"),
    ("task04", "Mind-State"), ("task05", "SSVEP"), ("nap", "Nap"),
]


def run(bundle, manifest):
    A = build_aligned(bundle)
    if A is None:
        print("  xcorr: sentiometer bundle not found — skipping")
        return None
    out = {"whole": None, "blocks": {}, "values": {}}

    R, Q, Neff, n = corr_matrix(A)
    out["whole"] = heatmap(
        R, Q, n, "Cardiac × Sentiometer — whole session", "xcorr_whole.png").name
    nsig = int(np.nansum(Q < 0.04))
    out["values"]["whole"] = {"r": R.tolist(), "q": Q.tolist(),
                              "n_eff": Neff.tolist(), "n": n, "n_sig_fdr": nsig}

    for key, title in BLOCK_TITLES:
        sub = A[A["task"] == key]
        Rb, Qb, Neffb, nb = corr_matrix(sub)
        if nb < 8:   # too few aligned windows for a stable r
            out["values"][key] = {"r": Rb.tolist(), "n": nb, "skipped": True}
            continue
        nm = heatmap(Rb, Qb, nb, f"Cardiac × Sentiometer — {title}", f"xcorr_{key}.png").name
        nsig_b = int(np.nansum(Qb < 0.04))
        out["blocks"][key] = {"name": nm, "title": title, "n": nb, "n_sig_fdr": nsig_b}
        out["values"][key] = {"r": Rb.tolist(), "q": Qb.tolist(),
                              "n_eff": Neffb.tolist(), "n": nb, "n_sig_fdr": nsig_b}

    manifest["xcorr"] = out
    print(f"  xcorr: whole (n={n}, {nsig} FDR-sig cells) + {len(out['blocks'])} block matrices")
    return out
