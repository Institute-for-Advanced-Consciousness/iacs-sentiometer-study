"""Shared helpers for report figures.

Functions in this module are used by both ``03_manifest.py`` (to embed a
sample-EEG figure per subject) and ``04_niccotest_report.py`` (for the
Sentiometer on/off full-spectrum analysis + on/off sample epochs).

Nothing here touches pyedflib / pyxdf — callers pass in already-extracted
numpy arrays plus labels / sample rate / metadata. That keeps the helpers
testable and cheap to import.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib
import numpy as np
from scipy import signal

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


# ----- Channel set (consistent across all subjects) -------------------------

SAMPLE_CHANNELS = ("Fp1", "Fp2", "C3", "C4", "O1", "O2")  # front LR, mid LR, back LR

EPOCH_S = 10.0          # length of each sample-EEG excerpt
N_EPOCHS_PER_COND = 3   # "1st, 2nd, 3rd epochs" per condition

# Canonical frequency bands (Hz)
BANDS = [
    ("Delta",    1.0, 4.0),
    ("Theta",    4.0, 8.0),
    ("Alpha",    8.0, 13.0),
    ("Beta",     13.0, 30.0),
    ("Low gamma", 30.0, 60.0),
]


# ----- Epoch-time selection -------------------------------------------------

def pick_epoch_times(
    bv_duration_s: float,
    marker_times_rel_s: list[float] | None = None,
    task_fallback: tuple[float, float] = (60.0, 600.0),
) -> dict[str, list[float]]:
    """Return {'task': [3 times], 'late': [3 times]} in seconds relative to
    the BrainVision stream start.

    * ``task`` window: if task markers exist, use the times spanned by the
      earliest and latest markers; otherwise use ``task_fallback``.
    * ``late`` window: 15 min (900 s) after the last task marker if that
      still fits inside the recording with a 60 s buffer; otherwise pick
      the last 4 min of the recording (excluding the final 60 s as
      device-removal buffer).

    Callers then use each returned time as an epoch start. Epoch length is
    :data:`EPOCH_S`.
    """
    # --- Task window --------------------------------------------------
    if marker_times_rel_s:
        t_task_start = max(0.0, min(marker_times_rel_s))
        t_task_end = max(marker_times_rel_s)
    else:
        t_task_start, t_task_end = task_fallback

    task_span = max(10.0, t_task_end - t_task_start)
    task_times = [
        t_task_start + task_span * f
        for f in np.linspace(0.2, 0.8, N_EPOCHS_PER_COND)
    ]
    # Safety: make sure each epoch fits in the recording.
    task_times = [
        max(0.0, min(t, bv_duration_s - EPOCH_S - 1.0)) for t in task_times
    ]

    # --- Late / sleep window ------------------------------------------
    late_anchor = None
    if marker_times_rel_s:
        t_last = max(marker_times_rel_s)
        t_candidate = t_last + 900.0  # 15 min after last task marker
        if t_candidate + EPOCH_S * N_EPOCHS_PER_COND + 60.0 < bv_duration_s:
            late_anchor = t_candidate
    if late_anchor is None:
        # Fallback: last ~4 min, excluding final 60 s
        late_end = bv_duration_s - 60.0
        late_anchor = max(0.0, late_end - (N_EPOCHS_PER_COND * 60.0 + 60.0))
    late_times = [
        late_anchor + i * 60.0  # 60 s apart → widely sampled
        for i in range(N_EPOCHS_PER_COND)
    ]
    late_times = [
        max(0.0, min(t, bv_duration_s - EPOCH_S - 1.0)) for t in late_times
    ]

    return {"task": task_times, "late": late_times}


# ----- Sample-EEG epoch figure ---------------------------------------------

def plot_sample_epochs(
    data: np.ndarray,         # shape (n_samples, n_channels)
    fs: float,
    labels: list[str],
    condition_epochs: list[tuple[str, list[float]]],
    save_path: Path,
    title: str,
    channels: tuple[str, ...] = SAMPLE_CHANNELS,
    epoch_s: float = EPOCH_S,
    fig_height_per_row: float = 1.6,
) -> None:
    """Rows = channels × cols = epochs grid of short EEG traces.

    Each panel's y-axis is independently scaled from that panel's own
    amplitude distribution (2nd / 98th percentile, padded 20%), so a
    saturated / high-amplitude channel doesn't drown out the others and
    a quiet channel still shows its morphology. Demean per-panel so DC
    offsets don't shift the waveform off-screen.
    """
    n_ch = len(channels)
    all_epochs = [(cn, t) for cn, ts in condition_epochs for t in ts]
    n_col = len(all_epochs)

    fig, axes = plt.subplots(
        n_ch, n_col,
        figsize=(1.9 * n_col, fig_height_per_row * n_ch + 1.0),
        sharey=False, sharex=False,
    )
    if n_ch == 1:
        axes = np.array([axes])
    if n_col == 1:
        axes = axes.reshape(-1, 1)

    ch_idx: dict[str, int] = {
        ch: labels.index(ch) for ch in channels if ch in labels
    }

    for r, ch in enumerate(channels):
        if ch not in ch_idx:
            for c in range(n_col):
                axes[r, c].text(
                    0.5, 0.5, f"{ch} not found",
                    transform=axes[r, c].transAxes,
                    ha="center", va="center", fontsize=8,
                )
                axes[r, c].set_xticks([]); axes[r, c].set_yticks([])
            continue
        j = ch_idx[ch]
        for c, (cond_name, t0) in enumerate(all_epochs):
            i0 = int(t0 * fs); i1 = i0 + int(epoch_s * fs)
            if i1 > data.shape[0] or i0 < 0:
                axes[r, c].text(
                    0.5, 0.5, "out of range",
                    transform=axes[r, c].transAxes,
                    ha="center", va="center", fontsize=7, color="grey",
                )
                axes[r, c].set_xticks([]); axes[r, c].set_yticks([])
                continue
            x = data[i0:i1, j].astype(float)
            x = x - float(np.mean(x))
            t = np.arange(x.size) / fs
            # Palette hints by condition name.
            color = "#1f4f99"
            if cond_name.startswith("Sleep") or cond_name.startswith("Rest") \
                    or cond_name == "Late":
                color = "#1b5e3a"
            if cond_name.startswith("Sent-ON") or cond_name == "ON":
                color = "#1f77b4"
            if cond_name.startswith("Sent-OFF") or cond_name == "OFF":
                color = "#d62728"
            if cond_name.startswith("Task"):
                color = "#1f4f99"
            axes[r, c].plot(t, x, lw=0.45, color=color)
            # Per-panel adaptive y range: use a generous percentile so small
            # bursts aren't clipped, floor at ±5 µV so a totally flat
            # channel still has visible gridlines rather than a 0-pixel y
            # range.
            p2, p98 = np.percentile(x, [2.0, 98.0])
            pad = max(5.0, 0.2 * (p98 - p2))
            ylo, yhi = p2 - pad, p98 + pad
            if yhi - ylo < 10.0:
                mid = 0.5 * (ylo + yhi)
                ylo, yhi = mid - 5.0, mid + 5.0
            axes[r, c].set_ylim(ylo, yhi)
            axes[r, c].set_xlim(0, epoch_s)
            axes[r, c].grid(alpha=0.2, lw=0.3)
            # Tiny amplitude tag in the corner so the reader can tell
            # which panel is where on the intensity scale.
            axes[r, c].text(
                0.98, 0.04,
                f"±{int(max(abs(ylo), abs(yhi)))}µV",
                transform=axes[r, c].transAxes,
                ha="right", va="bottom", fontsize=6, color="#666",
            )
            if c == 0:
                axes[r, c].set_ylabel(ch, fontsize=9, rotation=0,
                                      ha="right", va="center")
            if r == 0:
                n_prev = sum(1 for k in range(c+1)
                             if all_epochs[k][0] == cond_name)
                axes[r, c].set_title(
                    f"{cond_name} {n_prev}\nt={t0:.0f}s", fontsize=8,
                )
            if r == n_ch - 1:
                axes[r, c].set_xlabel("s", fontsize=7)
            else:
                axes[r, c].set_xticklabels([])
            axes[r, c].tick_params(axis="both", labelsize=7)

    fig.suptitle(title, fontsize=11, y=0.995)
    fig.text(
        0.01, 0.002,
        f"Y axis auto-scaled per panel (2nd–98th percentile, padded).  "
        f"Each panel = {epoch_s:.0f} s, demeaned.  "
        f"Channels: {', '.join(channels)}.",
        fontsize=7, color="#555",
    )
    fig.tight_layout(rect=(0, 0.02, 1, 0.96))
    fig.savefig(save_path, dpi=140, bbox_inches="tight")
    plt.close(fig)


# ----- Full-spectrum Sentiometer ON/OFF analysis ----------------------------

def welch_psd(x: np.ndarray, fs: float,
              win_s: float = 4.0, overlap: float = 0.5
              ) -> tuple[np.ndarray, np.ndarray]:
    nperseg = int(fs * win_s)
    noverlap = int(nperseg * overlap)
    f, pxx = signal.welch(
        x, fs=fs, nperseg=nperseg, noverlap=noverlap, scaling="density"
    )
    return f, pxx


def compute_psd_matrix(
    data: np.ndarray, fs: float
) -> tuple[np.ndarray, np.ndarray]:
    """Return (freqs, psd_matrix of shape (n_channels, n_freqs))."""
    if data.ndim != 2 or data.shape[1] == 0:
        return np.array([]), np.empty((0, 0))
    freqs, first_pxx = welch_psd(data[:, 0], fs)
    out = np.zeros((data.shape[1], first_pxx.size))
    out[0] = first_pxx
    for j in range(1, data.shape[1]):
        _, out[j] = welch_psd(data[:, j], fs)
    return freqs, out


def band_power_matrix(
    freqs: np.ndarray, psd_matrix: np.ndarray
) -> dict[str, np.ndarray]:
    """Return ``{band_name: per_channel_power_array}``."""
    out: dict[str, np.ndarray] = {}
    for name, lo, hi in BANDS:
        mask = (freqs >= lo) & (freqs < hi)
        if mask.sum() < 2:
            out[name] = np.full(psd_matrix.shape[0], np.nan)
            continue
        # Integrate (µV²/Hz → µV²) via trapezoidal rule.
        out[name] = np.trapezoid(psd_matrix[:, mask], freqs[mask], axis=1)
    return out


def plot_log_ratio_heatmap(
    freqs: np.ndarray,
    psd_on: np.ndarray,
    psd_off: np.ndarray,
    labels: list[str],
    save_path: Path,
    title: str,
    freq_max: float = 60.0,
) -> None:
    """Heatmap of log10(PSD_ON / PSD_OFF) per channel × frequency.

    Positive values (red) = louder with Sentiometer on (noise added).
    Negative values (blue) = louder with Sentiometer off.
    """
    mask = (freqs <= freq_max)
    f = freqs[mask]
    on = psd_on[:, mask]
    off = psd_off[:, mask]
    # Avoid log(0) by flooring.
    eps = 1e-12
    ratio = np.log10((on + eps) / (off + eps))

    # Sort channels by mean log-ratio over 1–40 Hz, descending, so the
    # most affected channels are at the top — scanable at a glance.
    summary = ratio[:, (f >= 1.0) & (f <= 40.0)].mean(axis=1)
    order = np.argsort(-summary)
    ratio_sorted = ratio[order]
    labels_sorted = [labels[i] for i in order]

    fig, ax = plt.subplots(figsize=(10, 0.14 * len(labels_sorted) + 1.8))
    vmax = float(np.nanpercentile(np.abs(ratio_sorted), 98))
    vmax = max(vmax, 0.3)
    im = ax.imshow(
        ratio_sorted, aspect="auto", origin="lower",
        extent=(f.min(), f.max(), -0.5, len(labels_sorted) - 0.5),
        cmap="RdBu_r", vmin=-vmax, vmax=vmax, interpolation="nearest",
    )
    ax.set_yticks(range(len(labels_sorted)))
    ax.set_yticklabels(labels_sorted, fontsize=6)
    ax.set_xlabel("Frequency (Hz)")
    ax.set_title(title)
    ax.axvline(60, color="k", lw=0.4, ls=":")
    ax.axvline(20, color="k", lw=0.4, ls=":")
    cbar = fig.colorbar(im, ax=ax, fraction=0.025, pad=0.02)
    cbar.set_label("log₁₀(ON / OFF)", fontsize=8)
    cbar.ax.tick_params(labelsize=7)
    fig.tight_layout()
    fig.savefig(save_path, dpi=140)
    plt.close(fig)


def build_band_delta_rows(
    psd_on: np.ndarray,
    psd_off: np.ndarray,
    freqs: np.ndarray,
) -> list[list[str]]:
    """Header row + one row per band summarizing the ON/OFF contrast."""
    on_bp = band_power_matrix(freqs, psd_on)
    off_bp = band_power_matrix(freqs, psd_off)
    rows: list[list[str]] = [
        ["Band (Hz)",
         "Median ON (µV²)", "Median OFF (µV²)", "Δ% (ON − OFF) / OFF",
         "Channels louder ON",
         "Channels louder OFF"],
    ]
    for name, lo, hi in BANDS:
        a = on_bp[name]
        b = off_bp[name]
        valid = np.isfinite(a) & np.isfinite(b) & (b > 0)
        if valid.sum() == 0:
            rows.append([f"{name} ({lo:.0f}–{hi:.0f})", "-", "-", "-", "-", "-"])
            continue
        med_on = float(np.median(a[valid]))
        med_off = float(np.median(b[valid]))
        pct = (med_on - med_off) / med_off * 100
        n_up_on = int(np.sum(a[valid] > b[valid]))
        n_up_off = int(valid.sum() - n_up_on)
        rows.append([
            f"{name} ({lo:.0f}–{hi:.0f})",
            f"{med_on:.3f}", f"{med_off:.3f}",
            f"{pct:+.1f}%",
            f"{n_up_on}/{valid.sum()}",
            f"{n_up_off}/{valid.sum()}",
        ])
    return rows
