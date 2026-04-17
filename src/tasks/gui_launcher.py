"""Tkinter session launcher for the P013 task suite.

A single-window GUI that replaces the terminal launcher for live sessions.
The RA enters a participant ID, works through the pre-launch checklist, and
hits Start. The five tasks run sequentially; between each task a "Ready for
Next Task" button appears so the RA can check on the participant before
advancing.

The heavy lifting is delegated to :func:`tasks.launcher.run_session` — the
GUI only provides the interactive surface (fields, checkboxes, event log)
and a custom ``task_runner`` that iconifies the window around each task and
blocks on a Tk event for the between-task advance.

Run with::

    uv run python -m tasks.gui_launcher

Styling is the Jazzo palette: purple / white / black.
"""

from __future__ import annotations

import queue
import tkinter as tk
import traceback
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk
from typing import Any, Callable

from pylsl import StreamOutlet

from tasks.common.lsl_markers import create_session_outlet, send_marker
from tasks.launcher import (
    DEFAULT_DATA_ROOT,
    TASK_DISPLAY_NAMES,
    TASK_ORDER,
    _check_vayl_reachable,
    _resolve_by_name,
    _resolve_by_type,
    _run_task,
    _vayl_stop_overlay,
    run_session,
)


def _preload_stim_modules() -> None:
    """Import PsychoPy / Pygame on the calling thread.

    Must be called AFTER ``tk.Tk()`` has been constructed. Tk wants to be the
    first thing to register an ``NSApplication`` on macOS; if Pygame's SDL
    gets there first, Tcl/Tk 9 crashes with ``unrecognized selector
    -[NSApplication macOSVersion]`` at window creation. Preloading here
    (after Tk is up) also guarantees any lazy Cocoa init happens on the main
    thread, so later task code can run ``visual.Window()`` safely from the
    same thread without crossing an NSWindow-on-worker-thread boundary.
    """
    import pygame  # noqa: F401, PLC0415

    from tasks.common import audio  # noqa: F401, PLC0415
    from tasks.common import display  # noqa: F401, PLC0415
    from tasks.common import instructions  # noqa: F401, PLC0415


# ----- styling ---------------------------------------------------------------

PURPLE = "#5d5179"
WHITE = "#ffffff"
BLACK = "#000000"
GRAY_LIGHT = "#e0e0e0"
GRAY_MID = "#888888"
GREEN = "#2a8a3e"
RED = "#b33838"
YELLOW = "#c98500"


# ----- status chip colors ----------------------------------------------------

STATUS_COLORS = {
    "pending": GRAY_MID,
    "running": PURPLE,
    "completed": GREEN,
    "failed": RED,
    "aborted": RED,
    "skipped": GRAY_MID,
}


