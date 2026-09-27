"""Responsive, dynamically corrected keyboard typing engine."""

from __future__ import annotations

import bisect
import ctypes
import math
import random
import re
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable

try:
    import pyautogui as _pyautogui
except ImportError:  # Lets the pure scheduling tests run before dependencies are installed.
    _pyautogui = None

try:
    import pyperclip as _pyperclip
except ImportError:  # Unicode fallback reports a useful error if this dependency is absent.
    _pyperclip = None


# Leaving one second for OS/input overhead makes the user-facing guarantee robust.
MAX_ACTION_INTERVAL_SECONDS = 59.0
MIN_PRACTICAL_INTERVAL_SECONDS = 0.001
MODIFIER_ALIASES = {"control": "ctrl", "windows": "win", "command": "win"}
MODIFIERS = {"ctrl", "alt", "shift", "win"}
# PyAutoGUI's text writer can treat these as dead keys on international layouts.
# On Windows they use exact Unicode input first, with an ordinary key fallback.
LAYOUT_SENSITIVE_KEYS = {"'", '"', "`"}
HUMANIZATION_PROFILES: dict[str, dict[str, float]] = {
    "Off": {"pause_chance": 0.0, "pause_min": 0.0, "pause_max": 0.0, "typo_chance": 0.0},
    "Subtle": {"pause_chance": 0.07, "pause_min": 0.08, "pause_max": 0.25, "typo_chance": 0.004},
    "Natural": {"pause_chance": 0.16, "pause_min": 0.12, "pause_max": 0.50, "typo_chance": 0.010},
    "Expressive": {"pause_chance": 0.25, "pause_min": 0.18, "pause_max": 0.80, "typo_chance": 0.020},
}
KEYBOARD_NEIGHBORS = {
    "a": "qwsz", "b": "vghn", "c": "xdfv", "d": "serfcx", "e": "wsdr", "f": "drtgvc",
    "g": "ftyhbv", "h": "gyujnb", "i": "ujko", "j": "huikmn", "k": "jiolm", "l": "kop",
    "m": "njk", "n": "bhjm", "o": "iklp", "p": "ol", "q": "wa", "r": "edft",
    "s": "awedxz", "t": "rfgy", "u": "yhji", "v": "cfgb", "w": "qase", "x": "zsdc",
    "y": "tghu", "z": "asx",
}


@dataclass(frozen=True)
class TypingAction:
    value: str
    source_end: int
    newline: bool = False


@dataclass(frozen=True)
class EngineSettings:
    duration_seconds: float
    minimum_delay: float
    maximum_delay: float
    random_variation: bool
    variation_percent: float
    humanization_mode: str
    word_delay: float
    comma_delay: float
    period_delay: float
    question_delay: float
    exclamation_delay: float
    semicolon_delay: float
    colon_delay: float
    line_break_delay: float
    speed_multiplier: float
    line_break_method: str
    custom_line_break: str


class TypingValidationError(ValueError):
    pass


def build_actions(text: str) -> list[TypingAction]:
    """Create input actions while treating CRLF as one physical newline action."""
    actions: list[TypingAction] = []
    index = 0
    while index < len(text):
        character = text[index]
        if character == "\r" and index + 1 < len(text) and text[index + 1] == "\n":
            actions.append(TypingAction("\n", index + 2, True))
            index += 2
        elif character in {"\r", "\n"}:
            actions.append(TypingAction("\n", index + 1, True))
            index += 1
        else:
            actions.append(TypingAction(character, index + 1, False))
            index += 1
    return actions


