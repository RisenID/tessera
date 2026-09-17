#!/usr/bin/env python3
"""Checks the presence lock's state machine, with a pretend beacon."""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from sandbox import isolate                                          # noqa: E402

isolate()

from PySide6.QtCore import QEventLoop, QTimer                      # noqa: E402
from PySide6.QtWidgets import QApplication                         # noqa: E402

from tessera.backends import lockscreen                            # noqa: E402
from tessera.core.config import Config                             # noqa: E402
from tessera.core.hub import Hub                                   # noqa: E402

FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"{'ok  ' if ok else 'FAIL'} {label}{f': {detail}' if detail else ''}")
    if not ok:
        FAILURES.append(label)


def settle(milliseconds: int = 150) -> None:
    loop = QEventLoop()
    QTimer.singleShot(milliseconds, loop.quit)
    loop.exec()


class Beacon:
    """What the watcher would read, one reading per poll."""

    def __init__(self, readings: list) -> None:
        self.readings = iter(readings)

    def rssi(self):
        return next(self.readings, None)

    def stop(self) -> None:
        pass


def drive(hub: Hub, readings: list, away_seconds: int = 0) -> tuple[list, int]:
    presence = hub.presence
    presence.config.away_seconds = away_seconds
    # A reading counts as fresh for twenty seconds; here a poll is a moment,
    # so one poll after the last reading is "far" and the next is "gone".
    presence.fresh_seconds = 0.2
    presence._watcher = Beacon(readings)
    presence._armed = False
    presence._last_near = 0.0
    presence.state = "waiting"
    locks = []
    lockscreen.lock = lambda: locks.append(1) or None            # type: ignore[assignment]
    states: list = []
    presence.changed.connect(lambda s, r: states.append(s))
    for _ in readings:
        presence._poll()
        settle()
    presence._watcher = None
    return states, len(locks)


def main() -> int:
    app = QApplication(sys.argv)
    hub = Hub(Config())

    print("-- leaving locks once")
    states, locks = drive(hub, [-60, -60, -90, -90, None, None])
    check("near, then far, then gone", states == ["near", "far", "gone"], str(states))
    check("locked exactly once", locks == 1, str(locks))

    print("\n-- a phone that was never near does not lock")
    states, locks = drive(hub, [None, None, -95])
    check("only gone and far are reported", "near" not in states, str(states))
    check("nothing was locked", locks == 0, str(locks))

    print("\n-- a brief dip is not leaving")
    states, locks = drive(hub, [-60, -85, -60], away_seconds=600)
    check("stays near through one weak reading", set(states) == {"near"}, str(states))
    check("nothing was locked", locks == 0, str(locks))

    print("\n-- locking again needs the phone back first")
    states, locks = drive(hub, [-60, None, None, -60, None, None])
    check("two departures, two locks", locks == 2, str(locks))

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed:")
        for name in FAILURES:
            print(f"  - {name}")
        return 1
    print("all presence checks passed")
    app.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
