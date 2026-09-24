"""Small wrapper around pynput's system-wide hotkey listener."""

from __future__ import annotations

from collections.abc import Callable

try:
    from pynput import keyboard
except ImportError:
    keyboard = None


ALIASES = {"control": "ctrl", "windows": "cmd", "win": "cmd", "escape": "esc"}
SPECIAL_KEYS = {
    "ctrl", "alt", "shift", "cmd", "enter", "tab", "space", "backspace", "delete", "insert",
    "home", "end", "pageup", "pagedown", "up", "down", "left", "right", "esc",
    *{f"f{i}" for i in range(1, 25)},
}
MODIFIERS = {"ctrl", "alt", "shift", "cmd"}


def normalize_hotkey(value: str) -> str:
    parts = [ALIASES.get(part.strip().lower(), part.strip().lower()) for part in value.split("+")]
    if not parts or any(not part for part in parts):
        raise ValueError("Enter an emergency hotkey such as F8 or Ctrl+Alt+S.")
    if len(parts) != len(set(parts)):
        raise ValueError("The emergency hotkey cannot repeat a key.")
    if any(len(part) != 1 and part not in SPECIAL_KEYS for part in parts):
        raise ValueError("The emergency hotkey contains an unsupported key.")
    if any(part not in MODIFIERS for part in parts[:-1]) or parts[-1] in MODIFIERS:
        raise ValueError("Use optional modifiers followed by one non-modifier key, such as Ctrl+Alt+S.")
    return "+".join(f"<{part}>" if part in SPECIAL_KEYS else part for part in parts)


class GlobalHotkeyMonitor:
    def __init__(self, callback: Callable[[], None]) -> None:
        if keyboard is None:
            raise RuntimeError("pynput is not installed. Run: pip install -r requirements.txt")
        self._callback = callback
        self._listener = None
        self._display_value = ""

    @property
    def display_value(self) -> str:
        return self._display_value

    def start(self, value: str) -> None:
        normalized = normalize_hotkey(value)
        self.stop()
        self._listener = keyboard.GlobalHotKeys({normalized: self._callback})
        self._listener.daemon = True
        self._listener.start()
        self._display_value = value.strip()

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