def parse_shortcut(shortcut: str) -> tuple[list[str], str]:
    tokens = [MODIFIER_ALIASES.get(part.strip().lower(), part.strip().lower()) for part in shortcut.split("+")]
    if not tokens or any(not token for token in tokens):
        raise TypingValidationError("Enter a shortcut such as Ctrl+Enter.")
    if len(tokens) != len(set(tokens)):
        raise TypingValidationError("A shortcut cannot contain the same key twice.")
    modifiers = tokens[:-1]
    final_key = tokens[-1]
    if any(key not in MODIFIERS for key in modifiers) or final_key in MODIFIERS:
        raise TypingValidationError("A shortcut must have optional modifiers followed by one non-modifier key.")
    if len(final_key) != 1 and final_key not in {
        "enter", "tab", "space", "backspace", "delete", "insert", "home", "end", "pageup", "pagedown",
        "up", "down", "left", "right", "esc", "escape", *{f"f{i}" for i in range(1, 25)},
    }:
        raise TypingValidationError(f"Unsupported shortcut key: {final_key}")
    return modifiers, final_key


def validate_engine_settings(settings: EngineSettings) -> None:
    """Validate timing fields independently of any particular source text."""
    numeric = {
        "duration": settings.duration_seconds,
        "minimum delay": settings.minimum_delay,
        "maximum delay": settings.maximum_delay,
        "variation": settings.variation_percent,
        "word delay": settings.word_delay,
        "comma delay": settings.comma_delay,
        "period delay": settings.period_delay,
        "question-mark delay": settings.question_delay,
        "exclamation-mark delay": settings.exclamation_delay,
        "semicolon delay": settings.semicolon_delay,
        "colon delay": settings.colon_delay,
        "line-break delay": settings.line_break_delay,
        "speed multiplier": settings.speed_multiplier,
    }
    if any(not math.isfinite(value) for value in numeric.values()):
        raise TypingValidationError("All timing values must be finite numbers.")
    if settings.duration_seconds <= 0:
        raise TypingValidationError("Target duration must be greater than zero.")
    if any(value < 0 for key, value in numeric.items() if key not in {"duration", "speed multiplier"}):
        raise TypingValidationError("Delay and variation values cannot be negative.")
    if settings.minimum_delay > settings.maximum_delay:
        raise TypingValidationError("Minimum delay cannot be greater than maximum delay.")
    if settings.speed_multiplier <= 0:
        raise TypingValidationError("Typing speed multiplier must be greater than zero.")
    if settings.variation_percent > 100:
        raise TypingValidationError("Random variation must be between 0 and 100 percent.")
    if settings.humanization_mode not in HUMANIZATION_PROFILES:
        raise TypingValidationError("Choose a valid human behavior level.")
    if settings.line_break_method not in {"Shift + Enter", "Enter", "Custom key combination"}:
        raise TypingValidationError("Choose a valid line-break method.")
    if settings.line_break_method == "Custom key combination":
        parse_shortcut(settings.custom_line_break)


def validate_job(text: str, settings: EngineSettings) -> list[TypingAction]:
    actions = build_actions(text)
    if not actions:
        raise TypingValidationError("Enter some text before starting.")
    validate_engine_settings(settings)

    # There must be enough source actions to cover the requested active time without
    # ever deliberately going a full minute without a keyboard action.
    if settings.duration_seconds > len(actions) * MAX_ACTION_INTERVAL_SECONDS:
        maximum_minutes = len(actions) * MAX_ACTION_INTERVAL_SECONDS / 60.0
        raise TypingValidationError(
            "This text has too few keyboard actions for that duration. "
            f"To guarantee at least one action per minute, use {maximum_minutes:.2f} minutes or less, "
            "or provide more text."
        )
    if settings.duration_seconds < len(actions) * MIN_PRACTICAL_INTERVAL_SECONDS:
        minimum_seconds = len(actions) * MIN_PRACTICAL_INTERVAL_SECONDS
        raise TypingValidationError(
            f"That duration is too short to send {len(actions):,} actions reliably. "
            f"Use at least {minimum_seconds:.2f} seconds."
        )
    return actions


