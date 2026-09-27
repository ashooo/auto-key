"""Defaults and resilient JSON persistence for Auto Key."""

from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path
from typing import Any


DEFAULT_SETTINGS: dict[str, Any] = {
    "duration_minutes": 10.0,
    "countdown_seconds": 25,
    "line_break_method": "Shift + Enter",
    "custom_line_break": "Ctrl+Enter",
    "emergency_hotkey": "F8",
    "minimum_delay": 0.04,
    "maximum_delay": 0.18,
    "random_variation": True,
    "variation_percent": 12.0,
    "humanization_mode": "Natural",
    "notify_on_completion": True,
    "word_delay": 0.04,
    "comma_delay": 0.12,
    "period_delay": 0.22,
    "question_delay": 0.22,
    "exclamation_delay": 0.22,
    "semicolon_delay": 0.16,
    "colon_delay": 0.14,
    "line_break_delay": 0.30,
    "speed_multiplier": 1.0,
    "selected_preset": "Normal",
    "custom_presets": {},
    "window_geometry": "1050x790",
}


BUILT_IN_PRESETS: dict[str, dict[str, Any]] = {
    "Slow": {
        "minimum_delay": 0.10,
        "maximum_delay": 0.32,
        "random_variation": True,
        "variation_percent": 18.0,
        "humanization_mode": "Expressive",
        "word_delay": 0.08,
        "comma_delay": 0.22,
        "period_delay": 0.38,
        "question_delay": 0.38,
        "exclamation_delay": 0.38,
        "semicolon_delay": 0.26,
        "colon_delay": 0.24,
        "line_break_delay": 0.50,
        "countdown_seconds": 25,
        "speed_multiplier": 0.8,
    },
    "Normal": {
        "minimum_delay": 0.04,
        "maximum_delay": 0.18,
        "random_variation": True,
        "variation_percent": 12.0,
        "humanization_mode": "Natural",
        "word_delay": 0.04,
        "comma_delay": 0.12,
        "period_delay": 0.22,
        "question_delay": 0.22,
        "exclamation_delay": 0.22,
        "semicolon_delay": 0.16,
        "colon_delay": 0.14,
        "line_break_delay": 0.30,
        "countdown_seconds": 25,
        "speed_multiplier": 1.0,
    },
    "Fast": {
        "minimum_delay": 0.01,
        "maximum_delay": 0.08,
        "random_variation": True,
        "variation_percent": 8.0,
        "humanization_mode": "Subtle",
        "word_delay": 0.015,
        "comma_delay": 0.06,
        "period_delay": 0.10,
        "question_delay": 0.10,
        "exclamation_delay": 0.10,
        "semicolon_delay": 0.08,
        "colon_delay": 0.07,
        "line_break_delay": 0.15,
        "countdown_seconds": 10,
        "speed_multiplier": 1.35,
    },
}


PRESET_KEYS = tuple(BUILT_IN_PRESETS["Normal"].keys())


def settings_path() -> Path:
    """Keep settings beside the source/executable, as documented."""
    base = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    return base / "settings.json"


def load_settings(path: Path | None = None) -> dict[str, Any]:
    result = copy.deepcopy(DEFAULT_SETTINGS)
    target = path or settings_path()
    try:
        loaded = json.loads(target.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            for key in DEFAULT_SETTINGS:
                if key in loaded and isinstance(loaded[key], type(DEFAULT_SETTINGS[key])):
                    result[key] = loaded[key]
                elif key in loaded and isinstance(DEFAULT_SETTINGS[key], float) and isinstance(loaded[key], (int, float)):
                    result[key] = float(loaded[key])
    except (OSError, UnicodeError, json.JSONDecodeError):
        pass
    return result


def save_settings(settings: dict[str, Any], path: Path | None = None) -> None:
    target = path or settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {key: settings.get(key, copy.deepcopy(default)) for key, default in DEFAULT_SETTINGS.items()}
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, target)


def preset_values(name: str, settings: dict[str, Any]) -> dict[str, Any] | None:
    if name in BUILT_IN_PRESETS:
        return copy.deepcopy(BUILT_IN_PRESETS[name])
    custom = settings.get("custom_presets", {})
    value = custom.get(name) if isinstance(custom, dict) else None
    return copy.deepcopy(value) if isinstance(value, dict) else None
