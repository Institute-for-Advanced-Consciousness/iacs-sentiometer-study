#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11,<3.13"
# dependencies = [
#   "pyserial>=3.5",
#   "pyyaml>=6.0",
#   "numpy>=1.26",
#   "matplotlib>=3.8",
# ]
# ///
"""
Sentiometer Mac Visualizer (standalone build)
=============================================
Single-file plug-and-play visualizer for the IACS Sentiometer on macOS.
Double-click `Sentiometer Visualizer.command` to launch.

The PEP 723 metadata block above tells `uv` exactly what Python and
libraries to install in an isolated environment — nothing leaks into
the system Python.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import tkinter as tk
from tkinter import messagebox, ttk

import numpy as np
import serial
import serial.tools.list_ports
import yaml

HERE = Path(__file__).resolve().parent
DEFAULT_CONFIG = HERE / "sentiometer.yaml"

# STMicroelectronics CDC ACM (BlackPill F401CC firmware shipped with the device).
STM_VID = 0x0483
STM_CDC_PID = 0x5740

# Description / manufacturer substrings that flag a likely Sentiometer even
# if a firmware update changes the VID:PID. Lowercased before match.
DESCRIPTION_HINTS = (
    "blackpill",
    "stm32",
    "stmicroelectronics",
    "cdc in fs mode",
    "usbmodem",  # macOS device-path tell for any CDC ACM
)


# ---------------------------------------------------------------------------
# Serial helpers (slimmed copy of src/sentiometer/stream.py — no pylsl)
# ---------------------------------------------------------------------------

PARITY_MAP = {
    "none": serial.PARITY_NONE,
    "even": serial.PARITY_EVEN,
    "odd": serial.PARITY_ODD,
}


def open_serial(cfg: dict) -> serial.Serial:
    s = cfg["serial"]
    conn = serial.Serial(
        port=s["port"],
        baudrate=s["baudrate"],
        bytesize=s["bytesize"],
        parity=PARITY_MAP.get(s["parity"], serial.PARITY_NONE),
        stopbits=s["stopbits"],
        timeout=s["timeout_sec"],
    )
    # The BlackPill firmware needs DTR + RTS asserted before it accepts commands.
    conn.dtr = True
    conn.rts = True
    return conn


def send_command(conn: serial.Serial, command: str, line_ending: str = "\r\n") -> None:
    # 2 s pre-send: the BlackPill bootloader takes that long to be ready
    # after the port opens. 0.5 s post-send: gives the firmware time to
    # parse and ack before we start reading.
    time.sleep(2.0)
    conn.write((command + line_ending).encode("ascii"))
    time.sleep(0.5)


class SerialBuffer:
    """Read raw bytes from a serial port and split on \\r\\n.

    Works around pyserial's readline() interacting badly with timeouts and
    DTR timing — reading raw bytes and splitting ourselves is deterministic.
    """

    def __init__(self, conn: serial.Serial) -> None:
        self.conn = conn
        self._buf = b""

    def read_lines(self) -> list[bytes]:
        chunk = self.conn.read(self.conn.in_waiting or 1)
        if not chunk:
            return []
        self._buf += chunk
        parts = self._buf.split(b"\r\n")
        self._buf = parts[-1]  # keep incomplete fragment
        return [p for p in parts[:-1] if p]


def parse_line(raw: bytes, expected_n: int = 6) -> list[float] | None:
    try:
        text = raw.decode("ascii", errors="replace").strip()
        if not text:
            return None
        parts = text.split(",")
        if len(parts) != expected_n:
            return None
        return [float(p) for p in parts]
    except (ValueError, UnicodeDecodeError):
        return None


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
    """All serial ports with Sentiometer candidates flagged."""
    out: list[PortCandidate] = []
    for p in sorted(serial.tools.list_ports.comports(), key=lambda x: x.device):
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
# Config
# ---------------------------------------------------------------------------

def load_config() -> dict:
    if not DEFAULT_CONFIG.exists():
        raise FileNotFoundError(
            f"Could not find {DEFAULT_CONFIG.name}. It should sit next to this script."
        )
    with open(DEFAULT_CONFIG) as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Connect dialog
# ---------------------------------------------------------------------------

class ConnectDialog:
    def __init__(self, root: tk.Tk, default_duration_min: int = 30):
        self.root = root
        self.result: dict | None = None
        self._candidates: list[PortCandidate] = []

        root.title("Sentiometer Visualizer")
        root.geometry("560x320")
        root.resizable(False, False)

        header = tk.Frame(root, padx=20, pady=16)
        header.pack(fill="x")
        tk.Label(header, text="Sentiometer — Mac Visualizer",
                 font=("Helvetica", 16, "bold")).pack(anchor="w")
        tk.Label(header,
                 text="Pick the device port and click Connect. ★ = auto-detected Sentiometer.",
                 fg="#555").pack(anchor="w", pady=(2, 0))

        port_row = tk.Frame(root, padx=20, pady=4)
        port_row.pack(fill="x")
        tk.Label(port_row, text="Serial port:").pack(side="left")
        self.port_var = tk.StringVar()
        self.port_combo = ttk.Combobox(
            port_row, textvariable=self.port_var, width=55, state="readonly"
        )
        self.port_combo.pack(side="left", padx=(8, 8), fill="x", expand=True)
        tk.Button(port_row, text="Refresh", command=self.refresh_ports).pack(side="left")

        dur_row = tk.Frame(root, padx=20, pady=12)
        dur_row.pack(fill="x")
        tk.Label(dur_row, text="Recording duration (minutes):").pack(side="left")
        self.duration_var = tk.IntVar(value=default_duration_min)
        ttk.Spinbox(dur_row, from_=1, to=99999, textvariable=self.duration_var,
                    width=8).pack(side="left", padx=(8, 0))
        tk.Label(dur_row,
                 text="(close the window to stop early)",
                 fg="#888").pack(side="left", padx=(8, 0))

        self.status_var = tk.StringVar(value="")
        tk.Label(root, textvariable=self.status_var, fg="#333", padx=20,
                 justify="left", wraplength=520, anchor="w").pack(fill="x", pady=(8, 0))

        btn_row = tk.Frame(root, padx=20, pady=16)
        btn_row.pack(fill="x", side="bottom")
        ttk.Style().configure("Accent.TButton", font=("Helvetica", 13, "bold"))
        ttk.Button(btn_row, text="Quit", command=self._on_quit).pack(side="right")
        self.connect_btn = ttk.Button(
            btn_row, text="Connect", command=self._on_connect, style="Accent.TButton",
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
# Streaming thread
# ---------------------------------------------------------------------------

class StreamReader(threading.Thread):
    """Background thread: reads parsed samples and appends to per-channel deques."""

    def __init__(self, conn: serial.Serial, channel_buffers: list[deque],
                 time_buffer: deque, expected_n: int, sample_interval_ms: int):
        super().__init__(daemon=True)
        self.conn = conn
        self.channel_buffers = channel_buffers
        self.time_buffer = time_buffer
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
                for ch_idx in range(5):
                    self.channel_buffers[ch_idx].append(values[1 + ch_idx])
                self.samples += 1

    @property
    def rate_hz(self) -> float:
        elapsed = time.monotonic() - self.start_time
        return self.samples / elapsed if elapsed > 0 else 0.0


# ---------------------------------------------------------------------------
# Visualization
# ---------------------------------------------------------------------------

FREQUENCY_BANDS: tuple[tuple[str, float, float, str], ...] = (
    ("Delta",  1.0,   4.0,  "#4363d8"),
    ("Theta",  4.0,   8.0,  "#3cb44b"),
    ("Alpha",  8.0,  13.0,  "#f58231"),
    ("Beta",  13.0,  30.0,  "#911eb4"),
    ("Gamma", 30.0,  50.0,  "#e6194B"),
)
BAND_ALL_EDGES = np.arange(0, 210, 10, dtype=float)
BAND_ALL_LABELS = [f"{int(BAND_ALL_EDGES[i + 1])}" for i in range(len(BAND_ALL_EDGES) - 1)]


def run_visualizer(conn: serial.Serial, cfg: dict,
                   window_seconds: float = 5.0,
                   psd_window_seconds: float = 10.0,
                   sma_short_seconds: float = 10.0,
                   sma_long_seconds: float = 30.0) -> None:
    import matplotlib
    if matplotlib.get_backend().lower() != "tkagg":
        matplotlib.use("TkAgg")
    import matplotlib.pyplot as plt
    from matplotlib.animation import FuncAnimation
    from matplotlib.widgets import Button, TextBox

    nominal_rate = cfg["lsl"]["nominal_srate"]
    max_psd_window_seconds = 60.0
    buffer_seconds = (
        max(window_seconds + sma_long_seconds, max_psd_window_seconds) * 1.2
    )
    maxlen = int(buffer_seconds * nominal_rate)
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

    fig = plt.figure(figsize=(15, 8), constrained_layout=True)
    fig.canvas.manager.set_window_title("Sentiometer Visualizer")
    outer = fig.add_gridspec(nrows=2, ncols=1, height_ratios=[18, 1], hspace=0.04)
    data_gs = outer[0].subgridspec(nrows=6, ncols=2, width_ratios=[3.0, 1.5],
                                    hspace=0.18, wspace=0.05)
    ctrl_gs = outer[1].subgridspec(nrows=1, ncols=4,
                                    width_ratios=[1.4, 0.4, 1.6, 6.0], wspace=0.4)

    trace_specs = [
        ("Mean", "#000000"), ("PD1", "#e6194B"), ("PD2", "#3cb44b"),
        ("PD3", "#4363d8"), ("PD4", "#f58231"), ("PD5", "#911eb4"),
    ]
    trace_axes = []
    trace_lines = []
    for row, (label, color) in enumerate(trace_specs):
        ax = fig.add_subplot(data_gs[row, 0],
                             sharex=trace_axes[0] if trace_axes else None)
        ax.set_ylabel(label, rotation=0, ha="right", va="center",
                      fontweight="bold", fontsize=13, labelpad=10,
                      color=color if label != "Mean" else "black")
        ax.grid(True, alpha=0.25)
        line, = ax.plot([], [], lw=1.2 if label == "Mean" else 1.0, color=color)
        trace_lines.append(line)
        trace_axes.append(ax)
        if row < len(trace_specs) - 1:
            ax.tick_params(labelbottom=False)
    trace_axes[-1].set_xlabel(f"Time (s, last {window_seconds:.0f} s window)")

    mean_ax = trace_axes[0]
    sma_short_line, = mean_ax.plot([], [], color="#e6194B", lw=1.4, alpha=0.9,
                                    label=f"{sma_short_seconds:.0f}s SMA")
    sma_long_line, = mean_ax.plot([], [], color="#1f77ff", lw=1.4, alpha=0.9,
                                   label=f"{sma_long_seconds:.0f}s SMA")
    mean_ax.legend(loc="upper right", fontsize=8, framealpha=0.85)

    panel_ax = fig.add_subplot(data_gs[:, 1])
    nyquist = nominal_rate / 2.0
    band_names = [b[0] for b in FREQUENCY_BANDS]
    band_colors = [b[3] for b in FREQUENCY_BANDS]
    band_x = np.arange(len(FREQUENCY_BANDS))

    panel_artists: dict = {"bars_eeg": None, "bars_all": None, "psd_line": None}

    def _clear_artists() -> None:
        for k in panel_artists:
            panel_artists[k] = None

    def _setup_band_eeg_view() -> None:
        panel_ax.clear()
        panel_ax.set_ylabel("Power (dB, arb.)")
        panel_ax.set_xlabel("")
        bars = panel_ax.bar(band_x, np.zeros(len(FREQUENCY_BANDS)),
                            color=band_colors, edgecolor="black", linewidth=0.5)
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
        cmap = plt.get_cmap("viridis")
        colors = [cmap(i / max(1, n_bins - 1)) for i in range(n_bins)]
        bars = panel_ax.bar(x, np.zeros(n_bins),
                            color=colors, edgecolor="black", linewidth=0.4)
        panel_ax.set_xticks(x)
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

    _build_view()
    _apply_title()

    toggle_ax = fig.add_subplot(ctrl_gs[0, 0])
    toggle_btn = Button(toggle_ax, "Show power spectrum",
                        color="#e8e8e8", hovercolor="#cfd8ff")
    toggle_btn.label.set_fontsize(10)

    textbox_ax = fig.add_subplot(ctrl_gs[0, 2])
    window_textbox = TextBox(textbox_ax, "Window (s):  ",
                              initial=f"{psd_window_seconds:.0f}",
                              textalignment="center")
    window_textbox.label.set_fontsize(10)

    def _toggle(_event):
        idx = MODE_ORDER.index(view_state["mode"])
        view_state["mode"] = MODE_ORDER[(idx + 1) % len(MODE_ORDER)]
        _build_view()
        toggle_btn.label.set_text(MODE_LABELS[view_state["mode"]][1])
        _apply_title()
        fig.canvas.draw_idle()

    def _on_window_submit(text: str) -> None:
        try:
            v = float(text)
        except ValueError:
            window_textbox.set_val(f"{view_state['seconds']:.0f}")
            return
        v = max(1.0, min(max_psd_window_seconds, v))
        view_state["seconds"] = v
        window_textbox.set_val(f"{v:.0f}")
        _apply_title()
        fig.canvas.draw_idle()

    toggle_btn.on_clicked(_toggle)
    window_textbox.on_submit(_on_window_submit)

    fig.suptitle("Sentiometer — live signal", fontsize=13, fontweight="bold", y=0.97)

    sma_short_samples = max(2, int(sma_short_seconds * nominal_rate))
    sma_long_samples = max(2, int(sma_long_seconds * nominal_rate))

    def _rolling_mean(x: np.ndarray, k: int) -> np.ndarray:
        if x.size == 0:
            return x
        cs = np.concatenate(([0.0], np.cumsum(x)))
        idx = np.arange(1, x.size + 1)
        starts = np.maximum(idx - k, 0)
        return (cs[idx] - cs[starts]) / (idx - starts)

    band_edges = np.array([(b[1], b[2]) for b in FREQUENCY_BANDS])

    def _update(_frame):
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

        mask = ts >= t_min
        all_traces = [mean_signal] + per_channel
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

        sma_short_full = _rolling_mean(mean_signal, sma_short_samples)
        sma_long_full = _rolling_mean(mean_signal, sma_long_samples)
        sma_short_line.set_data(ts, sma_short_full)
        sma_long_line.set_data(ts, sma_long_full)
        if mask.any():
            stk = np.concatenate([
                mean_signal[mask], sma_short_full[mask], sma_long_full[mask],
            ])
            lo, hi = float(stk.min()), float(stk.max())
            if lo == hi:
                lo, hi = lo - 1, hi + 1
            pad = (hi - lo) * 0.08
            mean_ax.set_ylim(lo - pad, hi + pad)

        psd_window_samples = max(64, int(view_state["seconds"] * nominal_rate))
        if mean_signal.size >= 64:
            seg = mean_signal[-psd_window_samples:]
            seg = seg - seg.mean()
            window = np.hanning(seg.size)
            spec = np.fft.rfft(seg * window)
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

    _anim = FuncAnimation(fig, _update, interval=33, blit=False, cache_frame_data=False)
    fig._sentiometer_widgets = (toggle_btn, window_textbox, _anim)

    try:
        plt.show()
    finally:
        reader.stop()
        reader.join(timeout=2.0)


# ---------------------------------------------------------------------------
# Connect + entry point
# ---------------------------------------------------------------------------

def open_and_prime(cfg: dict, port: str, duration_min: int) -> serial.Serial:
    cfg["serial"]["port"] = port
    sample_ms = cfg["device"]["sample_interval_ms"]
    cfg["device"]["start_command"] = f"{duration_min:05d} {sample_ms}"

    conn = open_serial(cfg)
    conn.reset_input_buffer()
    line_ending = cfg["serial"].get("line_ending", "\r\n")
    send_command(conn, cfg["device"]["start_command"], line_ending)

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


def main() -> None:
    cfg = load_config()

    # Pre-import matplotlib BEFORE the connect dialog so the post-Connect
    # transition isn't held up by the first matplotlib import + font cache.
    import matplotlib
    matplotlib.use("TkAgg")
    import matplotlib.pyplot  # noqa: F401

    selection = run_connect_dialog()
    if selection is None:
        return

    progress_root = tk.Tk()
    progress_root.title("Sentiometer Visualizer")
    progress_root.geometry("440x130")
    progress_root.resizable(False, False)
    msg_var = tk.StringVar(value=f"Opening {selection['port']}…")
    tk.Label(progress_root, textvariable=msg_var, padx=20, pady=20,
             justify="left", wraplength=400).pack(fill="both", expand=True)
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
    except Exception as e:  # noqa: BLE001
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