class GuiLauncher:
    """Tk-based launcher for the P013 session."""

    # Stable participant ID used for the marker outlet's `source_id` from the
    # moment the GUI opens until it closes. MUST stay constant for the whole
    # run: LabRecorder identifies streams by (name, source_id), so if we
    # change source_id mid-session (e.g. swap from "LAUNCHER_READY" to the
    # real pid when Start is clicked) LabRecorder treats the new outlet as
    # a different stream and keeps its inlet bound to the old, now-gone
    # one — samples never reach the XDF. We emit the real participant id
    # as a marker (`participant_id:P001`) instead of encoding it into the
    # source_id.
    OUTLET_STABLE_ID = "Launcher"

    def __init__(self) -> None:
        self.outlet: StreamOutlet | None = None

        # Session state
        self.session_active = False
        self.abort_requested = False
        # Vayl reachability is a HARD prerequisite for starting a session
        # (Task 05 requires it). Updated by _run_preflight_checks; the
        # Start button watches this.
        self.vayl_ok = False

        # Per-task state (keyed by task_name)
        self.task_status_labels: dict[str, tk.Label] = {}
        self.task_time_labels: dict[str, tk.Label] = {}
        self.task_enabled_vars: dict[str, tk.BooleanVar] = {}
        self.task_checkboxes: dict[str, tk.Checkbutton] = {}
        self._task_start_times: dict[str, float] = {}

        # Thread-safe UI update queue
        self._ui_queue: queue.Queue[Callable[[], None]] = queue.Queue()

        # Build window. Tk.Tk() MUST be constructed before any import of
        # pygame / psychopy (those transitively init SDL or Cocoa in a way
        # that breaks Tcl/Tk on macOS if they run first).
        self.root = tk.Tk()
        self.root.title("P013 Session Launcher")
        self.root.configure(bg=WHITE)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._size_window()
        self._configure_styles()
        self._create_widgets()

        # Preload stim modules now that Tk owns NSApplication.
        _preload_stim_modules()

        # Open the LSL marker outlet right away so LabRecorder sees
        # `P013_Task_Markers` as long as this GUI is up. The outlet is
        # swapped for one bearing the real participant ID when Start is
        # clicked.
        self._open_outlet()

        # Kick off UI pump. Schedule one preflight check after the Tk
        # mainloop is up so the Vayl / EEG / Sentiometer rows populate
        # without the RA having to click "Re-check hardware" first. The
        # check runs on the main thread (required for macOS TCC system
        # dialogs not to crash); the ~5 s freeze happens after the window
        # has already rendered, so the RA sees UI before the freeze.
        self._poll_ui_queue()
        self.root.after(300, self._run_preflight_checks)

    # ----- window setup -----------------------------------------------------

    def _size_window(self) -> None:
        self.root.update_idletasks()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        w = min(820, max(600, sw - 100))
        h = min(980, max(600, sh - 120))
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 2 - 20)
        self.root.geometry(f"{w}x{h}+{x}+{y}")
        self.root.minsize(560, 500)

    def _configure_styles(self) -> None:
        self.style = ttk.Style()
        self.style.theme_use("clam")
        self.style.configure(
            "Purple.TButton",
            background=PURPLE,
            foreground=WHITE,
            font=("Arial", 13, "bold"),
            padding=(10, 12),
        )
        self.style.map(
            "Purple.TButton",
            background=[("active", BLACK), ("disabled", GRAY_LIGHT)],
            foreground=[("active", WHITE), ("disabled", GRAY_MID)],
        )
        self.style.configure(
            "Disabled.TButton",
            background=GRAY_LIGHT,
            foreground=GRAY_MID,
            font=("Arial", 13, "bold"),
            padding=(10, 12),
        )
        self.style.configure(
            "Abort.TButton",
            background=WHITE,
            foreground=BLACK,
            font=("Arial", 13, "bold"),
            padding=(10, 12),
        )
        self.style.map(
            "Abort.TButton",
            background=[("active", PURPLE), ("disabled", GRAY_LIGHT)],
            foreground=[("active", WHITE), ("disabled", GRAY_MID)],
        )
        self.style.configure(
            "Small.TButton",
            background=WHITE,
            foreground=PURPLE,
            font=("Arial", 10),
            padding=(6, 4),
        )

    # ----- widget layout ----------------------------------------------------

    def _create_widgets(self) -> None:
        outer = tk.Frame(self.root, bg=WHITE)
        outer.pack(fill=tk.BOTH, expand=True)

        self._canvas = tk.Canvas(outer, bg=WHITE, highlightthickness=0)
        vbar = ttk.Scrollbar(outer, orient=tk.VERTICAL, command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=vbar.set)
        vbar.pack(side=tk.RIGHT, fill=tk.Y)
        self._canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        main = tk.Frame(self._canvas, padx=20, pady=20, bg=WHITE)
        window_id = self._canvas.create_window((0, 0), window=main, anchor="nw")

        main.bind(
            "<Configure>",
            lambda _e: self._canvas.configure(scrollregion=self._canvas.bbox("all")),
        )
        self._canvas.bind(
            "<Configure>",
            lambda e: self._canvas.itemconfig(window_id, width=e.width),
        )

        def _on_mousewheel(event: tk.Event) -> None:
            widget = self.root.winfo_containing(event.x_root, event.y_root)
            if isinstance(widget, tk.Text):
                return
            self._canvas.yview_scroll(int(-event.delta / 120), "units")

        self._canvas.bind(
            "<Enter>",
            lambda _e: self._canvas.bind_all("<MouseWheel>", _on_mousewheel),
        )
        self._canvas.bind(
            "<Leave>", lambda _e: self._canvas.unbind_all("<MouseWheel>")
        )

        self._build_session_info(main)
        self._build_checklist(main)
        self._build_task_list(main)
        self._build_control(main)
        self._build_log(main)

        self.root.bind("<Return>", self._on_enter_pressed)

    def _build_session_info(self, parent: tk.Widget) -> None:
        frame = tk.LabelFrame(
            parent,
            text="Session Info",
            font=("Arial", 12, "bold"),
            padx=10,
            pady=10,
            bg=WHITE,
            fg=PURPLE,
        )
        frame.pack(fill=tk.X, pady=(0, 12))

        tk.Label(
            frame,
            text="Participant ID (e.g. P001):",
            font=("Arial", 11),
            bg=WHITE,
            fg=BLACK,
        ).pack(anchor=tk.W)

        self.participant_id_var = tk.StringVar()
        self.participant_id_var.trace_add("write", self._update_start_button)
        tk.Entry(
            frame,
            textvariable=self.participant_id_var,
            font=("Arial", 14),
            width=30,
            bg=WHITE,
            fg=BLACK,
            insertbackground=PURPLE,
            highlightthickness=1,
            highlightcolor=PURPLE,
            highlightbackground=BLACK,
        ).pack(fill=tk.X, pady=(5, 8))

        self.demo_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            frame,
            text="Demo mode (short trials, no hardware checks)",
            variable=self.demo_var,
            font=("Arial", 11),
            bg=WHITE,
            fg=BLACK,
            activebackground=WHITE,
            selectcolor=WHITE,
        ).pack(anchor=tk.W)

    def _build_checklist(self, parent: tk.Widget) -> None:
        frame = tk.LabelFrame(
            parent,
            text="Pre-Launch Checklist",
            font=("Arial", 12, "bold"),
            padx=10,
            pady=10,
            bg=WHITE,
            fg=PURPLE,
        )
        frame.pack(fill=tk.X, pady=(0, 12))

        # Manual checklist items. Each row: checkbox + label.
        self.manual_checks = []
        manual_items = [
            "LabRecorder is running and recording",
            "Headphones connected and volume set",
            "Participant is seated and calibrated",
        ]
        for item in manual_items:
            var = tk.BooleanVar()
            var.trace_add("write", self._update_start_button)
            tk.Checkbutton(
                frame,
                text=item,
                variable=var,
                font=("Arial", 11),
                bg=WHITE,
                fg=BLACK,
                activebackground=WHITE,
                selectcolor=WHITE,
            ).pack(anchor=tk.W)
            self.manual_checks.append(var)

        # Auto-detected LSL streams. Row: label, status, re-check button.
        auto_frame = tk.Frame(frame, bg=WHITE)
        auto_frame.pack(fill=tk.X, pady=(10, 0))

        self.auto_status_labels: dict[str, tk.Label] = {}

        def _add_auto_row(key: str, label_text: str) -> None:
            row = tk.Frame(auto_frame, bg=WHITE)
            row.pack(fill=tk.X, pady=2)
            tk.Label(
                row, text=label_text, font=("Arial", 11), bg=WHITE, fg=BLACK, width=32, anchor="w"
            ).pack(side=tk.LEFT)
            status = tk.Label(
                row, text="(not checked)", font=("Arial", 11, "bold"), bg=WHITE, fg=GRAY_MID
            )
            status.pack(side=tk.LEFT)
            self.auto_status_labels[key] = status

        _add_auto_row("eeg", "EEG stream (type=EEG):")
        _add_auto_row("sentiometer", "Sentiometer stream (IACS_Sentiometer):")
        _add_auto_row("vayl", "Vayl app (localhost:9471):")

        recheck_btn = ttk.Button(
            auto_frame,
            text="Re-check hardware",
            style="Small.TButton",
            command=self._run_preflight_checks,
        )
        recheck_btn.pack(anchor=tk.W, pady=(8, 0))

    def _build_task_list(self, parent: tk.Widget) -> None:
        frame = tk.LabelFrame(
            parent,
            text="Tasks (uncheck to skip)",
            font=("Arial", 12, "bold"),
            padx=10,
            pady=10,
            bg=WHITE,
            fg=PURPLE,
        )
        frame.pack(fill=tk.X, pady=(0, 12))

        for task_name, _ in TASK_ORDER:
            row = tk.Frame(frame, bg=WHITE)
            row.pack(fill=tk.X, pady=3)

            display = TASK_DISPLAY_NAMES.get(task_name, task_name)
            enabled = tk.BooleanVar(master=self.root, value=True)
            self.task_enabled_vars[task_name] = enabled

            cb = tk.Checkbutton(
                row,
                text=display,
                variable=enabled,
                font=("Arial", 11),
                bg=WHITE,
                fg=BLACK,
                activebackground=WHITE,
                selectcolor=WHITE,
                anchor="w",
                width=36,
            )
            cb.pack(side=tk.LEFT)
            self.task_checkboxes[task_name] = cb

            status = tk.Label(
                row,
                text="PENDING",
                font=("Arial", 11, "bold"),
                bg=WHITE,
                fg=STATUS_COLORS["pending"],
                width=12,
            )
            status.pack(side=tk.LEFT, padx=(10, 10))
            elapsed = tk.Label(
                row, text="", font=("Arial", 11), bg=WHITE, fg=GRAY_MID, width=10
            )
            elapsed.pack(side=tk.LEFT)
            self.task_status_labels[task_name] = status
            self.task_time_labels[task_name] = elapsed

    def _build_control(self, parent: tk.Widget) -> None:
        frame = tk.LabelFrame(
            parent,
            text="Control",
            font=("Arial", 12, "bold"),
            padx=10,
            pady=10,
            bg=WHITE,
            fg=PURPLE,
        )
        frame.pack(fill=tk.X, pady=(0, 12))

        btns = tk.Frame(frame, bg=WHITE)
        btns.pack(fill=tk.X)

        self.start_button = ttk.Button(
            btns,
            text="Start Session",
            command=self._start_session,
            style="Disabled.TButton",
            state=tk.DISABLED,
            cursor="hand2",
        )
        self.start_button.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5), pady=5)

        self.abort_button = ttk.Button(
            btns,
            text="Abort",
            command=self._abort_session,
            style="Abort.TButton",
            state=tk.DISABLED,
            cursor="hand2",
        )
        self.abort_button.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(5, 0), pady=5)

        # Live status line.
        self.current_status_label = tk.Label(
            frame, text="Idle", font=("Arial", 12), bg=WHITE, fg=PURPLE
        )
        self.current_status_label.pack(anchor=tk.W, pady=(10, 0))

    def _build_log(self, parent: tk.Widget) -> None:
        frame = tk.LabelFrame(
            parent,
            text="Event Log",
            font=("Arial", 12, "bold"),
            padx=10,
            pady=10,
            bg=WHITE,
            fg=PURPLE,
        )
        frame.pack(fill=tk.BOTH, expand=True)

        self.log_text = scrolledtext.ScrolledText(
            frame,
            font=("Courier", 10),
            height=12,
            state=tk.DISABLED,
            bg=WHITE,
            fg=BLACK,
            insertbackground=PURPLE,
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)

    # ----- UI helpers -------------------------------------------------------

    def _log(self, msg: str) -> None:
        """Append a timestamped line to the log (thread-safe)."""
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}\n"

        def do() -> None:
            self.log_text.config(state=tk.NORMAL)
            self.log_text.insert(tk.END, line)
            self.log_text.see(tk.END)
            self.log_text.config(state=tk.DISABLED)

        self._ui_queue.put(do)

    def _set_status(self, text: str) -> None:
        self._ui_queue.put(lambda: self.current_status_label.config(text=text))

    def _set_task_status(self, task_name: str, status: str) -> None:
        label = self.task_status_labels.get(task_name)
        if not label:
            return
        color = STATUS_COLORS.get(status, GRAY_MID)
        self._ui_queue.put(lambda: label.config(text=status.upper(), fg=color))

    def _set_task_elapsed(self, task_name: str, elapsed_s: float) -> None:
        label = self.task_time_labels.get(task_name)
        if not label:
            return
        text = f"{int(elapsed_s // 60):d}m {int(elapsed_s % 60):02d}s"
        self._ui_queue.put(lambda: label.config(text=text))

    def _poll_ui_queue(self) -> None:
        try:
            while True:
                func = self._ui_queue.get_nowait()
                try:
                    func()
                except tk.TclError:
                    pass
        except queue.Empty:
            pass
        self.root.after(50, self._poll_ui_queue)

    # ----- pre-flight -------------------------------------------------------

    def _run_preflight_checks(self) -> None:
        """Refresh the auto LSL/Vayl checklist rows synchronously on the main
        thread.

        LSL ``resolve_byprop`` and the Vayl ``urlopen`` may trigger macOS
        system dialogs (local-network permission, firewall prompt) the first
        time they run. Cocoa requires NSWindow instantiation on the main
        thread, so we can't delegate to a worker thread here — we block the
        UI for a few seconds instead. The ``_ui_queue`` pump still advances
        via the Tk mainloop because the resolve calls yield while waiting
        on the network.
        """
        self._log("Running hardware checks — GUI may freeze for a few seconds…")
        self.root.update_idletasks()
        eeg = _resolve_by_type("EEG", timeout=1.5)
        self._update_auto_row("eeg", eeg, blocking=False)
        self.root.update_idletasks()
        sent = _resolve_by_name("IACS_Sentiometer", timeout=1.5)
        self._update_auto_row("sentiometer", sent, blocking=False)
        self.root.update_idletasks()
        vayl = _check_vayl_reachable()
        # Vayl is a *hard* requirement (Task 05 needs the desktop app). The
        # auto-row for Vayl shows red FAIL rather than yellow WARN, and the
        # Start button stays disabled until this flips green.
        self._update_auto_row("vayl", vayl, blocking=True)
        self.vayl_ok = bool(vayl)
        self._log(
            f"Hardware checks complete — EEG={eeg}, "
            f"Sentiometer={sent}, Vayl={vayl} "
            f"({'BLOCKER cleared' if vayl else 'BLOCKER — open Vayl desktop app'})"
        )
        self._update_start_button()

    def _update_auto_row(self, key: str, ok: bool, blocking: bool) -> None:
        label = self.auto_status_labels.get(key)
        if not label:
            return
        text = "OK" if ok else ("FAIL" if blocking else "WARN")
        color = GREEN if ok else (RED if blocking else YELLOW)
        self._ui_queue.put(lambda: label.config(text=text, fg=color))

    # ----- start button state ----------------------------------------------

    def _update_start_button(self, *_args: Any) -> None:
        if self.session_active:
            return
        have_id = bool(self.participant_id_var.get().strip())
        demo = self.demo_var.get()
        # Vayl reachability is required in both demo and production — the
        # demo run still exercises Task 05 against the live bridge, and
        # letting the RA start without it produces a confusing mid-session
        # failure.
        checks_pass = (demo or all(v.get() for v in self.manual_checks)) and self.vayl_ok
        if have_id and checks_pass:
            self.start_button.config(state=tk.NORMAL, style="Purple.TButton")
        else:
            self.start_button.config(state=tk.DISABLED, style="Disabled.TButton")

    # ----- session control --------------------------------------------------

    def _open_outlet(self) -> None:
        """Create the single stable P013 marker outlet.

        Idempotent — if the outlet already exists it's left alone. The
        outlet is created once at GUI startup with a fixed
        ``source_id = f'P013_{OUTLET_STABLE_ID}'`` and kept alive for the
        entire lifetime of the process. This is critical: LabRecorder
        identifies streams by (name, source_id), so swapping source_id
        mid-session silently breaks its subscription and produces an
        empty marker stream in the XDF.
        """
        if self.outlet is not None:
            return
        try:
            self.outlet = create_session_outlet(self.OUTLET_STABLE_ID)
            self._log(
                f"LSL outlet live: P013_Task_Markers "
                f"(source_id=P013_{self.OUTLET_STABLE_ID})"
            )
        except Exception as exc:  # noqa: BLE001
            self._log(f"Failed to open LSL outlet: {exc}")

    def _start_session(self) -> None:
        """Run the whole session on the MAIN thread.

        macOS requires PsychoPy/Pygame windows to be created on the main
        thread; running the session in a worker crashes with "NSWindow should
        only be instantiated on the main thread". The launcher window is
        hidden for the full duration of the session so the participant sees
        only the task stim; it reappears once every task has finished and
        the session log is saved.
        """
        pid = self.participant_id_var.get().strip()
        if not pid:
            return
        self.session_active = True
        self.abort_requested = False
        self.start_button.config(state=tk.DISABLED, style="Disabled.TButton")
        self.abort_button.config(state=tk.NORMAL)
        # Lock task selection for the duration of the session.
        for cb in self.task_checkboxes.values():
            cb.config(state=tk.DISABLED)
        self._set_status(f"Session running — {pid}")
        self._log("=" * 40)
        self._log(f"SESSION START — participant {pid}")
        self._log("=" * 40)

        try:
            # The outlet was created once at GUI startup with a stable
            # source_id; do NOT recreate it here (would break LabRecorder's
            # subscription). Emit the participant id as its own marker so
            # the XDF still records who this session was for.
            if self.outlet is not None:
                send_marker(self.outlet, f"participant_id:{pid}")

            # Belt-and-braces: make sure the Vayl overlay is OFF before
            # Task 01 starts. Tasks 01-04 must never have the strobing
            # checkerboard running behind them. Task 05 will turn it ON
            # when it needs to and OFF again at ramp_end.
            if self.vayl_ok:
                stopped = _vayl_stop_overlay()
                self._log(
                    "Pre-session Vayl overlay-off: "
                    + ("OK" if stopped else "WARN — POST failed (not fatal)")
                )
            self.root.update_idletasks()

            # Hide the launcher for the entire session — participants never
            # see the Tk window between tasks. It reappears in the `finally`
            # block once the log has been persisted.
            self.root.withdraw()
            self.root.update()

            run_session(
                participant_id=pid,
                demo=self.demo_var.get(),
                interactive=False,
                task_runner=self._gui_task_runner,
                outlet=self.outlet,
            )
            if not self.abort_requested:
                self._log("=" * 40)
                self._log("SESSION COMPLETE")
                self._log("=" * 40)
                self._set_status("Session complete")
            else:
                self._set_status("Session aborted")
        except Exception as exc:  # noqa: BLE001
            self._log(f"ERROR: {exc}")
            self._log(traceback.format_exc())
            self._set_status("Session failed — see log")
        finally:
            # Bring the launcher back so the RA can see the event log and
            # task statuses. Do this before swapping outlets so the user
            # gets visual feedback immediately.
            try:
                self.root.deiconify()
                self.root.lift()
                self.root.focus_force()
            except tk.TclError:
                pass
            # Outlet stays alive between sessions (idempotent _open_outlet
            # is a no-op once set). Real cleanup happens in _on_close.
            self._reset_ui_after_session()

    def _abort_session(self) -> None:
        self.abort_requested = True
        self._log("Abort requested — the session will stop after the current task")
        self._set_status("Aborting after current task…")
        self.abort_button.config(state=tk.DISABLED)

    def _reset_ui_after_session(self) -> None:
        self.session_active = False
        self.abort_button.config(state=tk.DISABLED)
        for cb in self.task_checkboxes.values():
            cb.config(state=tk.NORMAL)
        self._update_start_button()

    # ----- per-task wrapper -------------------------------------------------

    def _gui_task_runner(
        self,
        task_name: str,
        module_path: str,
        outlet: StreamOutlet,
        session_config: dict,
        participant_id: str,
        demo: bool,
        data_root: Path,
    ) -> tuple[str, str | None]:
        """Run a single task on the main thread.

        The Tk window is already hidden (done once in ``_start_session``) so
        tasks flow directly into each other without an intermediate
        "Ready for Next Task" prompt — the participant only ever sees the
        current task's stim window.
        """
        if self.abort_requested:
            return ("skipped", "session aborted")

        display = TASK_DISPLAY_NAMES.get(task_name, task_name)

        # Respect the per-task "run / skip" checkbox.
        enabled_var = self.task_enabled_vars.get(task_name)
        if enabled_var is not None and not enabled_var.get():
            self._set_task_status(task_name, "skipped")
            self._log(f"--- {display}: skipped (unchecked)")
            return ("skipped", "unchecked in launcher")

        self._log(f">>> {display}: starting")
        self._set_task_status(task_name, "running")
        self._set_status(f"Running: {display}")
        start_ts = datetime.now().timestamp()
        self._task_start_times[task_name] = start_ts

        status, error = _run_task(
            task_name,
            module_path,
            outlet,
            session_config,
            participant_id,
            demo,
            data_root,
        )

        elapsed = datetime.now().timestamp() - start_ts
        self._set_task_elapsed(task_name, elapsed)
        self._set_task_status(task_name, status)
        if error:
            self._log(f"<<< {display}: {status.upper()} — {error}")
        else:
            self._log(f"<<< {display}: {status.upper()} ({int(elapsed)}s)")

        return (status, error)

    def _on_enter_pressed(self, _event: tk.Event) -> None:
        # Hook kept for future use; no-op while a session is active.
        return None

    # ----- lifecycle --------------------------------------------------------

    def _on_close(self) -> None:
        if self.session_active:
            if not messagebox.askyesno(
                "Quit?",
                "A session is currently running. Aborting will mark it as "
                "aborted in the session log. Quit anyway?",
            ):
                return
            self._abort_session()
        if self.outlet is not None:
            try:
                del self.outlet
            except Exception:  # noqa: BLE001
                pass
        self.root.destroy()

    def run(self) -> None:
        self._log("P013 GUI launcher started")
        self._log(
            "Enter a participant ID, complete the checklist, then hit "
            "'Start Session'."
        )
        self.root.mainloop()


def main() -> None:
    """Module entry point — `python -m tasks.gui_launcher`."""
    # Force data root to exist so session_log.json writes don't trip on missing
    # parent dirs when the user picks an ID for the very first time.
    DEFAULT_DATA_ROOT.mkdir(parents=True, exist_ok=True)
    try:
        GuiLauncher().run()
    except BaseException:  # noqa: BLE001
        # Log any unhandled exception so we can tell the difference between a
        # harmless pyglet teardown "exception ignored" (which the interpreter
        # still surfaces as exit 1) and a real bug in the GUI layer.
        import sys
        import traceback as _tb

        print("\n===== Unhandled exception in GUI launcher =====", file=sys.stderr)
        _tb.print_exc(file=sys.stderr)
        raise


if __name__ == "__main__":
    main()
