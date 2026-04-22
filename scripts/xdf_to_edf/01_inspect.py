"""Step 1 — inspect the XDF and BrainVision config before any conversion.

Never transforms data. Prints everything we need to agree on before writing
an EDF:

1. Single XDF detected in sampledata/ (else stop).
2. pyxdf.load_xdf: per-stream name / type / source_id / channel count /
   sample rate / duration / channel labels + units + types.
3. Full info XML for the EEG stream.
4. BrainVision workspace (.rwksp) — extract whatever settings are
   reachable. The file is a Microsoft OLE Compound Document (NOT plain
   XML, despite its extension in the field), so we use ``olefile`` and
   pull readable strings from each internal stream.
5. BrainVision .cfg — parse the [channels] block and cross-check against
   the EEG stream's channel labels.
6. First / last 5 timestamps per stream + across-stream start/end
   offset (LSL clock drift check).
7. Anomaly report: missing channels, sample-rate mismatch, marker-stream
   emptiness, etc.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterable
from configparser import ConfigParser
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import olefile
import pyxdf

# Matplotlib is optional; only needed by the forensic block. Import lazily
# inside the forensic function so the base inspection still runs on a
# dev box without the tasks extra installed.

from _common import (
    CFG_PATH,
    RWKSP_PATH,
    SAMPLE_DIR,
    REF_DIR,
    REPO_ROOT,
    diag_dir_for,
    find_xdf as _find_xdf_common,
    subject_from_xdf,
)

PT = ZoneInfo("America/Los_Angeles")

# BrainAmp Standard at `resolution=0` (from .cfg) = 0.1 µV / LSB, 16-bit
# signed → ±3276.7 µV amplifier input range. We call any sample within
# 2% of the rails "at saturation".
BRAINAMP_PHYSICAL_RANGE_UV = 3276.7
BRAINAMP_SATURATION_THRESHOLD_UV = 3200.0
# Nominal sampling interval at 500 Hz:
EXPECTED_DT_S = 1.0 / 500.0
# Rep channels for the time-course plot (4 anatomical extremes):
REP_CHANNELS = ("Fp1", "Cz", "Oz", "TP9")

SECTION = "=" * 78


def banner(text: str) -> None:
    print(f"\n{SECTION}")
    print(text)
    print(SECTION)


def h2(text: str) -> None:
    print(f"\n--- {text} ---")


# ----- XDF detection + stream helpers ---------------------------------------


def find_xdf() -> Path:
    """Newest .xdf in sampledata/ (ties broken by mtime). See _common.find_xdf."""
    return _find_xdf_common()


def _get(d: object, *keys: str, default: object = "") -> object:
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


def channel_entries(stream: dict) -> list[dict]:
    info = stream.get("info") or {}
    desc = info.get("desc") or []
    if not desc:
        return []
    desc0 = desc[0] if isinstance(desc, list) else desc
    if not isinstance(desc0, dict):
        return []
    chs = desc0.get("channels") or []
    if not chs:
        return []
    chs0 = chs[0] if isinstance(chs, list) else chs
    if not isinstance(chs0, dict):
        return []
    entries = chs0.get("channel") or []
    if not isinstance(entries, list):
        entries = [entries]
    return [c for c in entries if isinstance(c, dict)]


def first_val(obj: object, *keys: str, default: str = "") -> str:
    cur = obj
    for k in keys:
        if isinstance(cur, dict):
            cur = cur.get(k, default)
        else:
            return default
    if isinstance(cur, list) and cur:
        return str(cur[0])
    if cur is None:
        return default
    return str(cur)


# ----- .cfg parser ----------------------------------------------------------


def parse_cfg(cfg_path: Path) -> dict:
    cp = ConfigParser(strict=False)
    cp.read(cfg_path)
    settings = dict(cp["settings"]) if "settings" in cp else {}
    labels_raw = cp["channels"]["labels"] if "channels" in cp else ""
    labels = [x.strip() for x in labels_raw.split(",") if x.strip()]
    return {"settings": settings, "labels": labels, "path": cfg_path}


# ----- .rwksp (OLE) parser --------------------------------------------------


def parse_rwksp(rwksp_path: Path) -> dict:
    """Pull readable settings out of the BrainVision workspace file.

    The .rwksp is a Microsoft OLE Compound Document with multiple named
    streams of proprietary serialized settings. Full deserialization would
    require BrainVision's own library; we fall back to reading the OLE
    structure, dumping per-stream byte sizes, and scanning each stream for
    printable ASCII substrings that reveal the reference-channel name,
    ground channel name, and path references (e.g. the electrode-position
    file the workspace points at).
    """
    if not rwksp_path.exists():
        return {"present": False, "path": rwksp_path}

    ole = olefile.OleFileIO(str(rwksp_path))
    entries: dict[str, dict] = {}
    for entry in ole.listdir():
        path = "/".join(entry)
        try:
            data = ole.openstream(entry).read()
        except Exception as exc:  # noqa: BLE001
            entries[path] = {"error": str(exc)}
            continue
        ascii_parts = [
            a.decode("latin-1", errors="replace")
            for a in re.findall(rb"[\x20-\x7e]{4,}", data)
        ]
        entries[path] = {"size": len(data), "strings": ascii_parts}

    return {"present": True, "path": rwksp_path, "entries": entries}


# ----- forensic on the first 120 s of the EEG stream ------------------------


def _stream_by_name(streams: list[dict], name: str) -> dict | None:
    return next(
        (s for s in streams if first_val(s, "info", "name") == name), None
    )


def _window_mask(ts: np.ndarray, t0: float, start_s: float, end_s: float) -> np.ndarray:
    return (ts >= t0 + start_s) & (ts < t0 + end_s)


def forensic_brainamp_first_120s(streams: list[dict], diag_dir: Path) -> dict:
    """Produce the forensic diagnostic panel + report for Paller.

    Writes two PNGs under *diag_dir* and returns a summary dict that
    feeds the hypothesis section.
    """
    banner("FORENSIC — BrainAmpSeries first 120 s")
    diag_dir.mkdir(parents=True, exist_ok=True)

    eeg = _stream_by_name(streams, "BrainAmpSeries-Dev_1")
    if eeg is None:
        print("  BrainAmpSeries-Dev_1 stream not found; skipping forensic.")
        return {}

    ts_raw = eeg.get("time_stamps")
    if ts_raw is None or len(ts_raw) == 0:
        print("  BrainAmpSeries stream empty; skipping forensic.")
        return {}

    ts = np.asarray(ts_raw, dtype=float)
    data_raw = eeg.get("time_series")
    data = np.asarray(data_raw if data_raw is not None else [], dtype=float)
    labels = [first_val(c, "label") for c in channel_entries(eeg)]
    t0 = float(ts[0])
    mask = _window_mask(ts, t0, 0.0, 120.0)
    ts120 = ts[mask] - t0
    data120 = data[mask, :]
    print(f"  samples in first 120 s: {ts120.size} "
          f"(expected ~{120*500} @ 500 Hz)")
    print(f"  channels inspected    : {len(labels)}")

    results: dict[str, object] = {}

    # ----- (1a) timestamp discontinuities -----
    h2("Timestamp continuity in first 120 s")
    dts = np.diff(ts120)
    # Threshold: anything off by >25% of nominal dt (i.e. < 1.5 ms or > 2.5 ms).
    tolerance = 0.25 * EXPECTED_DT_S
    odd = np.where(np.abs(dts - EXPECTED_DT_S) > tolerance)[0]
    dup = np.where(dts == 0)[0]
    print(f"  nominal inter-sample dt : {EXPECTED_DT_S*1000:.3f} ms")
    print(f"  observed median dt      : {np.median(dts)*1000:.3f} ms")
    print(f"  min dt / max dt         : "
          f"{dts.min()*1000:.3f} ms / {dts.max()*1000:.3f} ms")
    print(f"  # samples off-dt > ±25%: {odd.size}")
    print(f"  # duplicate timestamps : {dup.size}")
    if odd.size:
        print(f"  first 10 anomalous dt indices (sec / dt ms):")
        for i in odd[:10]:
            print(f"    idx {i:>7d}  t={ts120[i]:.3f}s  dt={dts[i]*1000:.3f}ms")
    results["ts_anomalies"] = int(odd.size)
    results["ts_duplicates"] = int(dup.size)

    # ----- (1b) saturation / clipping -----
    h2("Saturation / clipping (|v| > 3200 µV)")
    sat_counts = (np.abs(data120) > BRAINAMP_SATURATION_THRESHOLD_UV).sum(axis=0)
    sat_rows = [
        (labels[i], int(sat_counts[i]))
        for i in range(len(labels))
        if sat_counts[i] > 0
    ]
    sat_rows.sort(key=lambda r: r[1], reverse=True)
    if sat_rows:
        print(f"  channels with any saturated samples in first 120 s:")
        for label, n in sat_rows[:20]:
            pct = 100.0 * n / data120.shape[0]
            print(f"    {label:<6}  {n:>7d} samples ({pct:.2f} %)")
    else:
        print("  no channel reached the ±3200 µV amplifier rails.")
    results["saturation_rows"] = sat_rows

    # ----- (1c) flat-lined channels -----
    h2("Flat-lined channels (variance < 0.01 µV² in any 5 s window)")
    win_s = 5.0
    n_wins = int(120 // win_s)
    flat_hits: list[tuple[str, int, float]] = []
    for i in range(n_wins):
        w_mask = (ts120 >= i * win_s) & (ts120 < (i + 1) * win_s)
        seg = data120[w_mask, :]
        if seg.shape[0] < 50:
            continue
        variances = seg.var(axis=0, ddof=0)
        for ci, v in enumerate(variances):
            if v < 0.01:
                flat_hits.append((labels[ci], i, float(v)))
    if flat_hits:
        print(f"  {len(flat_hits)} (channel × window) flat hits:")
        flat_hits.sort(key=lambda r: (r[1], r[0]))
        for label, wi, v in flat_hits[:30]:
            print(f"    window {wi:>2d} [{wi*int(win_s):>3d}–{(wi+1)*int(win_s):>3d}s]  "
                  f"{label:<6}  variance={v:.6f}")
    else:
        print("  no flat-lined windows detected.")
    results["flat_hits"] = flat_hits

    # Also carry the split-analysis arrays for the hypothesis block.
    def _split_stats(m: np.ndarray) -> dict:
        seg = data120[m, :]
        if seg.shape[0] == 0:
            return {"n": 0, "sat_channels": 0, "mean_rms": 0.0}
        sat = (np.abs(seg) > BRAINAMP_SATURATION_THRESHOLD_UV).sum(axis=0)
        return {
            "n": int(seg.shape[0]),
            "sat_channels": int((sat > 0.1 * seg.shape[0]).sum()),
            "mean_rms": float(np.mean(np.sqrt((seg ** 2).mean(axis=0)))),
        }

    split_a = (ts120 >= 0) & (ts120 < 55)
    split_b = (ts120 >= 55) & (ts120 < 120)
    results["split_pre55"] = _split_stats(split_a)
    results["split_post55"] = _split_stats(split_b)

    # ----- split analysis: pre-55 s vs post-55 s -----
    h2("Split analysis: [0, 55 s]  vs  [55 s, 120 s]")
    split_a = (ts120 >= 0) & (ts120 < 55)
    split_b = (ts120 >= 55) & (ts120 < 120)
    for tag, mask_s in (("pre-55 ", split_a), ("post-55", split_b)):
        seg = data120[mask_s, :]
        if seg.shape[0] == 0:
            print(f"  {tag}: no samples in window")
            continue
        sat = (np.abs(seg) > BRAINAMP_SATURATION_THRESHOLD_UV).sum(axis=0)
        rms_ch = np.sqrt((seg ** 2).mean(axis=0))
        rms_mean = float(np.mean(rms_ch))
        rms_med = float(np.median(rms_ch))
        n_sat_ch = int((sat > 0.1 * seg.shape[0]).sum())  # >10% saturated
        print(
            f"  {tag}:  samples={seg.shape[0]:>6d}  "
            f"mean RMS={rms_mean:>8.1f} µV  "
            f"median RMS={rms_med:>8.1f} µV  "
            f"channels >10%-sat={n_sat_ch}"
        )

    # ----- (1a, visual) 1-s rolling RMS heatmap -----
    h2("Writing RMS heatmap PNG (1-s windows)")
    import matplotlib  # noqa: PLC0415

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt  # noqa: PLC0415

    n_sec = 120
    rms = np.zeros((n_sec, data120.shape[1]), dtype=float)
    for i in range(n_sec):
        w = (ts120 >= i) & (ts120 < i + 1)
        seg = data120[w, :]
        if seg.shape[0]:
            rms[i, :] = np.sqrt((seg ** 2).mean(axis=0))
        else:
            rms[i, :] = np.nan

    fig, ax = plt.subplots(figsize=(14, 12))
    im = ax.imshow(
        rms.T,
        aspect="auto",
        origin="lower",
        cmap="magma",
        vmin=0,
        vmax=np.nanpercentile(rms, 99),
        extent=(0, n_sec, -0.5, rms.shape[1] - 0.5),
    )
    ax.set_xlabel("seconds from BrainAmpSeries stream start")
    ax.set_ylabel("channel index")
    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels, fontsize=6)
    ax.set_title(
        "BrainAmpSeries-Dev_1  —  per-channel RMS (µV), 1-s windows, first 120 s"
    )
    cbar = fig.colorbar(im, ax=ax, shrink=0.8)
    cbar.set_label("RMS (µV)")
    # mark 55 s with a vertical dashed line
    ax.axvline(55, color="white", linestyle="--", linewidth=1.2, alpha=0.8)
    ax.text(55.5, rms.shape[1] - 1, "t=55 s (reported)", color="white",
            fontsize=8, va="top")
    fig.tight_layout()
    rms_path = diag_dir /"eeg_first_120s_rms.png"
    fig.savefig(rms_path, dpi=140)
    plt.close(fig)
    print(f"  wrote {rms_path}")
    results["rms_png"] = str(rms_path)

    # ----- (4) raw traces for 4 representative channels -----
    h2("Writing raw trace PNG (Fp1, Cz, Oz, TP9)")
    picks = [(lbl, labels.index(lbl)) for lbl in REP_CHANNELS if lbl in labels]
    # Taller per-row (3.6 in vs 2.2 in) so each trace has room; sharey
    # off so a saturating TP9 doesn't squash a clean Oz.
    fig, axes = plt.subplots(len(picks), 1, figsize=(14, 3.6 * len(picks)),
                             sharex=True, sharey=False)
    if len(picks) == 1:
        axes = [axes]
    for ax, (lbl, ci) in zip(axes, picks):
        x = data120[:, ci]
        # Demeaned per channel so DC offset doesn't push the trace off-screen.
        x_dc = x - float(np.nanmean(x))
        ax.plot(ts120, x_dc, linewidth=0.35)
        ax.axvline(55, color="red", linestyle="--", linewidth=0.8, alpha=0.7)
        ax.set_ylabel(f"{lbl}  (µV)")
        # Adaptive y from 2–98 percentile + 20% pad; never collapse to
        # zero, never clip dramatic saturation spikes off-screen.
        p2, p98 = np.nanpercentile(x_dc, [2.0, 98.0])
        pad = max(5.0, 0.2 * (p98 - p2))
        ax.set_ylim(p2 - pad, p98 + pad)
        ax.grid(alpha=0.3)
    axes[-1].set_xlabel("seconds from stream start")
    fig.suptitle(
        "BrainAmpSeries-Dev_1  —  first 120 s raw traces, representative channels "
        "(red dashed = t=55 s)."
        "  Each row demeaned + auto-scaled per channel."
    )
    fig.tight_layout()
    traces_path = diag_dir /"eeg_time_course_first_2min.png"
    fig.savefig(traces_path, dpi=140)
    plt.close(fig)
    print(f"  wrote {traces_path}")
    results["traces_png"] = str(traces_path)

    # ----- (2) marker alignment in first 120 s -----
    h2("Markers in first 120 s (relative to BrainAmpSeries start)")
    marker_hits: list[tuple[float, str, str]] = []
    for s in streams:
        info = s.get("info") or {}
        if first_val(info, "type").lower() != "markers":
            continue
        name = first_val(info, "name")
        ts_raw_m = s.get("time_stamps")
        if ts_raw_m is None or len(ts_raw_m) == 0:
            print(f"  {name}: (stream is empty)")
            continue
        tsm = np.asarray(ts_raw_m, dtype=float)
        samples_raw = s.get("time_series")
        samples = samples_raw if samples_raw is not None else []
        for t, v in zip(tsm, samples):
            rel = float(t - t0)
            if 0 <= rel <= 120:
                val = v[0] if isinstance(v, (list, tuple)) and v else str(v)
                marker_hits.append((rel, name, str(val)))
    if not marker_hits:
        print("  (no markers fired in the first 120 s on any marker stream)")
    else:
        marker_hits.sort()
        for rel, name, val in marker_hits[:80]:
            print(f"  t={rel:>7.3f}s  [{name}]  {val}")
    results["marker_hits_first_120s"] = len(marker_hits)

    # ----- (3) cross-stream integrity at t ≈ 55 s -----
    h2("Cross-stream snapshot at BrainAmp relative t ≈ 55 s")
    abs_t = t0 + 55.0
    for s in streams:
        info = s.get("info") or {}
        stype = first_val(info, "type").lower()
        name = first_val(info, "name")
        if stype == "markers":
            continue
        ts_raw_s = s.get("time_stamps")
        if ts_raw_s is None or len(ts_raw_s) == 0:
            continue
        tss = np.asarray(ts_raw_s, dtype=float)
        # Nearest sample to abs_t
        if tss[0] > abs_t or tss[-1] < abs_t:
            print(f"  {name}: abs_t=55s outside this stream's range "
                  f"(stream {tss[0]-t0:+.1f}..{tss[-1]-t0:+.1f} s from EEG start).")
            continue
        # Slice ±1 s around abs_t
        slice_mask = (tss >= abs_t - 1.0) & (tss <= abs_t + 1.0)
        seg_raw = s.get("time_series")
        seg = np.asarray(seg_raw if seg_raw is not None else [])[slice_mask]
        if seg.size == 0:
            print(f"  {name}: no samples in ±1 s of t=55s.")
            continue
        # Per-channel RMS on the 2 s window
        if seg.ndim == 2:
            rms_vals = np.sqrt((seg ** 2).mean(axis=0))
            ch_names = [first_val(c, "label") for c in channel_entries(s)]
            if len(ch_names) != seg.shape[1]:
                ch_names = [f"ch{i}" for i in range(seg.shape[1])]
            print(f"  {name}  ±1 s RMS per channel:")
            for nm, v in zip(ch_names, rms_vals):
                flag = "  SATURATED" if v > 1e4 else ""
                print(f"    {nm:<20}  {v:>10.3f}{flag}")
        else:
            rms_val = float(np.sqrt((seg ** 2).mean()))
            print(f"  {name}: ±1 s RMS = {rms_val:.3f}")

    return results


def hypothesis_panel(forensic: dict) -> None:
    banner("HYPOTHESIS — first 55 s of BrainAmpSeries")
    if not forensic:
        print("  (no forensic data)")
        return

    n_sat_channels = len(forensic.get("saturation_rows", []))
    ts_anomalies = forensic.get("ts_anomalies", 0)
    ts_dups = forensic.get("ts_duplicates", 0)
    n_flat_hits = len(forensic.get("flat_hits", []))
    n_markers = forensic.get("marker_hits_first_120s", 0)
    pre = forensic.get("split_pre55", {})
    post = forensic.get("split_post55", {})

    print(f"  channels with any saturated sample : {n_sat_channels}")
    print(f"  off-dt timestamp anomalies         : {ts_anomalies}")
    print(f"  duplicate timestamps               : {ts_dups}")
    print(f"  flat (channel × 5-s window) hits   : {n_flat_hits}")
    print(f"  markers in first 120 s             : {n_markers}")
    if pre and post:
        print(f"  pre-55 s : mean RMS {pre.get('mean_rms', 0):.1f} µV, "
              f"{pre.get('sat_channels', 0)} channels >10%-sat")
        print(f"  post-55 s: mean RMS {post.get('mean_rms', 0):.1f} µV, "
              f"{post.get('sat_channels', 0)} channels >10%-sat")

    print("\n  Candidate explanations:")
    print("  (a) impedance check artifact")
    print("  (b) electrode settling / baseline drift")
    print("  (c) LSL buffer discontinuity / clock glitch")
    print("  (d) amplifier failure (rail, saturation, or dead channel)")
    print("  (e) legitimate setup noise before recording began cleanly")
    print("  (f) other")

    # Heuristic ranking from the collected evidence.
    ranked: list[tuple[str, float, str]] = []
    if ts_anomalies > 50 or ts_dups > 0:
        ranked.append(
            ("c",
             0.7,
             f"{ts_anomalies} off-dt and {ts_dups} duplicate timestamps "
             "in the first 120 s point at LSL buffering / clock sync.")
        )
    if n_sat_channels > 8:
        ranked.append(
            ("d",
             0.6,
             f"{n_sat_channels} channels hit the ±3.2 mV rails — "
             "suggests amplifier saturation, not a transient settling.")
        )
    if n_flat_hits > 0:
        ranked.append(
            ("d",
             0.4,
             f"{n_flat_hits} (channel × 5-s window) hits are essentially "
             "flat, consistent with a dead reference or disconnected lead.")
        )
    # Baseline settling hypothesis (b/e) is most common when:
    # - saturation and ts anomalies are both low
    # - RMS is high early and lower later (read off the heatmap PNG)
    if n_sat_channels < 3 and ts_anomalies < 10 and n_flat_hits == 0:
        ranked.append(
            ("b/e",
             0.6,
             "Few saturations, clean timestamps, no flat channels — "
             "most likely amplifier settling / AC-coupling transient + "
             "electrode gel settling; typically clears within 30–120 s.")
        )

    if not ranked:
        print("\n  No strong evidence either way — inspect the PNGs.")
        return

    ranked.sort(key=lambda x: -x[1])
    print("\n  Most likely, in priority order:")
    for tag, conf, msg in ranked:
        print(f"    ({tag})  confidence {conf*100:.0f} %  —  {msg}")
    print(
        "\n  Also check eeg_first_120s_rms.png for a per-channel RMS "
        "timeline; the vertical dashed line is at the 55 s mark."
    )


# ----- main -----------------------------------------------------------------


def main() -> int:
    banner("P013 XDF INSPECTION — Step 1 (read-only)")
    print(f"Repo root: {REPO_ROOT}")
    print(f"Sample dir: {SAMPLE_DIR}")
    print(f"Reference dir: {REF_DIR}")

    # ----- 1. Find the XDF -----
    xdf_path = find_xdf()
    stat = xdf_path.stat()
    print(f"\nDetected XDF: {xdf_path.name}")
    print(f"  size: {stat.st_size/1e6:.1f} MB")
    mtime_pt = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).astimezone(PT)
    print(f"  mtime (PT): {mtime_pt.strftime('%Y-%m-%d %I:%M:%S %p %Z')}")

    # ----- 2. Load XDF -----
    print("\nLoading XDF via pyxdf.load_xdf (synchronize_clocks=default) …")
    streams, header = pyxdf.load_xdf(str(xdf_path))
    print(f"  XDF version: {first_val(header, 'info', 'version')}")
    print(f"  streams in file: {len(streams)}")

    # ----- 3. Per-stream summary -----
    banner("PER-STREAM SUMMARY")
    stream_rows: list[dict] = []
    for s in streams:
        info = s.get("info") or {}
        name = first_val(info, "name")
        stype = first_val(info, "type")
        source_id = first_val(info, "source_id")
        nch = first_val(info, "channel_count")
        srate = first_val(info, "nominal_srate")
        manuf = first_val(info, "manufacturer")
        ts_raw = s.get("time_stamps")
        ts = np.asarray(ts_raw if ts_raw is not None else [], dtype=float)
        n_samp = len(ts)
        duration = (float(ts[-1] - ts[0]) if n_samp > 1 else 0.0)
        first_ts = float(ts[0]) if n_samp else float("nan")
        last_ts = float(ts[-1]) if n_samp else float("nan")
        effective_hz = n_samp / duration if duration else 0.0

        print(f"\nstream {name!r}")
        print(f"  type           : {stype}")
        print(f"  source_id      : {source_id}")
        print(f"  manufacturer   : {manuf!r}")
        print(f"  channel_count  : {nch}")
        print(f"  nominal_srate  : {srate} Hz")
        print(f"  samples        : {n_samp}")
        print(f"  lsl first_ts   : {first_ts:.3f}")
        print(f"  lsl last_ts    : {last_ts:.3f}")
        print(f"  duration       : {duration:.3f} s ({duration/60:.2f} min)")
        print(f"  effective rate : {effective_hz:.3f} Hz")

        # Channel table
        chs = channel_entries(s)
        if chs:
            print(f"  channels ({len(chs)}):")
            for j, ch in enumerate(chs):
                label = first_val(ch, "label")
                unit = first_val(ch, "unit")
                chtype = first_val(ch, "type")
                print(f"    {j:3d}  {label:<24}  type={chtype:<12} unit={unit}")
        else:
            print("  channels: no metadata")

        stream_rows.append(
            {
                "name": name,
                "type": stype,
                "srate_nominal": float(srate) if srate else 0.0,
                "effective_hz": effective_hz,
                "n_samp": n_samp,
                "first_ts": first_ts,
                "last_ts": last_ts,
                "duration": duration,
                "channel_labels": [first_val(ch, "label") for ch in chs],
            }
        )

    # ----- 4. Full info XML for EEG stream -----
    banner("FULL INFO FOR EEG STREAM  (stream with type=EEG and max channels)")
    eeg_stream = max(
        (s for s in streams if first_val(s, "info", "type").lower() == "eeg"),
        default=None,
        key=lambda s: int(first_val(s, "info", "channel_count") or 0),
    )
    if eeg_stream is None:
        print("  (no EEG-type stream found)")
    else:
        name = first_val(eeg_stream, "info", "name")
        print(f"EEG stream picked: {name!r}")
        import json

        # pyxdf returns nested dicts; json.dumps renders them readably.
        info = eeg_stream.get("info") or {}
        print(json.dumps(info, indent=2, default=str)[:6000])

    # ----- 5. Cross-check .cfg -----
    banner("CROSS-CHECK: BrainVision .cfg vs EEG stream channel labels")
    if not CFG_PATH.exists():
        print(f"  (.cfg missing at {CFG_PATH})")
    else:
        cfg = parse_cfg(CFG_PATH)
        print(f"  .cfg path: {CFG_PATH}")
        print(f"  .cfg settings: {cfg['settings']}")
        print(f"  .cfg channel count: {len(cfg['labels'])}")
        print(f"  .cfg first 6 labels: {cfg['labels'][:6]}")
        print(f"  .cfg last 6 labels: {cfg['labels'][-6:]}")

        if eeg_stream is not None:
            xdf_labels = [
                first_val(c, "label") for c in channel_entries(eeg_stream)
            ]
            same = xdf_labels == cfg["labels"]
            print(f"  match with XDF EEG channel order: {same}")
            if not same:
                # Diff per-index
                mismatched = [
                    (i, cfg["labels"][i] if i < len(cfg["labels"]) else "",
                     xdf_labels[i] if i < len(xdf_labels) else "")
                    for i in range(max(len(cfg["labels"]), len(xdf_labels)))
                    if (i >= len(cfg["labels"]) or i >= len(xdf_labels)
                        or cfg["labels"][i] != xdf_labels[i])
                ]
                print(f"  mismatches: {len(mismatched)}")
                for i, c, x in mismatched[:20]:
                    print(f"    idx {i:3d}: cfg={c!r}  xdf={x!r}")

    # ----- 6. Workspace (.rwksp) ------
    banner("BRAINVISION WORKSPACE (.rwksp) — OLE compound, readable strings")
    wksp = parse_rwksp(RWKSP_PATH)
    if not wksp["present"]:
        print(f"  (file missing at {RWKSP_PATH})")
    else:
        print(f"  path: {wksp['path']}")
        for stream_path, meta in wksp["entries"].items():
            h2(stream_path)
            if "error" in meta:
                print(f"  error: {meta['error']}")
                continue
            print(f"  bytes: {meta['size']}")
            strings = meta["strings"]
            # Show things that look like filesystem paths / electrode names /
            # filter labels. Filter to ≤120 char lines, dedup, keep order.
            seen: set[str] = set()
            for s_ in strings:
                if len(s_) > 120:
                    continue
                if s_ in seen:
                    continue
                seen.add(s_)
                print(f"  {s_}")
        print(
            "\n  NOTE: .rwksp is a proprietary OLE compound format. "
            "Full filter-coefficient / reference-channel parameters "
            "live in serialized binary blobs we cannot decode without "
            "BrainVision's own library. We rely on the .cfg (`dccoupling=0` "
            "= AC coupled, standard BrainAmp DC ≈ 10 s time constant = "
            "~0.016 Hz HP) and the stated P013 montage (FCz online ref, "
            "AFz ground — actiCAP 64 default) in Step 2."
        )

    # ----- 7. Timestamps & clock drift -----
    banner("FIRST / LAST 5 TIMESTAMPS PER STREAM + CLOCK DRIFT")
    for row in stream_rows:
        print(f"\n{row['name']!r}")
        s = next(s for s in streams if first_val(s, "info", "name") == row["name"])
        ts_raw = s.get("time_stamps")
        ts = np.asarray(ts_raw if ts_raw is not None else [], dtype=float)
        if len(ts) == 0:
            print("  (no samples)")
            continue
        print(f"  first 5: {['%.3f' % t for t in ts[:5]]}")
        print(f"  last  5: {['%.3f' % t for t in ts[-5:]]}")

    # Start-offset + end-offset between all populated streams
    populated = [r for r in stream_rows if r["n_samp"] > 1]
    if len(populated) >= 2:
        earliest_start = min(r["first_ts"] for r in populated)
        latest_start = max(r["first_ts"] for r in populated)
        earliest_end = min(r["last_ts"] for r in populated)
        latest_end = max(r["last_ts"] for r in populated)
        print(f"\nearliest stream start (LSL): {earliest_start:.3f}")
        print(f"latest   stream start (LSL): {latest_start:.3f}")
        print(f"start spread: {latest_start - earliest_start:.3f} s")
        print(f"earliest stream end   (LSL): {earliest_end:.3f}")
        print(f"latest   stream end   (LSL): {latest_end:.3f}")
        print(f"end spread:   {latest_end - earliest_end:.3f} s")

    # ----- 8. Anomalies -----
    banner("ANOMALY REPORT")
    anomalies: list[str] = []

    # a) Effective rate vs nominal rate
    for row in stream_rows:
        if row["srate_nominal"] > 0 and row["n_samp"] > 1:
            drift = abs(row["effective_hz"] - row["srate_nominal"]) / row["srate_nominal"]
            if drift > 0.01:
                anomalies.append(
                    f"sample-rate drift {drift*100:.2f}% on {row['name']!r} "
                    f"(nominal {row['srate_nominal']} Hz, effective "
                    f"{row['effective_hz']:.3f} Hz)"
                )

    # b) Cross-stream start offsets (LSL clock spread)
    if len(populated) >= 2:
        spread = latest_start - earliest_start
        if spread > 5.0:
            anomalies.append(
                f"stream start spread = {spread:.1f} s — "
                "streams joined the LSL network at noticeably different "
                "times; trim to the later start in Step 2."
            )

    # c) Marker streams that are empty
    for row in stream_rows:
        if row["type"].lower() == "markers" and row["n_samp"] == 0:
            anomalies.append(
                f"marker stream {row['name']!r} has 0 samples — "
                "no markers will end up as EDF annotations."
            )

    # d) Stream duration spread
    if len(populated) >= 2:
        durs = [r["duration"] for r in populated]
        if max(durs) - min(durs) > 10:
            anomalies.append(
                f"stream duration spread = {max(durs)-min(durs):.1f} s — "
                "the shorter stream(s) define the trim window in Step 2."
            )

    # e) Sentiometer: count channels, verify label sanity
    for row in stream_rows:
        if row["name"].lower() == "sentiometer":
            if len(row["channel_labels"]) != 6:
                anomalies.append(
                    f"Sentiometer stream has {len(row['channel_labels'])} "
                    "channels (expected 6: device_ts + PD1..PD5)."
                )

    # f) EEG/CGX sample-rate mismatch (both should be 500)
    eeg_rate = next(
        (r["srate_nominal"] for r in stream_rows if r["type"].lower() == "eeg"),
        None,
    )
    cgx_rate = next(
        (r["srate_nominal"] for r in stream_rows if r["name"].startswith("CGX")),
        None,
    )
    if eeg_rate and cgx_rate and abs(eeg_rate - cgx_rate) > 0.5:
        anomalies.append(
            f"EEG nominal rate ({eeg_rate}) != CGX nominal rate ({cgx_rate})"
        )

    if anomalies:
        for a in anomalies:
            print(f"  * {a}")
    else:
        print("  (none detected)")

    # ----- 9. Forensic on BrainAmpSeries first 120 s -----
    subject = subject_from_xdf(xdf_path)
    diag_dir = diag_dir_for(subject)
    print(f"\nOutput subject ID: {subject}")
    print(f"Diagnostic PNGs will be written to: {diag_dir}")
    forensic = forensic_brainamp_first_120s(streams, diag_dir)
    hypothesis_panel(forensic)

    banner("END OF STEP 1 — review above, then run Step 2")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
