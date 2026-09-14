"""What this computer is, and what it can therefore do."""

from __future__ import annotations

import os
import platform as _stdlib
import shutil
import subprocess
import sys
from pathlib import Path

APP_ID = "dev.tessera.Tessera"
APP_NAME = "Tessera"
#: The directory name under XDG on Linux. Not the app id: this is what shipped,
#: and changing it would orphan every existing pairing and setting.
UNIX_DIR = "tessera"


def detect() -> str:
    """"windows", "macos" or "linux"."""
    forced = os.environ.get("TESSERA_PLATFORM", "").strip().lower()
    if forced in {"windows", "linux", "macos"}:
        return forced
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


NAME = detect()
IS_WINDOWS = NAME == "windows"
IS_LINUX = NAME == "linux"
IS_MACOS = NAME == "macos"

#: The kernel actually underneath, which TESSERA_PLATFORM cannot change.
REAL = "windows" if sys.platform.startswith("win") else (
    "macos" if sys.platform == "darwin" else "linux"
)

#: Features that cannot work on a platform, and why.
UNSUPPORTED: dict[str, dict[str, str]] = {
    "windows": {
        "bluetooth_codecs":
            "Windows chooses the Bluetooth codec itself, and offers no way for "
            "another program to change it or to add an LDAC decoder.",
        "kdeconnect":
            "KDE Connect is reached over D-Bus, which is a Linux interface. "
            "The companion app covers the same ground.",
        "dnd_desktop":
            "Windows Focus Assist cannot be set by another program, so Do Not "
            "Disturb only travels phone to desktop -- Tessera's own popups go "
            "quiet, and Windows keeps its own setting.",
        "mpris":
            "Media details come from the companion app; MPRIS is a Linux "
            "desktop interface.",
    },
    "macos": {
        "bluetooth_audio": "Not implemented on macOS.",
        "bluetooth_codecs": "Not implemented on macOS.",
        "bluetooth_calls": "Not implemented on macOS.",
        "webcam": "Not implemented on macOS.",
        "kdeconnect": "KDE Connect is reached over D-Bus, a Linux interface.",
        "dnd_desktop": "Not implemented on macOS.",
        "mpris": "MPRIS is a Linux desktop interface.",
        "hotspot": "Joining a network from the app is not implemented on macOS.",
        "storage": "Not implemented on macOS.",
    },
    "linux": {},
}


def supported(feature: str) -> bool:
    """Whether *feature* can work here at all."""
    return feature not in UNSUPPORTED.get(NAME, {})


def reason(feature: str) -> str:
    """Why *feature* cannot work here, or an empty string when it can."""
    return UNSUPPORTED.get(NAME, {}).get(feature, "")


# -- directories -------------------------------------------------------------


def _windows_dir(variable: str, fallback: str) -> Path:
    base = os.environ.get(variable)
    if base:
        return Path(base)
    return Path.home() / "AppData" / fallback


def config_dir() -> Path:
    """Where settings live. %APPDATA%\\Tessera, or $XDG_CONFIG_HOME/…"""
    if IS_WINDOWS:
        return _windows_dir("APPDATA", "Roaming") / APP_NAME
    if IS_MACOS:
        return Path.home() / "Library" / "Application Support" / APP_ID
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / UNIX_DIR


def state_dir() -> Path:
    """Where logs and icons live: big, rewritable, not worth syncing."""
    if IS_WINDOWS:
        return _windows_dir("LOCALAPPDATA", "Local") / APP_NAME
    if IS_MACOS:
        return Path.home() / "Library" / "Logs" / APP_ID
    base = os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state"
    return Path(base) / UNIX_DIR


def cache_dir() -> Path:
    if IS_WINDOWS:
        return _windows_dir("LOCALAPPDATA", "Local") / APP_NAME / "cache"
    if IS_MACOS:
        return Path.home() / "Library" / "Caches" / APP_ID
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / UNIX_DIR


# -- finding the tools -------------------------------------------------------

#: Where installers actually put things, checked after PATH.
_EXTRA_PATHS: dict[str, tuple[str, ...]] = {
    "windows": (
        r"%LOCALAPPDATA%\Android\Sdk\platform-tools",
        r"%LOCALAPPDATA%\Programs\scrcpy",
        r"%LOCALAPPDATA%\Microsoft\WinGet\Links",
        r"%USERPROFILE%\scoop\shims",
        r"%ProgramData%\chocolatey\bin",
        r"%ProgramFiles%\scrcpy",
        r"%ProgramFiles(x86)%\scrcpy",
        r"%ProgramFiles%\Android\platform-tools",
    ),
    "linux": (),
    "macos": ("/opt/homebrew/bin", "/usr/local/bin"),
}


def tool(name: str) -> str:
    """The file name of an external tool: adb becomes adb.exe on Windows."""
    if IS_WINDOWS and not name.lower().endswith(".exe"):
        return f"{name}.exe"
    return name


#: name -> (path, when looked up). Searching PATH costs tens of ms on Windows.
_found: dict[str, tuple[str, float]] = {}
#: Long enough to cover startup, short enough to notice a tool installed later.
TOOL_CACHE_SECONDS = 30.0


def find_tool(name: str) -> str:
    """Full path to *name*, looking where its installers put it."""
    import time

    cached = _found.get(name)
    if cached and time.monotonic() - cached[1] < TOOL_CACHE_SECONDS:
        return cached[0]
    path = _find_tool(name)
    _found[name] = (path, time.monotonic())
    return path


def _find_tool(name: str) -> str:
    found = shutil.which(tool(name)) or shutil.which(name)
    if found:
        return found
    for raw in _EXTRA_PATHS.get(NAME, ()):
        directory = Path(os.path.expandvars(raw))
        if "%" in str(directory):        # an unset variable; nothing to look in
            continue
        candidate = directory / tool(name)
        if candidate.is_file():
            return str(candidate)
    return ""


def have_tool(name: str) -> bool:
    return bool(find_tool(name))


# -- running things ----------------------------------------------------------


def no_window_flags() -> int:
    """Creation flags that keep a console window from flashing on Windows."""
    if REAL != "windows":
        return 0
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))


def describe() -> str:
    """One line for the log and the Settings page."""
    if IS_WINDOWS:
        if REAL == "windows":
            # platform.release() runs a subprocess on Windows.
            build = sys.getwindowsversion().build           # type: ignore[attr-defined]
            return f"Windows {'11' if build >= 22000 else '10'} (build {build})"
        return "Windows"
    if IS_MACOS:
        return f"macOS {_stdlib.mac_ver()[0]}".strip()
    return f"Linux {_stdlib.release()}".strip()
