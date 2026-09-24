from __future__ import annotations

import queue
import random
import time
import unittest
from unittest import mock

from typing_engine import (
    MAX_ACTION_INTERVAL_SECONDS,
    EngineSettings,
    TypingAction,
    TypingEngine,
    TypingValidationError,
    build_actions,
    build_timing_weights,
    calculate_next_delay,
    parse_shortcut,
    validate_job,
)


def settings(duration: float = 0.12, **overrides):
    values = dict(
        duration_seconds=duration,
        minimum_delay=0.01,
        maximum_delay=0.03,
        random_variation=True,
        variation_percent=10.0,
        humanization_mode="Off",
        word_delay=0.01,
        comma_delay=0.02,
        period_delay=0.03,
        question_delay=0.03,
        exclamation_delay=0.03,
        semicolon_delay=0.02,
        colon_delay=0.02,
        line_break_delay=0.04,
        speed_multiplier=1.0,
        line_break_method="Shift + Enter",
        custom_line_break="Ctrl+Enter",
    )
    values.update(overrides)
    return EngineSettings(**values)


class FakeKeyboard:
    class FailSafeException(Exception):
        pass

    FAILSAFE = False
    PAUSE = 0

    def __init__(self):
        self.events = []

    def write(self, value, interval=0):
        self.events.append(("write", value))

    def press(self, value):
        self.events.append(("press", value))

    def keyDown(self, value):
        self.events.append(("down", value))

    def keyUp(self, value):
        self.events.append(("up", value))


class FailSafeKeyboard(FakeKeyboard):
    def write(self, value, interval=0):
        raise self.FailSafeException("corner reached")


class FakeClipboard:
    def __init__(self, value="previous clipboard"):
        self.value = value
        self.copies = []

    def paste(self):
        return self.value

    def copy(self, value):
        self.value = value
        self.copies.append(value)


class PlanningTests(unittest.TestCase):
    def test_crlf_and_blank_lines_become_one_action_per_newline(self):
        actions = build_actions("a\r\n\r\nb")
        self.assertEqual([a.value for a in actions], ["a", "\n", "\n", "b"])
        self.assertEqual(actions[-1].source_end, 6)

    def test_shortcut_validation(self):
        self.assertEqual(parse_shortcut("Control+Shift+Enter"), (["ctrl", "shift"], "enter"))
        with self.assertRaises(TypingValidationError):
            parse_shortcut("Ctrl+Alt")

    def test_rejects_duration_that_would_leave_a_full_minute_idle(self):
        with self.assertRaisesRegex(TypingValidationError, "at least one action per minute"):
            validate_job("x", settings(duration=MAX_ACTION_INTERVAL_SECONDS + 0.01))

    def test_invalid_delay_range_and_impossibly_fast_job_are_rejected(self):
        with self.assertRaisesRegex(TypingValidationError, "Minimum delay"):
            validate_job("abc", settings(minimum_delay=0.2, maximum_delay=0.1))
        with self.assertRaisesRegex(TypingValidationError, "too short"):
            validate_job("x" * 1000, settings(duration=0.5))

    def test_random_weight_base_remains_inside_delay_bounds(self):
        cfg = settings(
            duration=1.0,
            minimum_delay=0.1,
            maximum_delay=0.2,
            variation_percent=100.0,
            word_delay=0,
            comma_delay=0,
            period_delay=0,
            question_delay=0,
            exclamation_delay=0,
            semicolon_delay=0,
            colon_delay=0,
            line_break_delay=0,
        )
        weights = build_timing_weights(build_actions("abcdef"), cfg, random.Random(5))
        self.assertTrue(all(0.1 <= value <= 0.2 for value in weights))

    def test_humanized_variation_is_seeded_and_varies_word_pauses(self):
        cfg = settings(
            duration=2.0,
            minimum_delay=0.1,
            maximum_delay=0.2,
            variation_percent=40.0,
            word_delay=0.2,
            comma_delay=0,
            period_delay=0,
            question_delay=0,
            exclamation_delay=0,
            semicolon_delay=0,
            colon_delay=0,
            line_break_delay=0,
            humanization_mode="Natural",
        )
        actions = build_actions("one two three four")
        first = build_timing_weights(actions, cfg, random.Random(8))
        repeated = build_timing_weights(actions, cfg, random.Random(8))
        different = build_timing_weights(actions, cfg, random.Random(9))
        self.assertEqual(first, repeated)
        self.assertNotEqual(first, different)
        word_starts = [index for index in range(1, len(actions)) if actions[index - 1].value == " "]
        self.assertGreater(len({round(first[index], 5) for index in word_starts}), 1)

    def test_punctuation_delay_is_applied_after_punctuation(self):
        cfg = settings(
            duration=1.0,
            minimum_delay=0.1,
            maximum_delay=0.1,
            random_variation=False,
            comma_delay=0.4,
        )
        weights = build_timing_weights(build_actions("a,b"), cfg, random.Random(1))
        self.assertAlmostEqual(weights[1], 0.1)
        self.assertAlmostEqual(weights[2], 0.5)

    def test_delay_reserves_enough_capacity_for_later_minute_caps(self):
        delay = calculate_next_delay(
            remaining_budget=117.5,
            remaining_actions=2,
            current_weight=0.001,
            suffix_weight=100.001,
            average_send_time=0.0,
        )
        self.assertAlmostEqual(delay, 58.5)