def calculate_next_delay(
    remaining_budget: float,
    remaining_actions: int,
    current_weight: float,
    suffix_weight: float,
    average_send_time: float,
) -> float:
    """Allocate the next delay while retaining enough capacity for later actions.

    The lower bound matters when a low-weight action appears before high-weight
    punctuation. Without it, later 59-second caps could make the target duration
    unreachable even though the job as a whole passed validation.
    """
    delay_budget = max(0.0, remaining_budget - average_send_time * remaining_actions)
    weighted_delay = delay_budget * (current_weight / suffix_weight)
    later_capacity = max(0, remaining_actions - 1) * MAX_ACTION_INTERVAL_SECONDS
    minimum_needed_now = max(0.0, delay_budget - later_capacity)
    return min(MAX_ACTION_INTERVAL_SECONDS, max(weighted_delay, minimum_needed_now))


def build_timing_weights(actions: Iterable[TypingAction], settings: EngineSettings, rng: random.Random) -> list[float]:
    extras = {
        ",": settings.comma_delay,
        ".": settings.period_delay,
        "?": settings.question_delay,
        "!": settings.exclamation_delay,
        ";": settings.semicolon_delay,
        ":": settings.colon_delay,
    }
    midpoint = (settings.minimum_delay + settings.maximum_delay) / 2.0
    variation = settings.variation_percent / 100.0 if settings.random_variation else 0.0
    humanization = HUMANIZATION_PROFILES[settings.humanization_mode]
    weights: list[float] = []
    previous: TypingAction | None = None
    # Human typing tends to arrive in short fast and slow runs. Retaining most
    # of the preceding drift produces that cadence while a smaller independent
    # jitter prevents the runs from sounding mechanical.
    speed_drift = 0.0
    for action in actions:
        base = midpoint
        if variation:
            speed_drift = speed_drift * 0.78 + rng.uniform(-variation, variation) * 0.22
            jitter = rng.uniform(-variation * 0.35, variation * 0.35)
            relative_change = min(variation, max(-variation, speed_drift + jitter))
            base *= 1.0 + relative_change
        base = min(settings.maximum_delay, max(settings.minimum_delay, base))
        # The multiplier changes the relative amount devoted to ordinary characters;
        # final duration remains authoritative and punctuation pauses retain their shape.
        weight = base / settings.speed_multiplier
        # Each weight is a delay *before* this action. Apply contextual delays from
        # the preceding source action so commas and line breaks really pause after
        # they have been sent, never before.
        if previous is not None:
            if previous.newline:
                pause = settings.line_break_delay
                if variation:
                    pause *= rng.uniform(0.88, 1.20)
                weight += pause
            else:
                pause = extras.get(previous.value, 0.0)
                if variation and pause:
                    pause *= rng.uniform(0.82, 1.24)
                weight += pause
                if previous.value.isspace():
                    word_pause = settings.word_delay
                    if variation:
                        word_pause *= rng.uniform(0.70, 1.38)
                    # Thinking pauses happen at word boundaries and are planned
                    # into the relative timing weights, so the requested finish
                    # time remains authoritative.
                    if humanization["pause_chance"] and rng.random() < humanization["pause_chance"]:
                        word_pause += rng.uniform(humanization["pause_min"], humanization["pause_max"])
                    weight += word_pause
        weights.append(max(weight, 0.000_001))
        previous = action
    return weights


