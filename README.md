# Auto Key

Auto Key is a local Windows desktop typing utility built with Python, Tkinter, and PyAutoGUI. It sends the exact text you provide to whichever application has keyboard focus after a visible, configurable countdown. It does not inspect browsers, select targets, bypass protections, or hide its automation.

## Requirements and installation

- Windows 10 or 11
- Python 3.11 or 3.12 recommended (the standard Windows installer includes Tkinter)

From PowerShell or Command Prompt in this directory:

```bat
python -m venv venv
venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python main.py
```

## Basic use

1. Paste or type text in **Text to Type**.
2. Enter the target duration in minutes; decimals such as `0.5` and `2.5` are accepted.
3. Leave the countdown at 25 seconds or select/type another value from 1 to 120.
4. Select a line-break method. **Shift + Enter** is the safe default for text areas where plain Enter might submit.
5. Click **Start**, switch to the target application, and click its text field.
6. Do not interact with the keyboard until the job completes unless you intend to pause or stop it.

The app never activates or clicks another window. No input is sent before the countdown reaches zero.

## Timing behavior and one-action-per-minute guarantee

The target duration is active typing time. Auto Key assigns relative timing weights to characters, spaces, punctuation, and line breaks, then continuously redistributes the remaining time over the remaining input. Small send-time differences are corrected smoothly as the job proceeds. Random variation creates short correlated fast/slow bursts. The Human behavior setting adds occasional word-boundary thinking pauses and rare adjacent-key typos that are corrected with Backspace before typing continues. Choose Off, Subtle, Natural, or Expressive; tight-duration jobs automatically omit corrections when there is not enough time.

While actively typing, every planned gap before a keyboard action is capped at **59 seconds**. This guarantees at least one action during every active minute with a small allowance for operating-system input overhead. If the text contains too few actions to fill the requested duration under that rule, Start shows an error and asks for a shorter duration or more text. Paused time is intentionally excluded from this guarantee and from the target duration.

Configured minimum/maximum delays and variation shape the relative cadence. The requested total duration takes priority, so the dynamic scheduler may scale those values. Random character timing is bounded before this global scaling, and the scheduler compensates for variation.

## Pause, resume, stop, and safety

- **Pause** stops new input quickly and retains the exact source position.
- **Resume** continues from that position. Time spent paused does not count toward the selected duration.
- **Stop** cancels all future input.
- **Cancel Countdown** cancels before typing begins and sends no text.
- **F8** is the default global emergency stop and works during countdown, typing, or pause. It can be changed in Advanced Settings.
- PyAutoGUI's corner fail-safe remains enabled. Move the mouse to the upper-left corner to trigger it; the error is handled and modifier keys are released.

Shift, Ctrl, Alt, and Windows modifiers are released after newline shortcuts, pause, stop, failure, emergency stop, and shutdown. Closing the app stops its worker thread. A source text that does not end with a newline never gets an extra Enter; if it does end in a newline, only that source newline is sent.

When **Notify when typing is complete** is enabled in Advanced Settings, a completion notification appears after a successful run. Cancelled, stopped, and failed runs do not trigger it.

## Line breaks and custom shortcuts

- **Shift + Enter** presses and safely releases Shift around Enter.
- **Enter** sends plain Enter.
- **Custom key combination** accepts forms such as `Ctrl+Enter` or `Ctrl+Shift+Enter`.

Remember that Enter can submit browser forms. Test with Notepad first.

## Unicode

ASCII characters use PyAutoGUI. On Windows, straight apostrophes, quotation marks, and backticks use exact character input first so international keyboard layouts cannot turn them into dead keys; they fall back to ordinary ASCII key presses if a target rejects that input method. Other non-ASCII text first uses the operating system UTF-16 input-event path. If the focused application rejects that event, Auto Key briefly copies the single character, pastes it, and restores the previous text clipboard. This prevents smart punctuation such as `’` from stopping the job. A few games, elevated windows, and custom controls may reject both input methods; Windows may also prevent a non-elevated app from typing into an elevated target.

## Settings and presets

Slow, Normal, and Fast presets adjust relative cadence, punctuation timing, randomness, and countdown without changing the selected target duration. You can name and save custom presets in Advanced Settings. Normal configuration and custom presets are stored locally in `settings.json`; source text is never saved. Missing, partial, or corrupt settings safely fall back to defaults.

## Run tests

The tests use a fake keyboard backend and do not type into other applications:

```bat
python -m unittest discover -s tests -v
```

For a manual safety check, begin with Notepad and a short countdown. Test one line, multiple paragraphs and blank lines, pause/resume, Stop, countdown cancellation, F8, mouse-corner fail-safe, Unicode, ending/non-ending newlines, and closing during a run.

## Build a Windows executable

With the virtual environment active:

```bat
pip install pyinstaller
pyinstaller --noconfirm --onefile --windowed --name AutoKey main.py
```

The executable appears at `dist\AutoKey.exe`. `settings.json` is created beside the executable when settings are saved. Some security products warn about global-hotkey and synthetic-keyboard applications; build and run only code you trust.