class EngineTests(unittest.TestCase):
    def wait_for(self, events, wanted, timeout=2.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            event = events.get(timeout=max(0.01, deadline - time.monotonic()))
            if event["type"] == wanted:
                return event
        self.fail(f"Did not receive {wanted}")

    def test_exact_text_and_shift_enter_without_extra_enter(self):
        backend = FakeKeyboard()
        events = queue.Queue()
        engine = TypingEngine(events.put, backend, random.Random(1))
        engine.start("a\nB", settings())
        completed = self.wait_for(events, "completed")
        self.assertEqual(completed["position"], 3)
        meaningful = [event for event in backend.events if event != ("up", "ctrl") and event != ("up", "alt") and event != ("up", "win")]
        self.assertIn(("write", "a"), meaningful)
        self.assertIn(("down", "shift"), meaningful)
        self.assertIn(("press", "enter"), meaningful)
        self.assertIn(("write", "B"), meaningful)
        self.assertEqual(sum(event == ("press", "enter") for event in meaningful), 1)
        self.assertLess(abs(completed["difference"]), 0.05)

    def test_apostrophe_uses_exact_windows_input(self):
        backend = FakeKeyboard()
        engine = TypingEngine(lambda event: None, backend, random.Random(11))
        with mock.patch.object(engine, "_send_windows_unicode") as unicode_sender:
            engine._send_action(TypingAction("'", 1), settings())
        unicode_sender.assert_called_once_with("'")
        self.assertEqual(backend.events, [])

    def test_apostrophe_falls_back_to_ascii_key_if_unicode_input_fails(self):
        backend = FakeKeyboard()
        engine = TypingEngine(lambda event: None, backend, random.Random(12))
        with mock.patch.object(engine, "_send_windows_unicode", side_effect=OSError("blocked")):
            engine._send_action(TypingAction("'", 1), settings())
        self.assertEqual(backend.events, [("press", "'")])

    def test_human_typo_is_corrected_before_intended_character(self):
        backend = FakeKeyboard()
        rng = mock.Mock()
        rng.random.return_value = 0.0
        rng.choice.return_value = "h"
        rng.uniform.side_effect = [0.06, 0.05]
        engine = TypingEngine(lambda event: None, backend, rng)

        with mock.patch("typing_engine.time.sleep") as brief_sleep:
            engine._send_action(TypingAction("g", 1), settings(humanization_mode="Natural"))

        self.assertEqual(backend.events, [("write", "h"), ("press", "backspace"), ("write", "g")])
        self.assertEqual(brief_sleep.call_count, 2)

        backend.events.clear()
        engine._send_action(TypingAction("g", 2), settings(humanization_mode="Natural"), allow_correction=False)
        self.assertEqual(backend.events, [("write", "g")])

    def test_smart_apostrophe_pastes_and_restores_clipboard_if_unicode_fails(self):
        backend = FakeKeyboard()
        clipboard = FakeClipboard()
        engine = TypingEngine(lambda event: None, backend, random.Random(13))
        with (
            mock.patch("typing_engine._pyperclip", clipboard),
            mock.patch.object(engine, "_send_windows_unicode", side_effect=OSError("blocked")),
            mock.patch("typing_engine.time.sleep"),
        ):
            engine._send_action(TypingAction("’", 1), settings())

        self.assertEqual(clipboard.copies, ["’", "previous clipboard"])
        self.assertEqual(clipboard.value, "previous clipboard")
        self.assertIn(("down", "ctrl"), backend.events)
        self.assertIn(("press", "v"), backend.events)
        self.assertIn(("up", "ctrl"), backend.events)

    def test_pause_time_is_excluded_and_resume_continues_position(self):
        backend = FakeKeyboard()
        events = queue.Queue()
        engine = TypingEngine(events.put, backend, random.Random(2))
        engine.start("abcdef", settings(duration=0.30))
        time.sleep(0.07)
        self.assertTrue(engine.pause())
        before = len([e for e in backend.events if e[0] == "write"])
        time.sleep(0.12)
        during = len([e for e in backend.events if e[0] == "write"])
        self.assertEqual(before, during)
        self.assertTrue(engine.resume())
        completed = self.wait_for(events, "completed")
        self.assertGreaterEqual(completed["elapsed"], 0.25)
        self.assertLess(abs(completed["difference"]), 0.06)
        self.assertEqual("".join(value for kind, value in backend.events if kind == "write"), "abcdef")

    def test_stop_prevents_future_text_actions(self):
        backend = FakeKeyboard()
        events = queue.Queue()
        engine = TypingEngine(events.put, backend, random.Random(3))
        engine.start("abcdefghij", settings(duration=0.8))
        time.sleep(0.12)
        engine.stop()
        self.wait_for(events, "stopped")
        count = len([e for e in backend.events if e[0] == "write"])
        time.sleep(0.12)
        self.assertEqual(count, len([e for e in backend.events if e[0] == "write"]))
        self.assertLess(count, 10)

    def test_failsafe_is_reported_and_all_modifiers_are_released(self):
        backend = FailSafeKeyboard()
        events = queue.Queue()
        engine = TypingEngine(events.put, backend, random.Random(4))
        engine.start("x", settings(duration=0.02))
        error = self.wait_for(events, "error")
        self.assertIn("fail-safe", error["message"].lower())
        for modifier in ("shift", "ctrl", "alt", "win"):
            self.assertIn(("up", modifier), backend.events)


if __name__ == "__main__":
    unittest.main()