class TypingEngine:
    def __init__(
        self,
        event_callback: Callable[[dict[str, Any]], None],
        keyboard_backend: Any | None = None,
        random_source: random.Random | None = None,
    ) -> None:
        self._callback = event_callback
        self._keyboard = keyboard_backend if keyboard_backend is not None else _pyautogui
        if self._keyboard is None:
            raise RuntimeError("PyAutoGUI is not installed. Run: pip install -r requirements.txt")
        self._rng = random_source or random.Random()
        self._lock = threading.RLock()
        self._input_lock = threading.RLock()
        self._run_event = threading.Event()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._start_time = 0.0
        self._paused_total = 0.0
        self._pause_started: float | None = None
        self._run_id = 0
        self._held_modifiers: set[str] = set()
        self._last_typo_source_end = -100

        # Keep the mouse-corner fail-safe active and remove PyAutoGUI's implicit pause;
        # all pacing is performed by the interruptible scheduler below.
        self._keyboard.FAILSAFE = True
        self._keyboard.PAUSE = 0

    @property
    def is_alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self, text: str, settings: EngineSettings) -> int:
        actions = validate_job(text, settings)
        with self._lock:
            if self.is_alive:
                raise RuntimeError("A typing job is already running.")
            self._run_id += 1
            run_id = self._run_id
            self._stop_event.clear()
            self._run_event.set()
            self._start_time = time.monotonic()
            self._paused_total = 0.0
            self._pause_started = None
            self._last_typo_source_end = -100
            self._thread = threading.Thread(
                target=self._worker,
                args=(run_id, text, actions, settings),
                name="typing-engine",
                daemon=True,
            )
            self._thread.start()
            return run_id

    def pause(self) -> bool:
        with self._lock:
            if not self.is_alive or not self._run_event.is_set() or self._stop_event.is_set():
                return False
            self._pause_started = time.monotonic()
            self._run_event.clear()
        # If an atomic shortcut is already being sent, let it finish correctly
        # before releasing keys. A later action rechecks the pause flag while
        # holding this same input lock and therefore cannot slip through.
        self.release_modifiers()
        return True

    def resume(self) -> bool:
        with self._lock:
            if not self.is_alive or self._run_event.is_set() or self._stop_event.is_set():
                return False
            if self._pause_started is not None:
                self._paused_total += time.monotonic() - self._pause_started
                self._pause_started = None
            self._run_event.set()
            return True

    def stop(self) -> None:
        self._stop_event.set()
        self._run_event.set()  # Wake a paused worker so it can exit.
        self.release_modifiers()

    def shutdown(self, timeout: float = 1.5) -> None:
        self.stop()
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout)
        self.release_modifiers()

    def active_elapsed(self) -> float:
        with self._lock:
            if not self._start_time:
                return 0.0
            now = time.monotonic()
            current_pause = now - self._pause_started if self._pause_started is not None else 0.0
            return max(0.0, now - self._start_time - self._paused_total - current_pause)

    def release_modifiers(self) -> None:
        # Release the common modifiers even if our own tracking was interrupted.
        with self._input_lock:
            for key in ("shift", "ctrl", "alt", "win"):
                try:
                    self._keyboard.keyUp(key)
                except Exception:
                    pass
            with self._lock:
                self._held_modifiers.clear()

    def _emit(self, run_id: int, event_type: str, **payload: Any) -> None:
        self._callback({"run_id": run_id, "type": event_type, **payload})

    def _wait_active(self, duration: float) -> bool:
        target = self.active_elapsed() + max(0.0, duration)
        while not self._stop_event.is_set():
            if not self._run_event.is_set():
                self._run_event.wait(0.05)
                continue
            remaining = target - self.active_elapsed()
            if remaining <= 0:
                return True
            self._stop_event.wait(min(0.03, remaining))
        return False

    def _worker(
        self,
        run_id: int,
        text: str,
        actions: list[TypingAction],
        settings: EngineSettings,
    ) -> None:
        weights = build_timing_weights(actions, settings, self._rng)
        suffix = [0.0] * len(weights)
        running = 0.0
        for index in range(len(weights) - 1, -1, -1):
            running += weights[index]
            suffix[index] = running
        word_ends = [match.end() for match in re.finditer(r"\S+", text)]
        average_send_time = 0.002
        last_progress_update = 0.0
        position = 0

        try:
            self._emit(run_id, "typing")
            for index, action in enumerate(actions):
                if self._stop_event.is_set():
                    break

                remaining_actions = len(actions) - index
                remaining_budget = max(0.0, settings.duration_seconds - self.active_elapsed())
                planned_delay = calculate_next_delay(
                    remaining_budget,
                    remaining_actions,
                    weights[index],
                    suffix[index],
                    average_send_time,
                )

                if not self._wait_active(planned_delay):
                    break
                correction_reserve = 0.30
                budget_after_wait = max(0.0, settings.duration_seconds - self.active_elapsed())
                later_actions = remaining_actions - 1
                allow_correction = (
                    budget_after_wait
                    > correction_reserve + later_actions * MIN_PRACTICAL_INTERVAL_SECONDS
                )
                send_time = self._send_when_running(action, settings, allow_correction)
                if send_time is None:
                    break
                average_send_time = average_send_time * 0.9 + send_time * 0.1
                position = action.source_end

                now = time.monotonic()
                if now - last_progress_update >= 0.08 or index == len(actions) - 1:
                    elapsed = self.active_elapsed()
                    words_completed = bisect.bisect_right(word_ends, position)
                    self._emit(
                        run_id,
                        "progress",
                        position=position,
                        total=len(text),
                        words_completed=words_completed,
                        total_words=len(word_ends),
                        elapsed=elapsed,
                        remaining=max(0.0, settings.duration_seconds - elapsed),
                        cpm=(position / elapsed * 60.0) if elapsed > 0 else 0.0,
                        wpm=(words_completed / elapsed * 60.0) if elapsed > 0 else 0.0,
                    )
                    last_progress_update = now

            elapsed = self.active_elapsed()
            if self._stop_event.is_set():
                self._emit(run_id, "stopped", position=position, elapsed=elapsed)
            else:
                self._emit(
                    run_id,
                    "completed",
                    position=len(text),
                    total=len(text),
                    elapsed=elapsed,
                    target=settings.duration_seconds,
                    difference=elapsed - settings.duration_seconds,
                )
        except Exception as exc:
            fail_safe_type = getattr(self._keyboard, "FailSafeException", ())
            if fail_safe_type and isinstance(exc, fail_safe_type):
                message = "PyAutoGUI fail-safe triggered. Typing stopped safely."
            else:
                message = f"Typing stopped because of an error: {exc}"
            self._emit(run_id, "error", message=message, position=position, elapsed=self.active_elapsed())
        finally:
            self.release_modifiers()
            with self._lock:
                if self._pause_started is not None:
                    self._paused_total += time.monotonic() - self._pause_started
                    self._pause_started = None

    def _send_when_running(
        self,
        action: TypingAction,
        settings: EngineSettings,
        allow_correction: bool = True,
    ) -> float | None:
        """Send exactly one atomic action after a lock-protected state recheck."""
        while not self._stop_event.is_set():
            if not self._run_event.is_set():
                self._run_event.wait(0.03)
                continue
            with self._input_lock:
                if self._stop_event.is_set():
                    return None
                if not self._run_event.is_set():
                    continue
                sent_at = time.monotonic()
                self._send_action(action, settings, allow_correction)
                return time.monotonic() - sent_at
        return None

    def _send_action(
        self,
        action: TypingAction,
        settings: EngineSettings,
        allow_correction: bool = True,
    ) -> None:
        if action.newline:
            if settings.line_break_method == "Enter":
                self._keyboard.press("enter")
            elif settings.line_break_method == "Shift + Enter":
                self._send_shortcut(["shift"], "enter")
            else:
                modifiers, final_key = parse_shortcut(settings.custom_line_break)
                self._send_shortcut(modifiers, final_key)
            return

        if allow_correction:
            self._maybe_send_corrected_typo(action, settings)

        if action.value in LAYOUT_SENSITIVE_KEYS:
            if sys.platform == "win32":
                try:
                    self._send_windows_unicode(action.value)
                    return
                except Exception:
                    # Some elevated or custom controls reject Unicode events.
                    # The regular ASCII key remains a useful second route.
                    self._keyboard.press(action.value)
                    return
            self._keyboard.press(action.value)
            return

        if ord(action.value) > 127:
            self._send_unicode_with_fallback(action.value)
            return

        try:
            self._keyboard.write(action.value, interval=0)
        except Exception as exc:
            fail_safe_type = getattr(self._keyboard, "FailSafeException", ())
            if fail_safe_type and isinstance(exc, fail_safe_type):
                raise
            # If the normal writer rejects a control/symbol, Windows Unicode input is
            # more exact than dropping or replacing the character.
            if sys.platform == "win32":
                self._send_unicode_with_fallback(action.value)
            else:
                raise

    def _maybe_send_corrected_typo(self, action: TypingAction, settings: EngineSettings) -> None:
        profile = HUMANIZATION_PROFILES[settings.humanization_mode]
        character = action.value
        lower = character.lower()
        if (
            profile["typo_chance"] <= 0
            or lower not in KEYBOARD_NEIGHBORS
            or action.source_end - self._last_typo_source_end < 8
            or self._rng.random() >= profile["typo_chance"]
        ):
            return

        wrong = self._rng.choice(KEYBOARD_NEIGHBORS[lower])
        if character.isupper():
            wrong = wrong.upper()
        self._keyboard.write(wrong, interval=0)
        time.sleep(self._rng.uniform(0.05, 0.13))
        self._keyboard.press("backspace")
        time.sleep(self._rng.uniform(0.04, 0.10))
        self._last_typo_source_end = action.source_end

    def _send_unicode_with_fallback(self, text: str) -> None:
        """Send exact Unicode, pasting only when Windows rejects input events."""
        try:
            self._send_windows_unicode(text)
            return
        except Exception as unicode_error:
            if _pyperclip is None:
                raise RuntimeError(
                    f"Could not send Unicode character {text!r}; install pyperclip to enable the paste fallback."
                ) from unicode_error

            previous_text: str | None = None
            try:
                previous_text = _pyperclip.paste()
                _pyperclip.copy(text)
                self._send_shortcut(["ctrl"], "v")
                # Let the focused application consume WM_PASTE before restoring
                # the clipboard. This delay is measured by the pacing scheduler.
                time.sleep(0.04)
            except Exception as paste_error:
                fail_safe_type = getattr(self._keyboard, "FailSafeException", ())
                if fail_safe_type and isinstance(paste_error, fail_safe_type):
                    raise
                raise RuntimeError(f"Could not send Unicode character {text!r}.") from paste_error
            finally:
                if previous_text is not None:
                    try:
                        _pyperclip.copy(previous_text)
                    except Exception:
                        pass

    def _send_shortcut(self, modifiers: list[str], final_key: str) -> None:
        pressed: list[str] = []
        try:
            for modifier in modifiers:
                self._keyboard.keyDown(modifier)
                pressed.append(modifier)
                with self._lock:
                    self._held_modifiers.add(modifier)
            self._keyboard.press(final_key)
        finally:
            for modifier in reversed(pressed):
                try:
                    self._keyboard.keyUp(modifier)
                finally:
                    with self._lock:
                        self._held_modifiers.discard(modifier)

    @staticmethod
    def _send_windows_unicode(text: str) -> None:
        if sys.platform != "win32":
            raise RuntimeError(f"Unicode character {text!r} is not supported by PyAutoGUI on this platform.")

        # KEYEVENTF_UNICODE accepts UTF-16 code units through the scan-code field.
        # Sending both down and up events works in standard Windows edit controls and
        # modern browser text fields without using the clipboard.
        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [
                ("wVk", ctypes.c_ushort),
                ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.c_void_p),
            ]

        class INPUT_UNION(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT)]

        class INPUT(ctypes.Structure):
            _fields_ = [("type", ctypes.c_ulong), ("union", INPUT_UNION)]

        units = text.encode("utf-16-le")
        for offset in range(0, len(units), 2):
            unit = int.from_bytes(units[offset : offset + 2], "little")
            down = INPUT(1, INPUT_UNION(ki=KEYBDINPUT(0, unit, 0x0004, 0, None)))
            up = INPUT(1, INPUT_UNION(ki=KEYBDINPUT(0, unit, 0x0004 | 0x0002, 0, None)))
            sent = ctypes.windll.user32.SendInput(2, (INPUT * 2)(down, up), ctypes.sizeof(INPUT))
            if sent != 2:
                raise OSError("Windows could not send a Unicode keyboard event.")
