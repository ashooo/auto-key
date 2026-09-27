from __future__ import annotations

import copy
import tkinter as tk
import unittest
from unittest import mock

import gui
from config import DEFAULT_SETTINGS


class FakeEngine:
    def __init__(self, callback):
        self.callback = callback
        self.is_alive = False
        self.started = 0

    def start(self, _text, _settings):
        self.started += 1
        self.is_alive = True
        return self.started

    def pause(self):
        return False

    def resume(self):
        return False

    def stop(self):
        self.is_alive = False

    def release_modifiers(self):
        pass

    def shutdown(self):
        self.is_alive = False


class FakeHotkey:
    def __init__(self, _callback):
        self.display_value = ""

    def start(self, value):
        self.display_value = value

    def stop(self):
        pass


class GuiSmokeTests(unittest.TestCase):
    def test_window_builds_and_cancel_countdown_sends_nothing(self):
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk display unavailable: {exc}")
        root.withdraw()
        with (
            mock.patch.object(gui, "TypingEngine", FakeEngine),
            mock.patch.object(gui, "GlobalHotkeyMonitor", FakeHotkey),
            mock.patch.object(gui, "load_settings", return_value=copy.deepcopy(DEFAULT_SETTINGS)),
            mock.patch.object(gui, "save_settings"),
        ):
            app = gui.TypingAutomationApp(root)
            root.update_idletasks()
            app.text_box.insert("1.0", "one\n\nTwo")
            app._update_text_stats()
            self.assertIn("Lines: 3", app.text_stats_var.get())
            app.vars["duration_minutes"].set("0.01")
            app.vars["countdown_seconds"].set("1")
            app._start_countdown()
            self.assertEqual(app._state, "COUNTDOWN")
            self.assertEqual(app.engine.started, 0)
            app._cancel_countdown()
            self.assertEqual(app._state, "READY")
            self.assertEqual(app.engine.started, 0)
            with mock.patch.object(gui.messagebox, "showinfo") as completion_notice:
                app._handle_engine_event(
                    {"type": "completed", "total": 8, "elapsed": 1.0, "target": 1.0, "difference": 0.0}
                )
                completion_notice.assert_called_once()
                self.assertEqual(app._state, "COMPLETED")
            app._closing = True
        root.destroy()


if __name__ == "__main__":
    unittest.main()
