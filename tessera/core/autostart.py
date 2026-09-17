"""Starting Tessera when the user logs in."""

from __future__ import annotations

import logging
import os
import shlex
import sys
from pathlib import Path

from . import platform

log = logging.getLogger(__name__)

APP_ID = platform.APP_ID
APP_NAME = platform.APP_NAME
LAUNCHER = "tessera"

#: The name of the registry value, and of the desktop entry.
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

ENTRY = """[Desktop Entry]
Type=Application
Name=Tessera
Comment=Phone companion
Exec={command}
Icon={app_id}
Terminal=false
StartupNotify=false
X-GNOME-Autostart-enabled=true
"""


def launch_command() -> str:
    """How to start Tessera again, quoted for the platform."""
    if getattr(sys, "frozen", False):
        return _quote(sys.executable)

    found = platform.find_tool(LAUNCHER)
    if found:
        return _quote(found)

    import tessera

    root = Path(tessera.__file__).resolve().parents[1]
    if platform.IS_WINDOWS:
        # No env(1) on Windows: use the interpreter that can already see the
        # package, which pythonw avoids a console window for.
        interpreter = sys.executable
        if interpreter.lower().endswith("python.exe"):
            windowed = Path(interpreter).with_name("pythonw.exe")
            if windowed.is_file():
                interpreter = str(windowed)
        return f'{_quote(interpreter)} -m tessera'
    return (
        f"env PYTHONPATH={shlex.quote(str(root))} "
        f"{shlex.quote(sys.executable)} -m tessera"
    )


def _quote(value: str) -> str:
    return f'"{value}"' if platform.IS_WINDOWS else shlex.quote(value)


# -- Linux -------------------------------------------------------------------


def path() -> Path:
    """The autostart entry's file. Windows has none; see RUN_KEY."""
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "autostart" / f"{APP_ID}.desktop"


def _xdg_enabled() -> bool:
    entry = path()
    if not entry.is_file():
        return False
    # A desktop that offers its own autostart list disables an entry by
    # editing it rather than deleting it, so both switches have to be read.
    text = entry.read_text("utf-8", errors="replace").lower()
    if "hidden=true" in text or "x-gnome-autostart-enabled=false" in text:
        return False
    return True


def _xdg_set(on: bool) -> bool:
    entry = path()
    try:
        if not on:
            entry.unlink(missing_ok=True)
            return True
        entry.parent.mkdir(parents=True, exist_ok=True)
        entry.write_text(
            ENTRY.format(command=launch_command(), app_id=APP_ID), "utf-8"
        )
        entry.chmod(0o644)
        return True
    except OSError as exc:
        log.warning("could not %s autostart: %s", "enable" if on else "disable", exc)
        return False


# -- Windows -----------------------------------------------------------------


def _registry():
    """The winreg module, or None where there is no registry."""
    try:
        import winreg
    except ImportError:
        return None
    return winreg


def _win_enabled() -> bool:
    winreg = _registry()
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _kind = winreg.QueryValueEx(key, APP_NAME)
            return bool(str(value).strip())
    except FileNotFoundError:
        return False
    except OSError as exc:
        log.warning("could not read the Run key: %s", exc)
        return False


def _win_set(on: bool) -> bool:
    winreg = _registry()
    if winreg is None:
        return False
    try:
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            if on:
                winreg.SetValueEx(
                    key, APP_NAME, 0, winreg.REG_SZ, launch_command()
                )
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError as exc:
        log.warning("could not %s autostart: %s", "enable" if on else "disable", exc)
        return False


# -- the interface the app uses ----------------------------------------------


def enabled() -> bool:
    return _win_enabled() if platform.IS_WINDOWS else _xdg_enabled()


def set_enabled(on: bool) -> bool:
    """Turn autostart on or off. False when it could not be written."""
    return _win_set(on) if platform.IS_WINDOWS else _xdg_set(on)


