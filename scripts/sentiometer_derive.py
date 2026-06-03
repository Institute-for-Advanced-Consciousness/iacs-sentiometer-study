"""Derive a standalone, analysis-ready Sentiometer bundle from a session XDF.

For the Sentiometer-only arm of P013 we don't need EEG/CGX. This script
loads ONLY the ``Sentiometer`` stream and ``P013_Task_Markers`` from the
XDF and writes a small parquet bundle under
``outputs/<stem>/preprocessed/``.

Continuous time-series (``sentiometer_timeseries.parquet``), one row per
500 Hz Sentiometer sample, all on the synchronised LSL clock:
  * ``t_lsl``     — LSL timestamp, seconds (float64; keep full precision)
  * ``t_rel_s``   — seconds relative to the ``session_start`` marker
  * ``device_ts`` — Sentiometer internal counter (reference only; NOT in
                    any derived series)
  * ``PD1``..``PD5`` — the five raw photodiode channels
  * ``sent_mean`` — mean across PD1..PD5 (per sample)
  * ``pc1``       — 1st principal component (largest eigenvalue) of
                    PD1..PD5, center-only / covariance PCA, projected to a
                    1-D series. Sign-oriented to correlate positively with
                    ``sent_mean`` so the sign is reproducible.
  * ``task``      — block per sample: task00..task05 / pre_session /
                    between_tasks / post_session_nap

Markers (``sentiometer_markers.parquet``), one row per P013 marker:
  ``t_lsl``, ``t_rel_s``, ``label``, ``task``, ``event``, ``is_json``

Plus ``sentiometer_pca.json`` (PC1 loadings + full eigen-spectrum /
variance explained, channel stats, sampling info) and a ``README.md``.

PCA is fit over the FULL recording (task suite + nap).

Usage (pandas/pyarrow injected per-run; they are not project deps):
    uv run --with pandas --with pyarrow python scripts/sentiometer_derive.py [session.xdf]
Default XDF: sample-data/P013_S01.xdf
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pyxdf

REPO = Path(__file__).resolve().parents[1]
XDF = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "sample-data" / "P013_S01.xdf"
SUBJECT = XDF.stem  # e.g. "P013_S01"
OUTDIR = REPO / "outputs" / SUBJECT / "preprocessed"


def channel_labels(stream):
    try:
        chans = stream["info"]["desc"][0]["channels"][0]["channel"]
        labels = [c["label"][0] for c in chans]
        return labels if all(labels) else None
    except (KeyError, IndexError, TypeError):
        return None


def main() -> None:
    if not XDF.exists():
        raise SystemExit(f"XDF not found: {XDF}")
    OUTDIR.mkdir(parents=True, exist_ok=True)

    streams, _ = pyxdf.load_xdf(
        str(XDF),
        select_streams=[{"name": "Sentiometer"}, {"name": "P013_Task_Markers"}],
        dejitter_timestamps=True,   # regularise the nominally-500 Hz clock
        synchronize_clocks=True,    # put both streams on a common LSL clock
    )
    by_name = {s["info"]["name"][0]: s for s in streams}
    if "Sentiometer" not in by_name or "P013_Task_Markers" not in by_name:
        raise SystemExit(f"Missing required streams; got {list(by_name)}")
    sent, mk = by_name["Sentiometer"], by_name["P013_Task_Markers"]

    ts = np.asarray(sent["time_stamps"], dtype=np.float64)
    X = np.asarray(sent["time_series"], dtype=np.float64)  # (n, 6)
    # HARD GUARD: the 2 GB read must not silently come back empty.
    if ts.size == 0 or X.size == 0 or X.ndim != 2:
        raise SystemExit(f"Sentiometer loaded empty/malformed: ts={ts.shape} X={X.shape}")
    n, nch = X.shape

    labels = channel_labels(sent) or ["device_ts", "PD1", "PD2", "PD3", "PD4", "PD5"][:nch]
    lab2idx = {l: i for i, l in enumerate(labels)}
    pd_labels = [l for l in labels if l.lower().startswith("pd")]
    if len(pd_labels) != 5:
        pd_labels = [l for l in labels if not ("ts" in l.lower() or "time" in l.lower())]
    if len(pd_labels) != 5:
        raise SystemExit(f"Could not identify 5 photodiodes from labels: {labels}")
    dev_label = next((l for l in labels if l not in pd_labels), None)

    PD = np.column_stack([X[:, lab2idx[l]] for l in pd_labels])  # (n, 5)
    device_ts = X[:, lab2idx[dev_label]] if dev_label else np.full(n, np.nan)
    sent_mean = PD.mean(axis=1)

    # ---- PC1: center-only / covariance PCA (5x5 eigendecomp) -------------
    mu = PD.mean(axis=0)
    cov = np.cov(PD, rowvar=False)
    evals, evecs = np.linalg.eigh(cov)            # ascending
    order = np.argsort(evals)[::-1]
    evals, evecs = evals[order], evecs[:, order]
    v1 = evecs[:, 0]
    pc1 = (PD - mu) @ v1
    if np.corrcoef(pc1, sent_mean)[0, 1] < 0:     # reproducible sign
        v1, pc1 = -v1, -pc1
    evr = (evals / evals.sum()).astype(float)

    # ---- markers ----------------------------------------------------------
    mlabels = [r[0] for r in mk["time_series"]]
    mts = np.asarray(mk["time_stamps"], dtype=np.float64)
    if len(mlabels) == 0:
        raise SystemExit("Marker stream loaded empty.")
    sess0 = mts[mlabels.index("session_start")] if "session_start" in mlabels else ts.min()
    sess_end = mts[mlabels.index("session_end")] if "session_end" in mlabels else ts.max()

    def parse_marker(label):
        if label.startswith("{"):
            try:
                return ("task05", str(json.loads(label).get("event", "json")), True)
            except json.JSONDecodeError:
                return ("other", "json", True)
        if label.startswith("task") and "_" in label and label[4:6].isdigit():
            return (label[:6], label[7:], False)
        if label.startswith(("session", "participant")):
            return ("meta", label, False)
        return ("other", label, False)

    parsed = [parse_marker(l) for l in mlabels]
    markers_df = pd.DataFrame({
        "t_lsl": mts,
        "t_rel_s": mts - sess0,
        "label": mlabels,
        "task": [p[0] for p in parsed],
        "event": [p[1] for p in parsed],
        "is_json": [p[2] for p in parsed],
    })

    # ---- per-sample task block -------------------------------------------
    task_col = np.full(n, "between_tasks", dtype=object)
    task_col[ts < sess0] = "pre_session"
    task_col[ts > sess_end] = "post_session_nap"
    for p in ("task00", "task01", "task02", "task03", "task04", "task05"):
        if f"{p}_start" in mlabels and f"{p}_end" in mlabels:
            s = mts[mlabels.index(f"{p}_start")]
            e = mts[mlabels.index(f"{p}_end")]
            task_col[(ts >= s) & (ts <= e)] = p

    # ---- assemble continuous frame ---------------------------------------
    cont = {
        "t_lsl": ts,
        "t_rel_s": ts - sess0,
        "device_ts": device_ts.astype(np.float64),
    }
    for i, l in enumerate(pd_labels):
        cont[l] = PD[:, i].astype(np.float32)
    cont["sent_mean"] = sent_mean.astype(np.float32)
    cont["pc1"] = pc1.astype(np.float32)
    cont["task"] = pd.Categorical(task_col.astype(str))
    cont_df = pd.DataFrame(cont)

    # HARD GUARDS before writing.
    assert len(cont_df) == n and n > 1_000_000, f"cont_df rows={len(cont_df)} n={n}"
    assert len(markers_df) == len(mlabels) > 0, f"markers rows={len(markers_df)}"

    ts_path = OUTDIR / "sentiometer_timeseries.parquet"
    mk_path = OUTDIR / "sentiometer_markers.parquet"
    cont_df.to_parquet(ts_path, index=False, engine="pyarrow", compression="snappy")
    markers_df.to_parquet(mk_path, index=False, engine="pyarrow", compression="snappy")

    fs = float(1.0 / np.median(np.diff(ts)))
    dur = float(ts[-1] - ts[0])
    meta = {
        "source_xdf": str(XDF),
        "subject": SUBJECT,
        "n_samples": int(n),
        "duration_s": dur,
        "duration_hms": f"{int(dur//3600)}h{int(dur%3600//60):02d}m{int(dur%60):02d}s",
        "fs_estimated_hz": fs,
        "session_start_lsl": float(sess0),
        "session_end_lsl": float(sess_end),
        "photodiode_channels": pd_labels,
        "device_ts_channel": dev_label,
        "pca": {
            "method": "covariance / center-only (channels NOT z-scored)",
            "fit_window": "full recording (task suite + nap)",
            "column": "pc1",
            "sign_convention": "oriented to correlate positively with sent_mean",
            "pc1_loadings": {k: round(float(v), 6) for k, v in zip(pd_labels, v1)},
            "explained_variance_ratio": [round(float(v), 6) for v in evr],
            "pc1_variance_explained": round(float(evr[0]), 6),
            "channel_means": {k: round(float(v), 6) for k, v in zip(pd_labels, mu)},
        },
        "n_markers": int(len(mlabels)),
    }
    (OUTDIR / "sentiometer_pca.json").write_text(json.dumps(meta, indent=2))

    readme = (
        f"# Sentiometer-only bundle - {SUBJECT}\n\n"
        f"Derived from `{XDF.name}` by `scripts/sentiometer_derive.py` (Sentiometer "
        f"stream only, full recording incl. nap, synchronised LSL clock).\n\n"
        f"## sentiometer_timeseries.parquet  ({n:,} rows @ ~{fs:.1f} Hz, {meta['duration_hms']})\n"
        f"| column | meaning |\n|---|---|\n"
        f"| t_lsl | LSL timestamp, seconds (float64 - keep full precision) |\n"
        f"| t_rel_s | seconds relative to session_start |\n"
        f"| device_ts | Sentiometer internal counter (reference only; not a derived series) |\n"
        f"| {pd_labels[0]}..{pd_labels[-1]} | raw photodiode channels |\n"
        f"| sent_mean | mean across the 5 photodiodes |\n"
        f"| pc1 | 1st principal component (largest eigenvalue), covariance PCA, 1-D |\n"
        f"| task | block per sample (task00..task05, pre_session, between_tasks, post_session_nap) |\n\n"
        f"`pc1` is sign-oriented to correlate positively with `sent_mean` "
        f"(PC1 explains {evr[0]*100:.1f}% of variance).\n\n"
        f"## sentiometer_markers.parquet  ({len(mlabels):,} rows)\n"
        f"`t_lsl`, `t_rel_s`, `label` (raw), `task` (prefix), `event` (suffix), `is_json`. "
        f"Same clock as the time-series - epoch with a subtraction / `searchsorted`.\n\n"
        f"## sentiometer_pca.json\nPC1 loadings + full eigen-spectrum + channel/sampling stats.\n\n"
        f"```python\nimport pandas as pd\n"
        f"ts = pd.read_parquet('sentiometer_timeseries.parquet')\n"
        f"mk = pd.read_parquet('sentiometer_markers.parquet')\n```\n"
    )
    (OUTDIR / "README.md").write_text(readme)

    # Tiny machine-readable verify sidecar (robust to console truncation).
    verify = {
        "ts_rows": int(pq.ParquetFile(ts_path).metadata.num_rows),
        "mk_rows": int(pq.ParquetFile(mk_path).metadata.num_rows),
        "ts_bytes": int(ts_path.stat().st_size),
        "mk_bytes": int(mk_path.stat().st_size),
        "fs_hz": round(fs, 4),
        "duration_hms": meta["duration_hms"],
        "pc1_evr": round(float(evr[0]), 4),
        "evr_full": [round(float(v), 4) for v in evr],
        "corr_mean_pc1": round(float(np.corrcoef(sent_mean, pc1)[0, 1]), 4),
        "nan_in_floats": int(cont_df.select_dtypes("number").isna().sum().sum()),
        "monotonic_t": bool(np.all(np.diff(ts) > 0)),
        "task_counts": {str(k): int(v) for k, v in cont_df["task"].value_counts().items()},
        "pc1_loadings": {k: round(float(v), 4) for k, v in zip(pd_labels, v1)},
    }
    (OUTDIR / "_verify.json").write_text(json.dumps(verify))
    print("OK ts_rows=%d mk_rows=%d" % (verify["ts_rows"], verify["mk_rows"]))


if __name__ == "__main__":
    main()
