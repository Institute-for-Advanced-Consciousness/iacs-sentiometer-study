#!/usr/bin/env python
"""
Sentiometer Mac Visualizer
==========================
Plug-and-play GUI for inspecting a live Sentiometer signal on macOS.

Flow:
  1. Tkinter dialog: detect candidate USB-CDC ports (autoselects the
     STMicroelectronics BlackPill F401CC if present), let the user pick
     one and choose a recording duration.
  2. Open the serial port, send the start command, wait for the first
     valid sample, drain pre-roll bytes.
  3. Hand off to a matplotlib live-viz window: 5 stacked subplots
     (PD1..PD5), sliding window, sample-rate readout.

Run:
    uv run --extra viz python scripts/mac_visualizer.py
Or double-click `launch_visualizer.command` from Finder.

This script is a *visualizer only* — it does not create an LSL outlet
and does not save anything to disk. For LSL streaming, use:
    uv run sentiometer run
"""

from __future__ import annotations

import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

# Allow running as `python scripts/mac_visualizer.py` without install.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import tkinter as tk  # noqa: E402
from tkinter import messagebox, ttk  # noqa: E402

import numpy as np  # noqa: E402
import serial  # noqa: E402
import serial.tools.list_ports  # noqa: E402
import yaml  # noqa: E402

