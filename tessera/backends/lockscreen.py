"""Locking this computer's session."""

from __future__ import annotations

import logging

from ..core import platform
from ..core.proc import have, run

log = logging.getLogger(__name__)


def lock() -> str | None:
    """Lock the screen. None when it worked, else why not."""
    if platform.REAL == "windows":
        import ctypes

        if ctypes.windll.user32.LockWorkStation():        # type: ignore[attr-defined]
            return None
        return "Windows refused to lock the workstation."

    attempts = []
    if have("loginctl"):
        attempts.append(["loginctl", "lock-session"])
    if have("gdbus"):
        attempts.append([
            "gdbus", "call", "--session", "--dest", "org.freedesktop.ScreenSaver",
            "--object-path", "/ScreenSaver", "--method", "org.freedesktop.ScreenSaver.Lock",
        ])
    if have("xdg-screensaver"):
        attempts.append(["xdg-screensaver", "lock"])
    problems = []
    for argv in attempts:
        result = run(argv, timeout=10.0)
        if result.ok:
            return None
        problems.append(f"{argv[0]}: {result.text.strip()[:120] or 'failed'}")
    return "No way to lock the screen was found: " + ("; ".join(problems) or "none of loginctl, gdbus or xdg-screensaver is installed.")
