"""Tkinter user interface for Auto Key."""

from __future__ import annotations

import copy
import math
import queue
import re
import time
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any

from config import BUILT_IN_PRESETS, DEFAULT_SETTINGS, PRESET_KEYS, load_settings, preset_values, save_settings
from hotkey import GlobalHotkeyMonitor, normalize_hotkey
from typing_engine import (
    EngineSettings,
    TypingEngine,
    TypingValidationError,
    build_actions,
    validate_engine_settings,
    validate_job,
)


def format_time(seconds: float, signed: bool = False) -> str:
    prefix = ""
    if signed:
        prefix = "+" if seconds >= 0 else "-"
    seconds = abs(seconds)
    whole = int(round(seconds))
    minutes, secs = divmod(whole, 60)
    hours, minutes = divmod(minutes, 60)
    body = f"{hours:d}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"
    return prefix + body


class TypingAutomationApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.settings = load_settings()
        self.events: queue.Queue[dict[str, Any]] = queue.Queue()
        self.engine = TypingEngine(self.events.put)
        self.hotkey = GlobalHotkeyMonitor(lambda: self.events.put({"type": "emergency"}))
        self._countdown_after_id: str | None = None
        self._countdown_deadline = 0.0
        self._pending_job: tuple[str, EngineSettings] | None = None
        self._active_run_id: int | None = None
        self._state = "READY"
        self._closing = False
        self._suspend_preview = False

        self.root.title("Auto Key - Timed Typing Utility")
        self.root.geometry(str(self.settings.get("window_geometry", DEFAULT_SETTINGS["window_geometry"])))
        self.root.minsize(880, 650)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._configure_styles()
        self._create_variables()
        self._build_interface()
        self._bind_updates()
        self._restart_hotkey(show_error=False)
        self._set_state("READY")
        self._update_text_stats()
        self.root.after(40, self._poll_events)

    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        for preferred in ("vista", "xpnative", "clam"):
            if preferred in style.theme_names():
                style.theme_use(preferred)
                break
        style.configure("Status.TLabel", font=("Segoe UI", 15, "bold"))
        style.configure("Countdown.TLabel", font=("Segoe UI", 23, "bold"), anchor="center")
        style.configure("Section.TLabelframe.Label", font=("Segoe UI", 10, "bold"))
        style.configure("Primary.TButton", font=("Segoe UI", 10, "bold"))

    def _create_variables(self) -> None:
        string_keys = (
            "duration_minutes", "countdown_seconds", "line_break_method", "custom_line_break",
            "emergency_hotkey", "minimum_delay", "maximum_delay", "variation_percent", "word_delay",
            "comma_delay", "period_delay", "question_delay", "exclamation_delay", "semicolon_delay",
            "colon_delay", "line_break_delay", "speed_multiplier", "selected_preset",
            "humanization_mode",
        )
        self.vars: dict[str, tk.Variable] = {}
        for key in string_keys:
            self.vars[key] = tk.StringVar(value=str(self.settings[key]))
        self.vars["random_variation"] = tk.BooleanVar(value=bool(self.settings["random_variation"]))
        self.vars["notify_on_completion"] = tk.BooleanVar(value=bool(self.settings["notify_on_completion"]))

        self.text_stats_var = tk.StringVar(value="Characters: 0    Words: 0    Lines: 0")
        self.preview_var = tk.StringVar()
        self.override_var = tk.StringVar(value="Dynamic pacing scales configured delays to meet the target.")
        self.status_var = tk.StringVar(value="Status: READY")
        self.countdown_var = tk.StringVar(value="Ready to start")
        self.progress_text_var = tk.StringVar(value="0 / 0 characters (0.0%)")
        self.words_progress_var = tk.StringVar(value="Words: 0 / 0")
        self.elapsed_var = tk.StringVar(value="Elapsed: 00:00")
        self.remaining_var = tk.StringVar(value="Remaining: 00:00")
        self.speed_var = tk.StringVar(value="Speed: 0 CPM  |  0 WPM")
        self.completion_var = tk.StringVar(value="")
        self.emergency_display_var = tk.StringVar(value=f"Emergency Stop: {self.settings['emergency_hotkey']}")
        self.custom_preset_name_var = tk.StringVar()

    def _build_interface(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        notebook = ttk.Notebook(self.root)
        notebook.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        main_tab = ttk.Frame(notebook, padding=8)
        advanced_tab = ttk.Frame(notebook, padding=14)
        notebook.add(main_tab, text="Main")
        notebook.add(advanced_tab, text="Advanced Settings")
        self._build_main_tab(main_tab)
        self._build_advanced_tab(advanced_tab)

    def _build_main_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=3)
        parent.columnconfigure(1, weight=2)
        parent.rowconfigure(0, weight=1)

        text_frame = ttk.LabelFrame(parent, text="Text to Type", style="Section.TLabelframe", padding=8)
        text_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        text_frame.columnconfigure(0, weight=1)
        text_frame.rowconfigure(0, weight=1)
        self.text_box = tk.Text(text_frame, wrap="word", undo=True, font=("Segoe UI", 10), padx=7, pady=7)
        scroll = ttk.Scrollbar(text_frame, orient="vertical", command=self.text_box.yview)
        self.text_box.configure(yscrollcommand=scroll.set)
        self.text_box.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        ttk.Label(text_frame, textvariable=self.text_stats_var).grid(row=1, column=0, sticky="w", pady=(7, 0))

        preview = ttk.LabelFrame(text_frame, text="Typing Preview", style="Section.TLabelframe", padding=8)
        preview.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Label(preview, textvariable=self.preview_var, justify="left").grid(row=0, column=0, sticky="w")
        ttk.Label(preview, textvariable=self.override_var, foreground="#805900", wraplength=560).grid(
            row=1, column=0, sticky="w", pady=(5, 0)
        )

        side = ttk.Frame(parent)
        side.grid(row=0, column=1, sticky="nsew")
        side.columnconfigure(0, weight=1)

        target = ttk.LabelFrame(side, text="Timing and Input", style="Section.TLabelframe", padding=10)
        target.grid(row=0, column=0, sticky="ew")
        target.columnconfigure(1, weight=1)
        ttk.Label(target, text="Target duration:").grid(row=0, column=0, sticky="w", pady=3)
        duration_row = ttk.Frame(target)
        duration_row.grid(row=0, column=1, sticky="ew", pady=3)
        ttk.Entry(duration_row, textvariable=self.vars["duration_minutes"], width=10).pack(side="left")
        ttk.Label(duration_row, text=" minutes").pack(side="left")

        ttk.Label(target, text="Start delay:").grid(row=1, column=0, sticky="w", pady=3)
        countdown = ttk.Combobox(
            target,
            textvariable=self.vars["countdown_seconds"],
            values=("10", "20", "25", "30", "45", "60"),
            width=12,
        )
        countdown.grid(row=1, column=1, sticky="w", pady=3)
        ttk.Label(target, text="seconds").grid(row=1, column=1, sticky="w", padx=(115, 0))

        ttk.Label(target, text="Line break method:").grid(row=2, column=0, sticky="w", pady=3)
        method = ttk.Combobox(
            target,
            textvariable=self.vars["line_break_method"],
            values=("Shift + Enter", "Enter", "Custom key combination"),
            state="readonly",
            width=24,
        )
        method.grid(row=2, column=1, sticky="ew", pady=3)
        ttk.Label(target, text="Custom shortcut:").grid(row=3, column=0, sticky="w", pady=3)
        self.custom_shortcut_entry = ttk.Entry(target, textvariable=self.vars["custom_line_break"])
        self.custom_shortcut_entry.grid(row=3, column=1, sticky="ew", pady=3)

        status = ttk.LabelFrame(side, text="Status", style="Section.TLabelframe", padding=10)
        status.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        status.columnconfigure(0, weight=1)
        ttk.Label(status, textvariable=self.status_var, style="Status.TLabel", anchor="center").grid(
            row=0, column=0, sticky="ew"
        )
        ttk.Label(status, textvariable=self.countdown_var, style="Countdown.TLabel").grid(
            row=1, column=0, sticky="ew", pady=(4, 8)
        )
        self.progress = ttk.Progressbar(status, maximum=100.0, mode="determinate")
        self.progress.grid(row=2, column=0, sticky="ew")
        ttk.Label(status, textvariable=self.progress_text_var, anchor="center").grid(row=3, column=0, sticky="ew", pady=3)
        metrics = ttk.Frame(status)
        metrics.grid(row=4, column=0, sticky="ew", pady=(4, 0))
        metrics.columnconfigure((0, 1), weight=1)
        ttk.Label(metrics, textvariable=self.words_progress_var).grid(row=0, column=0, sticky="w")
        ttk.Label(metrics, textvariable=self.elapsed_var).grid(row=1, column=0, sticky="w")
        ttk.Label(metrics, textvariable=self.remaining_var).grid(row=1, column=1, sticky="e")
        ttk.Label(metrics, textvariable=self.speed_var).grid(row=2, column=0, columnspan=2, sticky="w")
        ttk.Label(metrics, textvariable=self.completion_var, justify="left").grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(3, 0)
        )

        controls = ttk.Frame(side)
        controls.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        controls.columnconfigure((0, 1), weight=1)
        self.start_button = ttk.Button(controls, text="Start", command=self._start_countdown, style="Primary.TButton")
        self.pause_button = ttk.Button(controls, text="Pause", command=self._pause)
        self.resume_button = ttk.Button(controls, text="Resume", command=self._resume)
        self.stop_button = ttk.Button(controls, text="Stop", command=self._stop)
        self.cancel_button = ttk.Button(controls, text="Cancel Countdown", command=self._cancel_countdown)
        self.start_button.grid(row=0, column=0, sticky="ew", padx=(0, 3), pady=3)
        self.pause_button.grid(row=0, column=1, sticky="ew", padx=(3, 0), pady=3)
        self.resume_button.grid(row=1, column=0, sticky="ew", padx=(0, 3), pady=3)
        self.stop_button.grid(row=1, column=1, sticky="ew", padx=(3, 0), pady=3)
        self.cancel_button.grid(row=2, column=0, columnspan=2, sticky="ew", pady=3)
        ttk.Label(controls, textvariable=self.emergency_display_var, font=("Segoe UI", 10, "bold"), anchor="center").grid(
            row=3, column=0, columnspan=2, sticky="ew", pady=(8, 0)
        )
        ttk.Label(
            controls,
            text="Mouse to upper-left corner also triggers PyAutoGUI fail-safe.",
            wraplength=340,
            anchor="center",
        ).grid(row=4, column=0, columnspan=2, sticky="ew", pady=(3, 0))

    def _build_advanced_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.columnconfigure(1, weight=1)
        timing = ttk.LabelFrame(parent, text="Typing Timing", style="Section.TLabelframe", padding=12)
        timing.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        punctuation = ttk.LabelFrame(parent, text="Additional Delays", style="Section.TLabelframe", padding=12)
        punctuation.grid(row=0, column=1, sticky="nsew", padx=(8, 0))

        fields_left = (
            ("Minimum character delay (s)", "minimum_delay"),
            ("Maximum character delay (s)", "maximum_delay"),
            ("Variation percent", "variation_percent"),
            ("Typing speed multiplier", "speed_multiplier"),
            ("Emergency hotkey", "emergency_hotkey"),
        )
        timing.columnconfigure(1, weight=1)
        for row, (label, key) in enumerate(fields_left):
            ttk.Label(timing, text=label).grid(row=row, column=0, sticky="w", pady=5)
            ttk.Entry(timing, textvariable=self.vars[key], width=16).grid(row=row, column=1, sticky="ew", pady=5)
        ttk.Checkbutton(timing, text="Random timing variation", variable=self.vars["random_variation"]).grid(
            row=len(fields_left), column=0, columnspan=2, sticky="w", pady=5
        )
        ttk.Label(timing, text="Human behavior").grid(row=len(fields_left) + 1, column=0, sticky="w", pady=5)
        ttk.Combobox(
            timing,
            textvariable=self.vars["humanization_mode"],
            values=("Off", "Subtle", "Natural", "Expressive"),
            state="readonly",
            width=16,
        ).grid(row=len(fields_left) + 1, column=1, sticky="ew", pady=5)
        ttk.Label(
            timing,
            text="Adds occasional thinking pauses and corrected adjacent-key typos.",
            wraplength=340,
        ).grid(row=len(fields_left) + 2, column=0, columnspan=2, sticky="w", pady=(2, 5))
        ttk.Checkbutton(
            timing,
            text="Notify when typing is complete",
            variable=self.vars["notify_on_completion"],
        ).grid(row=len(fields_left) + 3, column=0, columnspan=2, sticky="w", pady=5)

        fields_right = (
            ("Between words", "word_delay"),
            ("After commas", "comma_delay"),
            ("After periods", "period_delay"),
            ("After question marks", "question_delay"),
            ("After exclamation marks", "exclamation_delay"),
            ("After semicolons", "semicolon_delay"),
            ("After colons", "colon_delay"),
            ("After line breaks", "line_break_delay"),
        )
        punctuation.columnconfigure(1, weight=1)
        for row, (label, key) in enumerate(fields_right):
            ttk.Label(punctuation, text=label).grid(row=row, column=0, sticky="w", pady=5)
            ttk.Entry(punctuation, textvariable=self.vars[key], width=16).grid(row=row, column=1, sticky="ew", pady=5)
            ttk.Label(punctuation, text="seconds").grid(row=row, column=2, sticky="w", padx=(5, 0))

        presets = ttk.LabelFrame(parent, text="Presets and Persistence", style="Section.TLabelframe", padding=12)
        presets.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(16, 0))
        presets.columnconfigure(1, weight=1)
        ttk.Label(presets, text="Preset:").grid(row=0, column=0, sticky="w", pady=4)
        self.preset_combo = ttk.Combobox(
            presets, textvariable=self.vars["selected_preset"], state="readonly", values=self._preset_names()
        )
        self.preset_combo.grid(row=0, column=1, sticky="ew", pady=4)
        ttk.Button(presets, text="Apply", command=self._apply_preset).grid(row=0, column=2, padx=(8, 0), pady=4)
        ttk.Label(presets, text="Save current timing as:").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(presets, textvariable=self.custom_preset_name_var).grid(row=1, column=1, sticky="ew", pady=4)
        ttk.Button(presets, text="Save Preset", command=self._save_custom_preset).grid(
            row=1, column=2, padx=(8, 0), pady=4
        )
        ttk.Button(presets, text="Save Settings", command=self._save_settings_clicked).grid(
            row=2, column=0, columnspan=3, sticky="ew", pady=(10, 0)
        )
        ttk.Label(
            presets,
            text=("The source text is never stored. Target duration remains unchanged when applying a preset. "
                  "Dynamic pacing may scale configured delays so completion stays close to the target."),
            wraplength=800,
        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(10, 0))

    def _bind_updates(self) -> None:
        self.text_box.bind("<<Modified>>", self._on_text_modified)
        for variable in self.vars.values():
            variable.trace_add("write", lambda *_args: self._update_preview())
        self.vars["line_break_method"].trace_add("write", lambda *_args: self._update_custom_shortcut_state())
        self._update_custom_shortcut_state()

    def _on_text_modified(self, _event: tk.Event) -> None:
        if self.text_box.edit_modified():
            self.text_box.edit_modified(False)
            self._update_text_stats()

    def _get_text(self) -> str:
        # Tk always includes an implementation newline at end-1c; exclude only that,
        # preserving every newline actually entered by the user.
        return self.text_box.get("1.0", "end-1c")

    def _update_text_stats(self) -> None:
        text = self._get_text()
        characters = len(text)
        words = len(re.findall(r"\S+", text))
        lines = text.count("\n") + 1 if text else 0
        self.text_stats_var.set(f"Characters: {characters:,}    Words: {words:,}    Lines: {lines:,}")
        self._update_preview()

    def _update_preview(self) -> None:
        if self._suspend_preview or not hasattr(self, "text_box"):
            return
        text = self._get_text()
        try:
            minutes = float(self.vars["duration_minutes"].get())
            if not math.isfinite(minutes) or minutes <= 0:
                raise ValueError
            actions = max(1, len(build_actions(text)))
            words = len(re.findall(r"\S+", text))
            cpm = len(text) / minutes
            wpm = words / minutes
            average = minutes * 60.0 / actions
            self.preview_var.set(
                f"Characters: {len(text):,}    Words: {words:,}\n"
                f"Requested duration: {minutes:g} min\n"
                f"Estimated speed: {cpm:.1f} CPM  |  {wpm:.1f} WPM\n"
                f"Average interval: ~{average:.3f} seconds per action"
            )
            if text and minutes * 60.0 > len(build_actions(text)) * 59.0:
                self.override_var.set("Duration is too long to guarantee one keyboard action per active minute.")
            else:
                self.override_var.set(
                    "Dynamic pacing scales configured delays to meet the target; planned gaps are capped at 59 seconds."
                )
        except (ValueError, TypeError):
            self.preview_var.set("Enter a positive target duration to see the preview.")

    def _update_custom_shortcut_state(self) -> None:
        if not hasattr(self, "custom_shortcut_entry"):
            return
        state = "normal" if self.vars["line_break_method"].get() == "Custom key combination" else "disabled"
        self.custom_shortcut_entry.configure(state=state)

    def _capture_engine_settings(self) -> EngineSettings:
        try:
            duration_minutes = float(self.vars["duration_minutes"].get())
            values = {
                key: float(self.vars[key].get())
                for key in (
                    "minimum_delay", "maximum_delay", "variation_percent", "word_delay", "comma_delay",
                    "period_delay", "question_delay", "exclamation_delay", "semicolon_delay", "colon_delay",
                    "line_break_delay", "speed_multiplier",
                )
            }
        except ValueError as exc:
            raise TypingValidationError("Timing fields must contain valid numbers.") from exc
        return EngineSettings(
            duration_seconds=duration_minutes * 60.0,
            random_variation=bool(self.vars["random_variation"].get()),
            line_break_method=str(self.vars["line_break_method"].get()),
            custom_line_break=str(self.vars["custom_line_break"].get()),
            humanization_mode=str(self.vars["humanization_mode"].get()),
            **values,
        )

    def _capture_persisted_settings(self) -> dict[str, Any]:
        result = copy.deepcopy(self.settings)
        numeric_keys = (
            "duration_minutes", "minimum_delay", "maximum_delay", "variation_percent", "word_delay",
            "comma_delay", "period_delay", "question_delay", "exclamation_delay", "semicolon_delay",
            "colon_delay", "line_break_delay", "speed_multiplier",
        )
        for key in numeric_keys:
            result[key] = float(self.vars[key].get())
        result["countdown_seconds"] = int(self.vars["countdown_seconds"].get())
        result["random_variation"] = bool(self.vars["random_variation"].get())
        result["notify_on_completion"] = bool(self.vars["notify_on_completion"].get())
        for key in (
            "line_break_method", "custom_line_break", "emergency_hotkey", "selected_preset", "humanization_mode"
        ):
            result[key] = str(self.vars[key].get()).strip()
        result["window_geometry"] = self.root.geometry()
        return result

    def _validate_countdown_and_hotkey(self) -> int:
        try:
            countdown = int(self.vars["countdown_seconds"].get())
        except ValueError as exc:
            raise TypingValidationError("Start delay must be a whole number of seconds.") from exc
        if not 1 <= countdown <= 120:
            raise TypingValidationError("Start delay must be between 1 and 120 seconds.")
        try:
            normalize_hotkey(str(self.vars["emergency_hotkey"].get()))
        except ValueError as exc:
            raise TypingValidationError(str(exc)) from exc
        return countdown

    def _start_countdown(self) -> None:
        if self.engine.is_alive or self._state == "COUNTDOWN":
            return
        try:
            text = self._get_text()
            engine_settings = self._capture_engine_settings()
            validate_job(text, engine_settings)
            countdown = self._validate_countdown_and_hotkey()
            self._restart_hotkey(show_error=True)
            self.settings = self._capture_persisted_settings()
            save_settings(self.settings)
        except (TypingValidationError, ValueError, OSError) as exc:
            messagebox.showerror("Cannot Start", str(exc), parent=self.root)
            return

        self._pending_job = (text, engine_settings)
        self._countdown_deadline = time.monotonic() + countdown
        self._reset_progress(len(text), len(re.findall(r"\S+", text)), engine_settings.duration_seconds)
        self._set_state("COUNTDOWN")
        self._tick_countdown()

    def _tick_countdown(self) -> None:
        self._countdown_after_id = None
        if self._state != "COUNTDOWN" or self._pending_job is None:
            return
        remaining = self._countdown_deadline - time.monotonic()
        if remaining > 0:
            self.countdown_var.set(f"Typing starts in\n{max(1, math.ceil(remaining))}")
            self._countdown_after_id = self.root.after(80, self._tick_countdown)
            return

        text, engine_settings = self._pending_job
        self._pending_job = None
        self.countdown_var.set("Typing now")
        self._set_state("TYPING")
        try:
            self._active_run_id = self.engine.start(text, engine_settings)
        except Exception as exc:
            self._set_state("ERROR")
            self.countdown_var.set("Could not start")
            messagebox.showerror("Typing Error", str(exc), parent=self.root)

    def _cancel_countdown(self, stopped: bool = False) -> None:
        if self._state != "COUNTDOWN":
            return
        if self._countdown_after_id is not None:
            self.root.after_cancel(self._countdown_after_id)
            self._countdown_after_id = None
        self._pending_job = None
        self.countdown_var.set("Countdown cancelled")
        self._set_state("STOPPED" if stopped else "READY")

    def _pause(self) -> None:
        if self.engine.pause():
            self._set_state("PAUSED")
            self.countdown_var.set("Paused")

    def _resume(self) -> None:
        if self.engine.resume():
            self._set_state("TYPING")
            self.countdown_var.set("Typing now")

    def _stop(self) -> None:
        if self._state == "COUNTDOWN":
            self._cancel_countdown(stopped=True)
            return
        if self.engine.is_alive:
            self.engine.stop()
            self._set_state("STOPPING")
            self.countdown_var.set("Stopping...")

    def _emergency_stop(self) -> None:
        if self._state == "COUNTDOWN":
            self._cancel_countdown(stopped=True)
            self.engine.release_modifiers()
        elif self.engine.is_alive:
            self.engine.stop()
            self._set_state("STOPPING")
            self.countdown_var.set("Emergency stop triggered")
        else:
            self.engine.release_modifiers()
            self._set_state("STOPPED")
            self.countdown_var.set("Emergency stop triggered")

    def _set_state(self, state: str) -> None:
        self._state = state
        self.status_var.set(f"Status: {state}")
        enabled: dict[str, set[str]] = {
            "READY": {"start"},
            "COUNTDOWN": {"stop", "cancel"},
            "TYPING": {"pause", "stop"},
            "PAUSED": {"resume", "stop"},
            "STOPPING": set(),
            "STOPPED": {"start"},
            "COMPLETED": {"start"},
            "ERROR": {"start"},
        }
        active = enabled.get(state, set())
        for name, button in (
            ("start", self.start_button), ("pause", self.pause_button), ("resume", self.resume_button),
            ("stop", self.stop_button), ("cancel", self.cancel_button),
        ):
            button.configure(state="normal" if name in active else "disabled")

    def _reset_progress(self, total_chars: int, total_words: int, target_seconds: float) -> None:
        self.progress["value"] = 0
        self.progress_text_var.set(f"0 / {total_chars:,} characters (0.0%)")
        self.words_progress_var.set(f"Words: 0 / {total_words:,}")
        self.elapsed_var.set("Elapsed: 00:00")
        self.remaining_var.set(f"Remaining: {format_time(target_seconds)}")
        self.speed_var.set("Speed: 0 CPM  |  0 WPM")
        self.completion_var.set("")

    def _poll_events(self) -> None:
        if self._closing:
            return
        try:
            while True:
                event = self.events.get_nowait()
                if event.get("type") == "emergency":
                    self._emergency_stop()
                    continue
                if self._active_run_id is not None and event.get("run_id") != self._active_run_id:
                    continue
                self._handle_engine_event(event)
        except queue.Empty:
            pass
        self.root.after(40, self._poll_events)

    def _handle_engine_event(self, event: dict[str, Any]) -> None:
        event_type = event["type"]
        if event_type == "typing":
            return
        if event_type == "progress":
            total = max(1, int(event["total"]))
            position = int(event["position"])
            percentage = position / total * 100.0
            self.progress["value"] = percentage
            self.progress_text_var.set(f"{position:,} / {int(event['total']):,} characters ({percentage:.1f}%)")
            self.words_progress_var.set(
                f"Words: {int(event['words_completed']):,} / {int(event['total_words']):,}"
            )
            self.elapsed_var.set(f"Elapsed: {format_time(float(event['elapsed']))}")
            self.remaining_var.set(f"Remaining: {format_time(float(event['remaining']))}")
            self.speed_var.set(f"Speed: {float(event['cpm']):.0f} CPM  |  {float(event['wpm']):.1f} WPM")
        elif event_type == "stopped":
            self._set_state("STOPPED")
            self.countdown_var.set("Stopped")
        elif event_type == "completed":
            self.progress["value"] = 100
            self.progress_text_var.set(f"{int(event['total']):,} / {int(event['total']):,} characters (100.0%)")
            self.elapsed_var.set(f"Elapsed: {format_time(float(event['elapsed']))}")
            self.remaining_var.set("Remaining: 00:00")
            self.completion_var.set(
                f"Completed in: {format_time(float(event['elapsed']))}\n"
                f"Target: {format_time(float(event['target']))}  |  Difference: {format_time(float(event['difference']), signed=True)}"
            )
            self.countdown_var.set("Completed")
            self._set_state("COMPLETED")
            if bool(self.vars["notify_on_completion"].get()):
                messagebox.showinfo(
                    "Typing Complete",
                    f"Typing finished successfully in {format_time(float(event['elapsed']))}.",
                    parent=self.root,
                )
        elif event_type == "error":
            self._set_state("ERROR")
            self.countdown_var.set("Typing stopped safely")
            messagebox.showerror("Typing Stopped", str(event["message"]), parent=self.root)

    def _preset_names(self) -> tuple[str, ...]:
        custom = self.settings.get("custom_presets", {})
        custom_names = sorted(custom) if isinstance(custom, dict) else []
        return (*BUILT_IN_PRESETS.keys(), "Custom", *custom_names)

    def _apply_preset(self) -> None:
        name = str(self.vars["selected_preset"].get())
        values = preset_values(name, self.settings)
        if values is None:
            if name != "Custom":
                messagebox.showerror("Preset Error", "That preset is missing or invalid.", parent=self.root)
            return
        self._suspend_preview = True
        try:
            for key in PRESET_KEYS:
                if key in values and key in self.vars:
                    self.vars[key].set(values[key])
        finally:
            self._suspend_preview = False
        self._update_preview()

    def _save_custom_preset(self) -> None:
        name = self.custom_preset_name_var.get().strip()
        if not name:
            messagebox.showerror("Preset Error", "Enter a name for the custom preset.", parent=self.root)
            return
        if name in BUILT_IN_PRESETS or name == "Custom":
            messagebox.showerror("Preset Error", "Choose a name different from the built-in presets.", parent=self.root)
            return
        try:
            validate_engine_settings(self._capture_engine_settings())
            self._validate_countdown_and_hotkey()
            values: dict[str, Any] = {}
            for key in PRESET_KEYS:
                if key == "random_variation":
                    values[key] = bool(self.vars[key].get())
                elif key == "countdown_seconds":
                    values[key] = int(self.vars[key].get())
                elif key == "humanization_mode":
                    values[key] = str(self.vars[key].get())
                else:
                    values[key] = float(self.vars[key].get())
            self.settings.setdefault("custom_presets", {})[name] = values
            self.vars["selected_preset"].set(name)
            self.preset_combo.configure(values=self._preset_names())
            self.settings = self._capture_persisted_settings()
            save_settings(self.settings)
        except (ValueError, TypingValidationError, OSError) as exc:
            messagebox.showerror("Preset Error", str(exc), parent=self.root)
            return
        messagebox.showinfo("Preset Saved", f"Saved preset: {name}", parent=self.root)

    def _save_settings_clicked(self) -> None:
        try:
            validate_engine_settings(self._capture_engine_settings())
            self._validate_countdown_and_hotkey()
            self._restart_hotkey(show_error=True)
            self.settings = self._capture_persisted_settings()
            save_settings(self.settings)
        except (ValueError, TypingValidationError, OSError) as exc:
            messagebox.showerror("Settings Error", str(exc), parent=self.root)
            return
        messagebox.showinfo("Settings", "Settings saved. The source text was not stored.", parent=self.root)

    def _restart_hotkey(self, show_error: bool) -> None:
        value = str(self.vars["emergency_hotkey"].get()).strip()
        try:
            if value != self.hotkey.display_value:
                self.hotkey.start(value)
            self.emergency_display_var.set(f"Emergency Stop: {value}")
        except Exception as exc:
            if show_error:
                raise TypingValidationError(f"Could not register the emergency hotkey: {exc}") from exc
            self.emergency_display_var.set("Emergency Stop: unavailable (check dependencies/settings)")

    def _on_close(self) -> None:
        self._closing = True
        if self._countdown_after_id is not None:
            self.root.after_cancel(self._countdown_after_id)
            self._countdown_after_id = None
        self._pending_job = None
        try:
            self.settings = self._capture_persisted_settings()
            save_settings(self.settings)
        except (ValueError, OSError):
            pass
        self.hotkey.stop()
        self.engine.shutdown()
        self.root.destroy()
