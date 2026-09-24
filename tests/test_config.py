from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from config import DEFAULT_SETTINGS, load_settings, save_settings
from hotkey import normalize_hotkey


class ConfigTests(unittest.TestCase):
    def test_corrupt_or_partial_json_falls_back_safely(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            path.write_text("{not valid json", encoding="utf-8")
            self.assertEqual(load_settings(path), DEFAULT_SETTINGS)
            path.write_text('{"countdown_seconds": 30}', encoding="utf-8")
            loaded = load_settings(path)
            self.assertEqual(loaded["countdown_seconds"], 30)
            self.assertEqual(loaded["line_break_method"], DEFAULT_SETTINGS["line_break_method"])

    def test_saved_settings_never_gain_a_source_text_field(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            values = dict(DEFAULT_SETTINGS)
            values["source_text"] = "must not persist"
            save_settings(values, path)
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotIn("source_text", saved)

    def test_emergency_hotkey_requires_a_non_modifier_final_key(self):
        self.assertEqual(normalize_hotkey("Ctrl+Alt+S"), "<ctrl>+<alt>+s")
        self.assertEqual(normalize_hotkey("F8"), "<f8>")
        with self.assertRaises(ValueError):
            normalize_hotkey("Ctrl+Alt")


if __name__ == "__main__":
    unittest.main()