from sentiometer.stream import (  # noqa: E402
    SerialBuffer,
    open_serial,
    parse_line,
    send_command,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = REPO_ROOT / "config" / "sentiometer.yaml"
LOCAL_CONFIG = REPO_ROOT / "config" / "local.yaml"

# STMicroelectronics CDC ACM (BlackPill F401CC firmware)
STM_VID = 0x0483
STM_CDC_PID = 0x5740

# Description / manufacturer substrings that flag a likely Sentiometer
# even if the VID:PID changes after a firmware update. Lowercased before match.
DESCRIPTION_HINTS = (
    "blackpill",
    "stm32",
    "stmicroelectronics",
    "cdc in fs mode",
    "usbmodem",  # macOS device-path tell for any CDC ACM
)


# ---------------------------------------------------------------------------
# Port detection
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PortCandidate:
    device: str
    description: str
    is_likely_sentiometer: bool

    def label(self) -> str:
        marker = "  ★" if self.is_likely_sentiometer else ""
        return f"{self.device}  —  {self.description}{marker}"


def list_candidates() -> list[PortCandidate]:
    """Return all serial ports, with Sentiometer candidates flagged.

    A port is flagged as "likely Sentiometer" if either:
      * its VID:PID matches the STMicroelectronics CDC class, OR
      * any of DESCRIPTION_HINTS appears in the description, manufacturer,
        product, or device path.

    Bluetooth and macOS debug-console ports are filtered out — they show
    up on every Mac and only add noise.
    """
    out: list[PortCandidate] = []
    for p in sorted(serial.tools.list_ports.comports(), key=lambda x: x.device):
        # Hide things that are never the Sentiometer.
        dev_lower = (p.device or "").lower()
        if "bluetooth" in dev_lower or "debug-console" in dev_lower:
            continue

        haystack = " ".join(
            filter(None, [p.description, p.manufacturer, p.product, p.device])
        ).lower()
        vid_pid_match = (p.vid == STM_VID and p.pid == STM_CDC_PID)
        hint_match = any(h in haystack for h in DESCRIPTION_HINTS)

        out.append(
            PortCandidate(
                device=p.device,
                description=p.description or "(no description)",
                is_likely_sentiometer=vid_pid_match or hint_match,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def load_config() -> dict:
    """Load sentiometer.yaml (preferring local.yaml override). Errors out clearly
    if neither file exists.
    """
    path = LOCAL_CONFIG if LOCAL_CONFIG.exists() else DEFAULT_CONFIG
    if not path.exists():
        raise FileNotFoundError(
            f"Could not find {DEFAULT_CONFIG}. Run from the repo root."
        )
    with open(path) as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Connect dialog (Tkinter)
# ---------------------------------------------------------------------------

class ConnectDialog:
    """Modal dialog: pick a port and duration, click Connect.

    On success, sets `self.result` to a dict with `port` and `duration_min`.
    On cancel/close, leaves `self.result = None`.
    """

    def __init__(self, root: tk.Tk, default_duration_min: int = 30):
        self.root = root
        self.result: dict | None = None
        self._candidates: list[PortCandidate] = []

        root.title("Sentiometer Visualizer")
        root.geometry("560x320")
        root.resizable(False, False)

        # Header
        header = tk.Frame(root, padx=20, pady=16)
        header.pack(fill="x")
        tk.Label(
            header,
            text="Sentiometer — Mac Visualizer",
            font=("Helvetica", 16, "bold"),
        ).pack(anchor="w")
        tk.Label(
            header,
            text="Pick the device port and click Connect. ★ = auto-detected Sentiometer.",
            fg="#555",
        ).pack(anchor="w", pady=(2, 0))

        # Port row
        port_row = tk.Frame(root, padx=20, pady=4)
        port_row.pack(fill="x")
        tk.Label(port_row, text="Serial port:").pack(side="left")
        self.port_var = tk.StringVar()
        self.port_combo = ttk.Combobox(
            port_row, textvariable=self.port_var, width=55, state="readonly"
        )
        self.port_combo.pack(side="left", padx=(8, 8), fill="x", expand=True)
        tk.Button(port_row, text="Refresh", command=self.refresh_ports).pack(side="left")

        # Duration row
        dur_row = tk.Frame(root, padx=20, pady=12)
        dur_row.pack(fill="x")
        tk.Label(dur_row, text="Recording duration (minutes):").pack(side="left")
        self.duration_var = tk.IntVar(value=default_duration_min)
        ttk.Spinbox(
            dur_row, from_=1, to=99999, textvariable=self.duration_var, width=8
        ).pack(side="left", padx=(8, 0))
        tk.Label(
            dur_row,
            text="(device firmware caps at 5 digits; close the window to stop early)",
            fg="#888",
        ).pack(side="left", padx=(8, 0))

        # Status / log area
        self.status_var = tk.StringVar(value="")
        tk.Label(
            root, textvariable=self.status_var, fg="#333", padx=20, justify="left",
            wraplength=520, anchor="w",
        ).pack(fill="x", pady=(8, 0))

        # Button row. ttk buttons render with the native Aqua theme so the
        # text is always readable; tk.Button ignores fg/bg on macOS and was
        # producing low-contrast text on the previous accent fill.
        btn_row = tk.Frame(root, padx=20, pady=16)
        btn_row.pack(fill="x", side="bottom")
        style = ttk.Style()
        style.configure("Accent.TButton", font=("Helvetica", 13, "bold"))
        ttk.Button(btn_row, text="Quit", command=self._on_quit).pack(side="right")
        self.connect_btn = ttk.Button(
            btn_row, text="Connect", command=self._on_connect,
            style="Accent.TButton",
        )
        self.connect_btn.pack(side="right", padx=(0, 8), ipadx=12)

        root.protocol("WM_DELETE_WINDOW", self._on_quit)
        root.bind("<Return>", lambda _e: self._on_connect())
        root.bind("<Escape>", lambda _e: self._on_quit())

        self.refresh_ports()

    def refresh_ports(self) -> None:
        self._candidates = list_candidates()
        if not self._candidates:
            self.port_combo["values"] = []
            self.port_var.set("")
            self.status_var.set(
                "No USB serial devices found. Plug in the Sentiometer "
                "(macOS will ask to allow the BLACKPILL_F401CC) and click Refresh."
            )
            self.connect_btn.config(state="disabled")
            return

        labels = [c.label() for c in self._candidates]
        self.port_combo["values"] = labels

        # Default to the first auto-detected Sentiometer; otherwise first port.
        auto = next((c for c in self._candidates if c.is_likely_sentiometer), None)
        chosen = auto or self._candidates[0]
        self.port_var.set(chosen.label())

        if auto is not None:
            self.status_var.set(f"Auto-detected Sentiometer at {auto.device}.")
        else:
            self.status_var.set(
                "No port matched the Sentiometer signature. "
                "Pick the right one manually if you know it."
            )
        self.connect_btn.config(state="normal")

    def _selected_device(self) -> str | None:
        label = self.port_var.get()
        for c in self._candidates:
            if c.label() == label:
                return c.device
        return None

    def _on_connect(self) -> None:
        device = self._selected_device()
        if not device:
            messagebox.showerror("No port selected", "Pick a serial port first.")
            return
        try:
            duration = int(self.duration_var.get())
            if duration < 1:
                raise ValueError
        except (ValueError, tk.TclError):
            messagebox.showerror("Invalid duration", "Duration must be a positive integer.")
            return
        self.result = {"port": device, "duration_min": duration}
        self.root.destroy()

    def _on_quit(self) -> None:
        self.result = None
        self.root.destroy()


def run_connect_dialog() -> dict | None:
    root = tk.Tk()
    dialog = ConnectDialog(root)
    root.mainloop()
    return dialog.result


# ---------------------------------------------------------------------------
# Background reader thread
# ---------------------------------------------------------------------------

class StreamReader(threading.Thread):
    """Background thread: reads parsed samples from the serial port and
    appends them to a thread-safe deque per channel.

    Channels stored: PD1..PD5 (the 5 photodiodes). The device timestamp
    column is parsed but not plotted — gap detection uses it to update
    the dropped-sample counter.
    """

    def __init__(
        self,
        conn: serial.Serial,
        channel_buffers: list[deque],
        time_buffer: deque,
        expected_n: int,
        sample_interval_ms: int,
    ):
        super().__init__(daemon=True)
        self.conn = conn
        self.channel_buffers = channel_buffers  # list of 5 deques (PD1..PD5)
        self.time_buffer = time_buffer  # deque of host-time floats (seconds)
        self.expected_n = expected_n
        self.sample_interval_ms = sample_interval_ms

        self.samples = 0
        self.dropped = 0
        self.parse_errors = 0
        self.start_time = time.monotonic()
        self._last_device_ts: float | None = None
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        buf = SerialBuffer(self.conn)
        first = True
        while not self._stop_event.is_set():
            try:
                lines = buf.read_lines()
            except (serial.SerialException, OSError):
                break
            for raw in lines:
                values = parse_line(raw, self.expected_n)
                if values is None:
                    self.parse_errors += 1
                    continue
                if first:
                    first = False
                    continue

                device_ts = values[0]
                if self._last_device_ts is not None:
                    gap = device_ts - self._last_device_ts
                    if gap > self.sample_interval_ms * 1.5:
                        self.dropped += int(gap / self.sample_interval_ms) - 1
                self._last_device_ts = device_ts

                self.time_buffer.append(time.monotonic() - self.start_time)
                # values = [device_ts, PD1, PD2, PD3, PD4, PD5]
                for ch_idx in range(5):
                    self.channel_buffers[ch_idx].append(values[1 + ch_idx])
                self.samples += 1

    @property
    def rate_hz(self) -> float:
        elapsed = time.monotonic() - self.start_time
        return self.samples / elapsed if elapsed > 0 else 0.0


# ---------------------------------------------------------------------------
# Live visualization
# ---------------------------------------------------------------------------

FREQUENCY_BANDS: tuple[tuple[str, float, float, str], ...] = (
    ("Delta",  1.0,   4.0,  "#4363d8"),
    ("Theta",  4.0,   8.0,  "#3cb44b"),
    ("Alpha",  8.0,  13.0,  "#f58231"),
    ("Beta",  13.0,  30.0,  "#911eb4"),
    ("Gamma", 30.0,  50.0,  "#e6194B"),
)

# 20 contiguous 10-Hz-wide bins covering 0-200 Hz for the "Band Power (All)"
# view. Each bin is labelled by its upper edge ("10", "20", ..., "200").
BAND_ALL_EDGES = np.arange(0, 210, 10, dtype=float)  # 0, 10, 20, ..., 200
BAND_ALL_LABELS = [f"{int(BAND_ALL_EDGES[i + 1])}" for i in range(len(BAND_ALL_EDGES) - 1)]


def run_visualizer(
    conn: serial.Serial,
    cfg: dict,
    window_seconds: float = 5.0,
    psd_window_seconds: float = 10.0,
    sma_short_seconds: float = 10.0,
    sma_long_seconds: float = 30.0,
) -> None:
    """Open a matplotlib window with three panels:

    - Left: stacked traces (Mean on top in black with two SMA overlays —
      `sma_short_seconds` in red, `sma_long_seconds` in blue — followed
      by PD1..PD5), each showing the last `window_seconds`.
    - Middle: power spectrum of the last `psd_window_seconds` of the mean.
    - Right: bar chart of band power (delta..gamma) integrated over the
      same `psd_window_seconds` window of the mean.

    Returns when the user closes the window.
    """
    # matplotlib is pre-imported by main() before the connect dialog so the
    # post-Connect transition is fast; this fallback path covers the rare
    # case where someone imports run_visualizer() directly.
    try:
        import matplotlib
        if matplotlib.get_backend().lower() != "tkagg":
            matplotlib.use("TkAgg")
        import matplotlib.pyplot as plt
        from matplotlib.animation import FuncAnimation
        from matplotlib.widgets import Button, TextBox
    except ImportError as e:
        raise RuntimeError(
            "matplotlib is required for the live visualizer. Install with:\n"
            "    uv sync --extra viz\n"
            f"(import error: {e})"
        ) from e

    nominal_rate = cfg["lsl"]["nominal_srate"]
    channel_labels = ["PD1", "PD2", "PD3", "PD4", "PD5"]
    # The PSD/band-power window is now user-adjustable at runtime; size the
    # buffer to support the maximum the textbox allows, not just the
    # initial value, so changing it never starves the FFT.
    max_psd_window_seconds = 60.0
    buffer_seconds = (
        max(window_seconds + sma_long_seconds, max_psd_window_seconds) * 1.2
    )
    maxlen = int(buffer_seconds * nominal_rate)

    # Per-channel deque + a shared time deque (all aligned by index).
    channel_buffers = [deque(maxlen=maxlen) for _ in range(5)]
    time_buffer = deque(maxlen=maxlen)

    reader = StreamReader(
        conn=conn,
        channel_buffers=channel_buffers,
        time_buffer=time_buffer,
        expected_n=cfg["device"]["values_per_line"],
        sample_interval_ms=cfg["device"]["sample_interval_ms"],
    )
    reader.start()

    # ----- Figure layout ---------------------------------------------------
    # Two columns: stacked traces (left) + a single right panel that holds
    # *both* the band-power bar chart (default) and the PSD plot in the
    # same gridspec slot — only one is visible at a time, swapped via the
    # toggle button in the bottom controls strip.
    fig = plt.figure(figsize=(15, 8), constrained_layout=True)
    fig.canvas.manager.set_window_title("Sentiometer Visualizer")
    outer = fig.add_gridspec(
        nrows=2, ncols=1, height_ratios=[18, 1], hspace=0.04,
    )
    data_gs = outer[0].subgridspec(
        nrows=6, ncols=2, width_ratios=[3.0, 1.5],
        hspace=0.18, wspace=0.05,
    )
    ctrl_gs = outer[1].subgridspec(
        nrows=1, ncols=4, width_ratios=[1.4, 0.4, 1.6, 6.0], wspace=0.4,
    )

    trace_specs = [
        ("Mean", "#000000"),
        ("PD1", "#e6194B"),
        ("PD2", "#3cb44b"),
        ("PD3", "#4363d8"),
        ("PD4", "#f58231"),
        ("PD5", "#911eb4"),
    ]
    trace_axes = []
    trace_lines = []
    for row, (label, color) in enumerate(trace_specs):
        ax = fig.add_subplot(data_gs[row, 0], sharex=trace_axes[0] if trace_axes else None)
        ax.set_ylabel(
            label, rotation=0, ha="right", va="center", fontweight="bold",
            fontsize=13, labelpad=10,
            color=color if label != "Mean" else "black",
        )
        ax.grid(True, alpha=0.25)
        line, = ax.plot([], [], lw=1.2 if label == "Mean" else 1.0, color=color)
        trace_lines.append(line)
        trace_axes.append(ax)
        if row < len(trace_specs) - 1:
            ax.tick_params(labelbottom=False)
    trace_axes[-1].set_xlabel(f"Time (s, last {window_seconds:.0f} s window)")

    # Two SMA overlays on the Mean (row 0) trace.
    mean_ax = trace_axes[0]
    sma_short_line, = mean_ax.plot(
        [], [], color="#e6194B", lw=1.4, alpha=0.9,
        label=f"{sma_short_seconds:.0f}s SMA",
    )
    sma_long_line, = mean_ax.plot(
        [], [], color="#1f77ff", lw=1.4, alpha=0.9,
        label=f"{sma_long_seconds:.0f}s SMA",
    )
    mean_ax.legend(loc="upper right", fontsize=8, framealpha=0.85)

    # Single right-panel axes; we clear-and-rebuild its contents when the
    # toggle button flips between band-power bars and the PSD line. This
    # avoids stacking two axes in the same gridspec slot (which collapses
    # constrained_layout and hangs TkAgg's interactive event loop).
    panel_ax = fig.add_subplot(data_gs[:, 1])
    nyquist = nominal_rate / 2.0
    band_names = [b[0] for b in FREQUENCY_BANDS]
    band_colors = [b[3] for b in FREQUENCY_BANDS]
    band_x = np.arange(len(FREQUENCY_BANDS))

    # Mutable artist holder — populated by _setup_*_view. Only one of
    # {bars_eeg, bars_all, psd_line} is non-None at a time, matching the
    # current view_state["mode"].
    panel_artists: dict = {"bars_eeg": None, "bars_all": None, "psd_line": None}

    def _clear_artists() -> None:
        panel_artists["bars_eeg"] = None
        panel_artists["bars_all"] = None
        panel_artists["psd_line"] = None

    def _setup_band_eeg_view() -> None:
        panel_ax.clear()
        panel_ax.set_ylabel("Power (dB, arb.)")
        panel_ax.set_xlabel("")
        bars = panel_ax.bar(
            band_x, np.zeros(len(FREQUENCY_BANDS)),
            color=band_colors, edgecolor="black", linewidth=0.5,
        )
        panel_ax.set_xticks(band_x)
        panel_ax.set_xticklabels(band_names, rotation=0)
        panel_ax.grid(True, alpha=0.25, axis="y")
        _clear_artists()
        panel_artists["bars_eeg"] = bars

    def _setup_band_all_view() -> None:
        panel_ax.clear()
        panel_ax.set_ylabel("Power (dB, arb.)")
        panel_ax.set_xlabel("Frequency upper edge (Hz)")
        n_bins = len(BAND_ALL_LABELS)
        x = np.arange(n_bins)
        # Smooth viridis-style ramp from blue (low Hz) to red (high Hz) so
        # the spectrum is readable at a glance even with 20 narrow bars.
        cmap = plt.get_cmap("viridis")
        colors = [cmap(i / max(1, n_bins - 1)) for i in range(n_bins)]
        bars = panel_ax.bar(
            x, np.zeros(n_bins),
            color=colors, edgecolor="black", linewidth=0.4,
        )
        panel_ax.set_xticks(x)
        # Label every other bar to keep ticks legible at this density.
        labels = [lbl if i % 2 == 1 else "" for i, lbl in enumerate(BAND_ALL_LABELS)]
        panel_ax.set_xticklabels(labels, rotation=0)
        panel_ax.grid(True, alpha=0.25, axis="y")
        _clear_artists()
        panel_artists["bars_all"] = bars

    def _setup_psd_view() -> None:
        panel_ax.clear()
        panel_ax.set_xlabel("Frequency (Hz)")
        panel_ax.set_ylabel("Power (dB, arb.)")
        panel_ax.grid(True, alpha=0.3, which="both")
        line, = panel_ax.plot([], [], color="#000000", lw=1.0)
        panel_ax.set_xlim(0, nyquist)
        _clear_artists()
        panel_artists["psd_line"] = line

    # Mutable view state. The animation reads `view_state["seconds"]`
    # every frame; the textbox callback updates it. The toggle button
    # cycles `mode` through three views in this order:
    #   band_eeg  →  psd  →  band_all  →  band_eeg ...
    MODE_ORDER = ("band_eeg", "psd", "band_all")
    MODE_LABELS = {
        "band_eeg": ("Band power (EEG)", "Show power spectrum"),
        "psd":      ("Power spectrum",   "Show all-band power"),
        "band_all": ("Band power (All)", "Show EEG bands"),
    }
    view_state = {"seconds": float(psd_window_seconds), "mode": "band_eeg"}

    def _apply_title() -> None:
        s = view_state["seconds"]
        title, _ = MODE_LABELS[view_state["mode"]]
        panel_ax.set_title(f"{title} — last {s:.0f} s")

    def _build_view() -> None:
        mode = view_state["mode"]
        if mode == "band_eeg":
            _setup_band_eeg_view()
        elif mode == "psd":
            _setup_psd_view()
        elif mode == "band_all":
            _setup_band_all_view()

    # Build the default (EEG-band) view.
    _build_view()
    _apply_title()

    # ----- Controls strip --------------------------------------------------
    toggle_ax = fig.add_subplot(ctrl_gs[0, 0])
    toggle_btn = Button(
        toggle_ax, "Show power spectrum", color="#e8e8e8", hovercolor="#cfd8ff",
    )
    toggle_btn.label.set_fontsize(10)

    textbox_ax = fig.add_subplot(ctrl_gs[0, 2])
    window_textbox = TextBox(
        textbox_ax, "Window (s):  ", initial=f"{psd_window_seconds:.0f}",
        textalignment="center",
    )
    window_textbox.label.set_fontsize(10)

    def _toggle(_event):
        # Advance to the next mode in MODE_ORDER (cycles back to start).
        idx = MODE_ORDER.index(view_state["mode"])
        view_state["mode"] = MODE_ORDER[(idx + 1) % len(MODE_ORDER)]
        _build_view()
        # MODE_LABELS[mode][1] is the button text shown *while in* that mode
        # — describing what the next click will do.
        toggle_btn.label.set_text(MODE_LABELS[view_state["mode"]][1])
        _apply_title()
        fig.canvas.draw_idle()

    def _on_window_submit(text: str) -> None:
        try:
            v = float(text)
        except ValueError:
            window_textbox.set_val(f"{view_state['seconds']:.0f}")
            return
        # Clamp to [1, max_psd_window_seconds]; FFT needs at least ~64
        # samples (~0.13 s at 500 Hz) but anything under 1 s is unhelpful
        # for spectral estimation.
        v = max(1.0, min(max_psd_window_seconds, v))
        view_state["seconds"] = v
        window_textbox.set_val(f"{v:.0f}")
        _apply_title()
        fig.canvas.draw_idle()

    toggle_btn.on_clicked(_toggle)
    window_textbox.on_submit(_on_window_submit)

    fig.suptitle("Sentiometer — live signal", fontsize=13, fontweight="bold", y=0.97)

    # SMA kernel sizes are fixed at startup; the spectral window length
    # comes from view_state["seconds"] and is recomputed each frame.
    sma_short_samples = max(2, int(sma_short_seconds * nominal_rate))
    sma_long_samples = max(2, int(sma_long_seconds * nominal_rate))

    def _rolling_mean(x: np.ndarray, k: int) -> np.ndarray:
        """Causal rolling mean of `x` with kernel size `k`. The first
        min(k-1, len(x)) values are computed against a shorter prefix so
        the line is defined from the very first sample (no NaN edge).
        """
        if x.size == 0:
            return x
        cs = np.concatenate(([0.0], np.cumsum(x)))
        idx = np.arange(1, x.size + 1)
        starts = np.maximum(idx - k, 0)
        return (cs[idx] - cs[starts]) / (idx - starts)

    band_edges = np.array([(b[1], b[2]) for b in FREQUENCY_BANDS])

    def _update(_frame):
        # Animation returns the trace+SMA artists; the right-panel artists
        # are looked up live from panel_artists because _setup_*_view
        # destroys and re-creates them on toggle.
        base_artists = trace_lines + [sma_short_line, sma_long_line]
        if not time_buffer:
            return base_artists

        ts = np.fromiter(time_buffer, dtype=np.float64)
        t_now = float(ts[-1])
        t_min = max(0.0, t_now - window_seconds)

        per_channel = [np.fromiter(buf, dtype=np.float64) for buf in channel_buffers]
        n = min(len(ts), *(len(c) for c in per_channel))
        if n == 0:
            return base_artists
        ts = ts[-n:]
        per_channel = [c[-n:] for c in per_channel]
        stack = np.vstack(per_channel)
        mean_signal = stack.mean(axis=0)

        # ----- Left column: scrolling traces -------------------------------
        mask = ts >= t_min
        all_traces = [mean_signal] + per_channel  # order matches trace_specs
        for line, ax, data in zip(trace_lines, trace_axes, all_traces):
            line.set_data(ts, data)
            ax.set_xlim(t_min, max(t_min + window_seconds, t_now))
            if mask.any():
                visible = data[mask]
                lo, hi = float(visible.min()), float(visible.max())
                if lo == hi:
                    lo, hi = lo - 1, hi + 1
                pad = (hi - lo) * 0.08
                ax.set_ylim(lo - pad, hi + pad)

        # SMA overlays on the Mean trace. Compute the rolling means across
        # the full buffered tail, then plot only the visible window slice
        # so the per-axis autoscale above already sees them via mask logic
        # below.
        sma_short_full = _rolling_mean(mean_signal, sma_short_samples)
        sma_long_full = _rolling_mean(mean_signal, sma_long_samples)
        sma_short_line.set_data(ts, sma_short_full)
        sma_long_line.set_data(ts, sma_long_full)
        # Re-fit the Mean axis y-limits so the SMAs (which span a wider
        # range than the raw signal in noisy stretches) are also in view.
        if mask.any():
            visible_stack = np.concatenate([
                mean_signal[mask], sma_short_full[mask], sma_long_full[mask],
            ])
            lo, hi = float(visible_stack.min()), float(visible_stack.max())
            if lo == hi:
                lo, hi = lo - 1, hi + 1
            pad = (hi - lo) * 0.08
            mean_ax.set_ylim(lo - pad, hi + pad)

        # ----- PSD + band power over the user-selected window ------------
        psd_window_samples = max(64, int(view_state["seconds"] * nominal_rate))
        if mean_signal.size >= 64:
            seg = mean_signal[-psd_window_samples:]
            seg = seg - seg.mean()
            window = np.hanning(seg.size)
            windowed = seg * window
            spec = np.fft.rfft(windowed)
            power = (np.abs(spec) ** 2) / (np.sum(window ** 2) + 1e-12)
            freqs = np.fft.rfftfreq(seg.size, d=1.0 / nominal_rate)

            mode = view_state["mode"]
            if mode == "psd" and panel_artists["psd_line"] is not None:
                power_db = 10.0 * np.log10(power + 1e-12)
                panel_artists["psd_line"].set_data(freqs, power_db)
                if power_db.size > 1:
                    lo = float(np.percentile(power_db, 5))
                    hi = float(power_db.max())
                    if hi - lo < 5:
                        hi = lo + 5
                    panel_ax.set_ylim(lo - 2, hi + 4)
            elif mode == "band_eeg" and panel_artists["bars_eeg"] is not None:
                band_db = np.zeros(len(FREQUENCY_BANDS))
                for i, (lo_hz, hi_hz) in enumerate(band_edges):
                    in_band = (freqs >= lo_hz) & (freqs < hi_hz)
                    bp = power[in_band].sum() if in_band.any() else 0.0
                    band_db[i] = 10.0 * np.log10(bp + 1e-12)
                for bar, h in zip(panel_artists["bars_eeg"], band_db):
                    bar.set_height(h)
                if np.isfinite(band_db).any():
                    lo, hi = float(band_db.min()), float(band_db.max())
                    if hi - lo < 5:
                        hi = lo + 5
                    panel_ax.set_ylim(lo - 4, hi + 4)
            elif mode == "band_all" and panel_artists["bars_all"] is not None:
                # 20 contiguous 10-Hz bins from 0-200 Hz. Bins above
                # Nyquist contribute 0 (and read as the dB floor).
                n_bins = len(BAND_ALL_LABELS)
                bin_db = np.zeros(n_bins)
                for i in range(n_bins):
                    lo_hz, hi_hz = BAND_ALL_EDGES[i], BAND_ALL_EDGES[i + 1]
                    in_band = (freqs >= lo_hz) & (freqs < hi_hz)
                    bp = power[in_band].sum() if in_band.any() else 0.0
                    bin_db[i] = 10.0 * np.log10(bp + 1e-12)
                for bar, h in zip(panel_artists["bars_all"], bin_db):
                    bar.set_height(h)
                if np.isfinite(bin_db).any():
                    lo, hi = float(bin_db.min()), float(bin_db.max())
                    if hi - lo < 5:
                        hi = lo + 5
                    panel_ax.set_ylim(lo - 4, hi + 4)

        title = (
            f"Sentiometer — live signal     "
            f"samples: {reader.samples:,}     "
            f"rate: {reader.rate_hz:.1f} Hz "
            f"(target {nominal_rate})     "
            f"dropped: {reader.dropped}     "
            f"parse errs: {reader.parse_errors}"
        )
        fig.suptitle(title, fontsize=11)
        return base_artists

    # Refresh ~30 fps. blit=False because the suptitle and per-axis y-limits
    # change every frame (blitting would skip those redraws).
    _anim = FuncAnimation(fig, _update, interval=33, blit=False, cache_frame_data=False)

    # Stash widget references on the figure so the GC doesn't collect them
    # mid-session (matplotlib widgets need a live Python reference for
    # callbacks to keep firing).
    fig._sentiometer_widgets = (toggle_btn, window_textbox, _anim)

    try:
        plt.show()
    finally:
        reader.stop()
        reader.join(timeout=2.0)


# ---------------------------------------------------------------------------
# Connection (post-dialog, pre-viz)
# ---------------------------------------------------------------------------

def open_and_prime(cfg: dict, port: str, duration_min: int) -> serial.Serial:
    """Open the serial port, send the start command, wait for the first valid
    sample. Raises RuntimeError on failure (caller is expected to surface
    this in a dialog).
    """
    cfg["serial"]["port"] = port
    sample_ms = cfg["device"]["sample_interval_ms"]
    cfg["device"]["start_command"] = f"{duration_min:05d} {sample_ms}"

    conn = open_serial(cfg)
    conn.reset_input_buffer()
    line_ending = cfg["serial"].get("line_ending", "\r\n")
    send_command(conn, cfg["device"]["start_command"], line_ending)

    # Wait up to 60 s for the first parsable line. The device sometimes
    # emits a "Delayed for NNNNN msecs" status string while a previous
    # session winds down — those don't parse and are ignored.
    expected_n = cfg["device"]["values_per_line"]
    buf = SerialBuffer(conn)
    deadline = time.monotonic() + 60.0
    while time.monotonic() < deadline:
        for raw in buf.read_lines():
            if parse_line(raw, expected_n) is not None:
                return conn
    conn.close()
    raise RuntimeError(
        "No valid samples received within 60 seconds. Try unplugging the "
        "Sentiometer USB cable and plugging it back in."
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    cfg = load_config()

    # Pre-import matplotlib BEFORE the connect dialog. The first import +
    # font cache build can take 1-3 s on macOS; doing it here means the
    # post-Connect transition feels instant instead of "stuck".
    import matplotlib
    matplotlib.use("TkAgg")
    import matplotlib.pyplot  # noqa: F401  — warms the import cache

    selection = run_connect_dialog()
    if selection is None:
        return  # user quit before connecting

    # Brief progress UI while the device starts up. Tk's update_idletasks
    # keeps it responsive while we block on serial. The 2 s pre-send sleep
    # in send_command() is required for the BlackPill bootloader to
    # stabilise — that's the bulk of the wait.
    progress_root = tk.Tk()
    progress_root.title("Sentiometer Visualizer")
    progress_root.geometry("440x130")
    progress_root.resizable(False, False)
    msg_var = tk.StringVar(value=f"Opening {selection['port']}…")
    tk.Label(
        progress_root, textvariable=msg_var, padx=20, pady=20, justify="left",
        wraplength=400,
    ).pack(fill="both", expand=True)
    progress_root.update()

    try:
        msg_var.set(
            f"Sending start command to {selection['port']}.\n"
            "Waiting ~2 s for the device to initialize…"
        )
        progress_root.update()
        conn = open_and_prime(cfg, selection["port"], selection["duration_min"])
        msg_var.set("Connected. Opening live visualization…")
        progress_root.update_idletasks()
    except Exception as e:  # noqa: BLE001 — surface anything that goes wrong
        progress_root.destroy()
        messagebox.showerror("Connection failed", str(e))
        return
    progress_root.destroy()

    try:
        run_visualizer(conn, cfg)
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    main()
