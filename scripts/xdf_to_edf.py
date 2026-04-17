"""Convert a recorded XDF into an EDF+ file suitable for PSG analysis.

Target consumer: Dr. Ken Paller's sleep-scoring pipeline, which expects EDF
files. EDF+ supports one sample rate per channel, so if multiple streams
in the XDF have different rates they're all written at their native rate
(EDF+ handles this as "non-contiguous blocks"; any competent EDF viewer
such as EDFbrowser, Polyman, or MNE handles it).

By default we include the CGX AIM-2 physiological stream (EOG, chin EMG,
ECG, respiration, PPG/SpO₂, GSR, temperature) and any stream whose
LSL ``type`` is ``EEG`` (BrainVision, CGX, etc.). We skip impedance
streams, packet counters, trigger channels, and marker streams by
default. A mapping file (``--mapping``) overrides the defaults for
channel renaming (e.g. ``ExGa 1`` → ``EOG-L``) and inclusion filters.

Usage::

    uv run python scripts/xdf_to_edf.py                           # single .xdf in sampledata/ -> session.edf
    uv run python scripts/xdf_to_edf.py path/to/in.xdf out.edf
    uv run python scripts/xdf_to_edf.py in.xdf out.edf --mapping mapping.yaml
    uv run python scripts/xdf_to_edf.py in.xdf --list-channels   # dry-run, print channels

Example mapping file (``--mapping`` argument)::

    # Each top-level key is a stream name; each inner key is the original
    # channel label. `label` renames the channel in the EDF. `exclude`
    # drops the channel entirely. Omitted channels fall back to their
    # original label + the default inclusion policy.
    "CGX AIM Phys. Mon. AIM-0106":
      "ExGa 1": {label: "EOG-L"}
      "ExGa 2": {label: "EOG-R"}
      "ExGa 3": {label: "EMG-Chin1"}
      "ExGa 4": {label: "EMG-Chin2"}
      "Packet Counter": {exclude: true}
      "TRIGGER": {exclude: true}
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pyedflib
import pyxdf
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SAMPLE_DIR = REPO_ROOT / "sampledata"

# ----- default channel-inclusion policy -------------------------------------

# Drop any stream whose *type* matches one of these (case-insensitive).
EXCLUDED_STREAM_TYPES = {"markers", "impedance", "impeadance"}  # CGX typos "Impeadance"

# Drop any channel whose *type* matches one of these (case-insensitive).
EXCLUDED_CHANNEL_TYPES = {"packet", "trig", "impedance"}

# Drop any channel whose *label* matches one of these (case-insensitive,
# substring match so "Packet Counter" and "TRIGGER" both match).
EXCLUDED_CHANNEL_LABEL_FRAGMENTS = {"packet", "trigger"}


# ----- helpers --------------------------------------------------------------


def _get(stream: dict, *keys: str, default: Any = "") -> Any:
    cursor: Any = stream
    for k in keys:
        if isinstance(cursor, list) and cursor and isinstance(cursor[0], dict):
            cursor = cursor[0]
        if not isinstance(cursor, dict):
            return default
        cursor = cursor.get(k, default)
    if isinstance(cursor, list) and cursor and isinstance(cursor[0], str):
        return cursor[0]
    return cursor


def _stream_name(stream: dict) -> str:
    return str(_get(stream, "info", "name")) or "<unnamed>"


def _stream_type(stream: dict) -> str:
    return str(_get(stream, "info", "type")) or ""


def _channel_entries(stream: dict) -> list[dict]:
    """Pull the ``desc.channels.channel`` list out of a pyxdf stream dict."""
    info = stream.get("info") or {}
    desc_field = info.get("desc") or []
    if not desc_field:
        return []
    desc0 = desc_field[0] if isinstance(desc_field, list) else desc_field
    if not isinstance(desc0, dict):
        return []
    chs_field = desc0.get("channels") or []
    if not chs_field:
        return []
    chs0 = chs_field[0] if isinstance(chs_field, list) else chs_field
    if not isinstance(chs0, dict):
        return []
    ch_list = chs0.get("channel") or []
    if not isinstance(ch_list, list):
        ch_list = [ch_list]
    return [c for c in ch_list if isinstance(c, dict)]


def _channel_label(ch: dict) -> str:
    return (ch.get("label") or [""])[0]


def _channel_unit(ch: dict) -> str:
    return (ch.get("unit") or ["uV"])[0] if "unit" in ch else "uV"


def _channel_type(ch: dict) -> str:
    return (ch.get("type") or [""])[0] if "type" in ch else ""


def _find_xdf(path_arg: str | None) -> Path:
    if path_arg:
        p = Path(path_arg).expanduser()
        if not p.exists():
            raise SystemExit(f"XDF file not found: {p}")
        return p
    if not DEFAULT_SAMPLE_DIR.exists():
        raise SystemExit(f"No sampledata/ directory at {DEFAULT_SAMPLE_DIR}.")
    candidates = sorted(DEFAULT_SAMPLE_DIR.glob("*.xdf"))
    if not candidates:
        raise SystemExit(f"No .xdf in {DEFAULT_SAMPLE_DIR}.")
    if len(candidates) > 1:
        names = "\n  ".join(str(c.name) for c in candidates)
        raise SystemExit(f"Multiple .xdf in {DEFAULT_SAMPLE_DIR}:\n  {names}\nPass one on the CLI.")
    return candidates[0]


def _load_mapping(mapping_path: Path | None) -> dict[str, dict[str, dict]]:
    if mapping_path is None:
        return {}
    with open(mapping_path) as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise SystemExit(f"{mapping_path}: expected a top-level mapping (dict).")
    return data


def _apply_mapping(
    stream_name: str,
    ch_label: str,
    mapping: dict[str, dict[str, dict]],
) -> tuple[str, bool]:
    """Return (final_label, exclude)."""
    stream_map = mapping.get(stream_name, {})
    ch_map = stream_map.get(ch_label, {}) if isinstance(stream_map, dict) else {}
    if ch_map.get("exclude"):
        return ch_label, True
    return ch_map.get("label", ch_label), False


def _default_excluded(ch: dict) -> bool:
    lbl = _channel_label(ch).lower().strip()
    ctype = _channel_type(ch).lower().strip()
    if ctype in EXCLUDED_CHANNEL_TYPES:
        return True
    if any(frag in lbl for frag in EXCLUDED_CHANNEL_LABEL_FRAGMENTS):
        return True
    return False


# ----- main conversion ------------------------------------------------------


def convert(
    xdf_path: Path,
    edf_path: Path,
    mapping: dict | None = None,
    *,
    verbose: bool = True,
) -> None:
    mapping = mapping or {}

    streams, header = pyxdf.load_xdf(str(xdf_path))
    if verbose:
        print(f"Loaded {len(streams)} streams from {xdf_path}")

    # Pick streams we'll include.
    picked: list[dict] = []
    for s in streams:
        name = _stream_name(s)
        stype = _stream_type(s).lower()
        if stype in EXCLUDED_STREAM_TYPES:
            if verbose:
                print(f"  [skip stream] {name!r} (type={stype})")
            continue
        # Allow the mapping to force-skip the whole stream by setting
        # `_exclude_stream: true` under its key.
        stream_map = mapping.get(name, {})
        if isinstance(stream_map, dict) and stream_map.get("_exclude_stream"):
            if verbose:
                print(f"  [skip stream] {name!r} (mapping override)")
            continue
        picked.append(s)

    if not picked:
        raise SystemExit("No usable streams after filtering. Nothing to write.")

    # Wall-clock anchor: mtime of the XDF is ~the last sample's wall time.
    file_utc = datetime.fromtimestamp(xdf_path.stat().st_mtime, tz=timezone.utc)
    # Global LSL range across all picked streams.
    def _has_ts(s: dict) -> bool:
        ts = s.get("time_stamps")
        return ts is not None and len(ts) > 0

    min_lsl = min(float(np.min(s["time_stamps"])) for s in picked if _has_ts(s))
    max_lsl = max(float(np.max(s["time_stamps"])) for s in picked if _has_ts(s))
    session_start_utc = file_utc - timedelta(seconds=(max_lsl - min_lsl))

    # Build a flat list of (label, signal_array, fs, unit, source_stream).
    signals: list[dict] = []
    for s in picked:
        name = _stream_name(s)
        fs = float(_get(s, "info", "nominal_srate") or 0) or None
        ts_raw = s.get("time_stamps")
        ts = np.asarray(ts_raw if ts_raw is not None else [], dtype=float)
        data_raw = s.get("time_series")
        data = np.asarray(data_raw if data_raw is not None else [])
        if fs is None and len(ts) > 1:
            fs = 1.0 / float(np.median(np.diff(ts)))
        if fs is None or fs <= 0 or len(ts) < 2:
            if verbose:
                print(f"  [skip stream] {name!r}: no usable sample rate")
            continue

        ch_entries = _channel_entries(s)
        n_ch = int(_get(s, "info", "channel_count") or data.shape[1] if data.ndim == 2 else 1)

        for j in range(data.shape[1] if data.ndim == 2 else 1):
            if j < len(ch_entries):
                ch = ch_entries[j]
                orig_label = _channel_label(ch) or f"Ch{j+1}"
                unit = _channel_unit(ch) or "uV"
                ctype = _channel_type(ch)
            else:
                ch = {}
                orig_label = f"Ch{j+1}"
                unit = "uV"
                ctype = ""

            final_label, forced_exclude = _apply_mapping(name, orig_label, mapping)
            if forced_exclude:
                if verbose:
                    print(f"  [skip channel] {name}:{orig_label} (mapping exclude)")
                continue
            if final_label == orig_label and _default_excluded(ch):
                # Default-skip channels (packet counter, trigger, impedance)
                # unless the mapping explicitly relabels them.
                if verbose:
                    print(f"  [skip channel] {name}:{orig_label} (default exclusion)")
                continue

            signal = (data[:, j] if data.ndim == 2 else data).astype(np.float64, copy=False)
            # pyedflib requires physical_min/max on both sides. EDF+ only
            # allocates 8 chars for each — so max absolute magnitude ≈ 9999999
            # (7 digits, leading sign, no decimal point) for safety. CGX
            # sometimes ships aux channels (SpO2, HR, Resp) with internal
            # scaling far outside the nominal µV range; clamp them so the
            # EDF writer doesn't error. The resolution loss is acceptable
            # because sleep scorers look at AASM-relevant channels (EEG,
            # EOG, EMG) in µV and at those we're well within range.
            rng = np.nanmax(np.abs(signal)) if signal.size else 1.0
            if not np.isfinite(rng) or rng == 0:
                rng = 1.0
            pad = 1.1 * rng
            pad = min(pad, 9_999_999.0)
            pmin, pmax = -float(pad), float(pad)
            # Clip the signal to the same range so writeSamples doesn't
            # blow up on an out-of-range value (e.g. a single spike).
            if np.nanmax(np.abs(signal)) > pad:
                signal = np.clip(signal, -pad, pad)
            signals.append(
                {
                    "label": final_label[:16],  # EDF label is <= 16 chars
                    "orig_label": orig_label,
                    "source_stream": name,
                    "unit": unit[:8],
                    "ctype": ctype,
                    "fs": int(round(fs)),
                    "signal": signal,
                    "physical_min": pmin,
                    "physical_max": pmax,
                }
            )

    if not signals:
        raise SystemExit("No channels selected after filtering. Nothing to write.")

    if verbose:
        print(f"\nWriting EDF+ with {len(signals)} channels to {edf_path}")

    writer = pyedflib.EdfWriter(
        str(edf_path), len(signals), file_type=pyedflib.FILETYPE_EDFPLUS
    )
    try:
        writer.setHeader(
            {
                "technician": "iacs-sentiometer-study",
                "recording_additional": "Converted from XDF via xdf_to_edf.py",
                "patientname": xdf_path.stem,
                "patient_additional": "",
                "patientcode": "",
                "equipment": "CGX AIM-2 / BrainVision / LSL",
                "admincode": "",
                "sex": "",
                "birthdate": "",
                "startdate": session_start_utc.replace(tzinfo=None),
            }
        )
        for i, ch in enumerate(signals):
            writer.setSignalHeader(
                i,
                {
                    "label": ch["label"],
                    "dimension": ch["unit"],
                    "sample_frequency": ch["fs"],
                    "physical_min": ch["physical_min"],
                    "physical_max": ch["physical_max"],
                    "digital_min": -32768,
                    "digital_max": 32767,
                    "prefilter": "",
                    "transducer": ch["ctype"] or ch["source_stream"],
                },
            )
        # Each call to writeSamples takes a list of 1-D arrays, one per channel.
        writer.writeSamples([ch["signal"] for ch in signals])
    finally:
        writer.close()

    if verbose:
        print("\nDone. Channel summary:")
        print(f"  {'#':>2}  {'label':<16}  {'fs':>5}  {'samples':>9}  "
              f"{'unit':<8}  source")
        for i, ch in enumerate(signals):
            print(
                f"  {i:>2}  {ch['label']:<16}  {ch['fs']:>5}  "
                f"{len(ch['signal']):>9}  {ch['unit']:<8}  "
                f"{ch['source_stream']}:{ch['orig_label']}"
            )


def list_channels(xdf_path: Path) -> None:
    streams, _ = pyxdf.load_xdf(str(xdf_path))
    print(f"File: {xdf_path}\n{len(streams)} streams\n")
    for s in streams:
        name = _stream_name(s)
        stype = _stream_type(s)
        fs = _get(s, "info", "nominal_srate")
        n_ch = _get(s, "info", "channel_count")
        ts_raw = s.get("time_stamps")
        ts = ts_raw if ts_raw is not None else []
        print(f"Stream {name!r}  type={stype}  fs={fs}  channels={n_ch}  "
              f"samples={len(ts)}")
        for j, ch in enumerate(_channel_entries(s)):
            label = _channel_label(ch)
            unit = _channel_unit(ch)
            ctype = _channel_type(ch)
            print(f"   {j:2d}: {label!r:<24} type={ctype!r} unit={unit}")
        print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "xdf",
        nargs="?",
        help="Path to .xdf (defaults to the single .xdf in sampledata/)",
    )
    parser.add_argument(
        "edf",
        nargs="?",
        help="Output .edf path (defaults to <xdf>.edf alongside the input)",
    )
    parser.add_argument(
        "--mapping",
        type=Path,
        default=None,
        help="YAML channel-mapping override file",
    )
    parser.add_argument(
        "--list-channels",
        action="store_true",
        help="Dry run: print channel inventory and exit without writing EDF",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress the per-channel summary on stdout",
    )
    args = parser.parse_args(argv)

    try:
        xdf_path = _find_xdf(args.xdf)
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.list_channels:
        list_channels(xdf_path)
        return 0

    if args.edf:
        edf_path = Path(args.edf).expanduser()
    else:
        edf_path = xdf_path.with_suffix(".edf")

    mapping = _load_mapping(args.mapping)
    convert(xdf_path, edf_path, mapping=mapping, verbose=not args.quiet)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
