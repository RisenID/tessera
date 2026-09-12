"""Starting Tessera when the user logs in.

An XDG autostart entry in ~/.config/autostart, which KDE, GNOME, XFCE and the
rest all read -- and which shows up in the desktop's own autostart settings, so
the user can see and undo it there too.

Login, not boot: the app needs a session to draw in and a tray to sit in, so
there is nothing useful to start before someone logs in.
"""

from __future__ import annotations

import logging
import os
import shlex
import shutil
import sys
from pathlib import Path

log = logging.getLogger(__name__)

APP_ID = "dev.tessera.Tessera"
LAUNCHER = "tessera"

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


def path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "autostart" / f"{APP_ID}.desktop"


def launch_command() -> str:
    """How to start Tessera again.

    Prefers the launcher on PATH, which covers both the package and the
    per-user install. A checkout with neither gets an explicit interpreter
    call, so autostart works before anything has been installed at all.
    """
    found = shutil.which(LAUNCHER)
    if found:
        return shlex.quote(found)

    import tessera

    root = Path(tessera.__file__).resolve().parents[1]
    return (
        f"env PYTHONPATH={shlex.quote(str(root))} "
        f"{shlex.quote(sys.executable)} -m tessera"
    )


def enabled() -> bool:
    entry = path()
    if not entry.is_file():
        return False
    # A desktop that offers its own autostart list disables an entry by
    # editing it rather than deleting it, so both switches have to be read.
    text = entry.read_text("utf-8", errors="replace").lower()
    if "hidden=true" in text or "x-gnome-autostart-enabled=false" in text:
        return False
    return True


def set_enabled(on: bool) -> bool:
    """Turn autostart on or off. False when the file could not be written."""
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
