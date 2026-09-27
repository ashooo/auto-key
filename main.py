"""Auto Key application entry point."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox


def main() -> None:
    root = tk.Tk()
    try:
        from gui import TypingAutomationApp

        TypingAutomationApp(root)
    except Exception as exc:
        root.withdraw()
        messagebox.showerror(
            "Auto Key could not start",
            f"{exc}\n\nInstall dependencies with:\npip install -r requirements.txt",
        )
        root.destroy()
        return
    root.mainloop()


if __name__ == "__main__":
    main()

