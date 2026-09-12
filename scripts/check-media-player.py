#!/usr/bin/env python3
"""Checks the phone published to this desktop as an MPRIS player.

Talks to the real session bus: the player is registered, queried the way a
media applet queries it, pressed the way a keyboard's media key presses it, and
then withdrawn. What cannot be checked from here is Plasma's applet drawing it,
which is a matter of looking at the screen.

Run it from the repository root:  python3 scripts/check-media-player.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QTimer                      # noqa: E402
from PySide6.QtWidgets import QApplication                         # noqa: E402

from tessera.backends import mpris_server                          # noqa: E402

FAILURES: list[str] = []
TRACK = {
    "title": "So Good",
    "artist": "Jhene Aiko",
    "album": "Chilombo",
    "playing": True,
    "app": "Apple Music",
    "canControl": True,
}


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"{'ok  ' if ok else 'FAIL'} {label}{f': {detail}' if detail else ''}")
    if not ok:
        FAILURES.append(label)


def spin(milliseconds: int) -> None:
    """Run the event loop, which is what answers D-Bus calls."""
    loop = QEventLoop()
    QTimer.singleShot(milliseconds, loop.quit)
    loop.exec()


def gdbus(*args: str, timeout: float = 12) -> str:
    """Ask over the bus from outside, as a media applet would."""
    result = subprocess.run(
        ["gdbus", "call", "--session", "--dest", mpris_server.SERVICE,
         "--object-path", mpris_server.PATH, "--method", *args],
        capture_output=True, text=True, timeout=timeout,
    )
    return (result.stdout or result.stderr).strip()


def ask(method: str, *args: str) -> list[str]:
    """Run a query on another thread, because this one must keep serving."""
    out: list[str] = []
    thread = threading.Thread(
        target=lambda: out.append(gdbus(method, *args)), daemon=True
    )
    thread.start()
    while thread.is_alive():
        spin(100)
    return out


def main() -> int:
    app = QApplication(sys.argv)

    if not mpris_server.available():
        print("no session bus here, so there is nothing to publish to")
        return 0

    player = mpris_server.MprisServer()
    pressed: list[str] = []
    raised: list[bool] = []
    player.commanded.connect(pressed.append)
    player.raiseRequested.connect(lambda: raised.append(True))

    print("-- publishing")
    check("the player takes its bus name", player.publish())
    player.update(TRACK)
    check("it knows what is playing", player.status == "Playing", player.status)

    print("\n-- what a media applet reads")
    properties = ask("org.freedesktop.DBus.Properties.GetAll",
                     mpris_server.PLAYER_IFACE)[0]
    check("the transport properties are readable", "CanControl" in properties)
    check("it says it can be controlled", "'CanControl': <true>" in properties)
    check("and that it cannot seek", "'CanSeek': <false>" in properties)

    metadata = ask("org.freedesktop.DBus.Properties.Get",
                   mpris_server.PLAYER_IFACE, "Metadata")[0]
    check("the title is there", "'So Good'" in metadata, metadata[:80])
    check("the artist is a list, as every other player sends it",
          "<['Jhene Aiko']>" in metadata)
    check("the track id is an object path, not a string",
          "objectpath" in metadata)
    check(
        "nothing is double-boxed",
        "<<" not in metadata,
        "a variant inside a variant reads as empty" if "<<" in metadata else "",
    )

    identity = ask("org.freedesktop.DBus.Properties.Get",
                   mpris_server.ROOT_IFACE, "Identity")[0]
    check("it names the app on the phone", "Apple Music" in identity, identity)

    print("\n-- what the buttons do")
    for method, action in (
        ("Next", "next"), ("Previous", "previous"),
        ("PlayPause", "playpause"), ("Pause", "pause"),
    ):
        ask(f"{mpris_server.PLAYER_IFACE}.{method}")
    spin(300)
    check(
        "every button reaches the phone's own action",
        pressed == ["next", "previous", "playpause", "pause"],
        str(pressed),
    )

    ask(f"{mpris_server.ROOT_IFACE}.Raise")
    spin(300)
    check("the applet can ask for the window", bool(raised))

    print("\n-- when the phone stops")
    player.update({"title": "", "playing": False, "canControl": False})
    check("the status goes to stopped", player.status == "Stopped", player.status)
    check("and there is no stale track left", player.metadata == {})

    player.withdraw()
    check("the bus name is given back", not player.published)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed:")
        for name in FAILURES:
            print(f"  - {name}")
        return 1
    print("all media-player checks passed")
    app.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
