#!/usr/bin/env python3
"""Checks changing the phone's Do Not Disturb through the companion."""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from sandbox import isolate                                          # noqa: E402

isolate()

from PySide6.QtNetwork import QAbstractSocket                      # noqa: E402
from PySide6.QtWidgets import QApplication                         # noqa: E402

from tessera.core.config import Config                             # noqa: E402
from tessera.core.hub import Hub                                   # noqa: E402

FAILURES: list[str] = []


def check(label: str, produced: object, wanted: object) -> None:
    ok = produced == wanted
    print(f"{'ok  ' if ok else 'FAIL'} {label}: {produced!r}")
    if not ok:
        FAILURES.append(label)


class Socket:
    def state(self):
        return QAbstractSocket.SocketState.ConnectedState

    def write(self, _data):
        return 0

    def blockSignals(self, _on):
        pass

    def abort(self):
        pass

    def deleteLater(self):
        pass


def connected_hub(answer: dict) -> tuple[Hub, list[dict], list[str]]:
    hub = Hub(Config())
    hub.config.dnd.mode = "off"
    client = hub.companion
    client._socket = Socket()
    client._authenticated = True
    sent: list[dict] = []
    errors: list[str] = []

    def request(message, on_reply):
        sent.append(message)
        on_reply(answer)

    client.request = request
    hub.errorOccurred.connect(errors.append)
    return hub, sent, errors


def main() -> int:
    app = QApplication(sys.argv)

    print("-- the phone takes the change")
    hub, sent, errors = connected_hub({"t": "reply", "mode": "priority"})
    hub._on_phone_dnd("off")
    hub.set_phone_dnd("priority")
    check("asks the phone", [m.get("t") for m in sent], ["dnd_set"])
    check("and shows the new mode", hub._phone_dnd, "priority")
    check("without an error", errors, [])

    print("\n-- something else holds it")
    reason = "Do Not Disturb was set by something other than Tessera."
    hub, sent, errors = connected_hub({"t": "error", "message": reason})
    shown: list[str] = []
    hub.dndChanged.connect(shown.append)
    hub._on_phone_dnd("priority")
    shown.clear()
    hub.set_phone_dnd("off")
    check("the switch goes back to what the phone has", hub._phone_dnd, "priority")
    check("the rail and page follow", shown[-1:], ["priority"])
    check("with the phone's reason", errors, [reason])

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed:")
        for name in FAILURES:
            print(f"  - {name}")
        return 1
    print("all Do Not Disturb checks passed")
    app.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
