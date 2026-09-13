#!/usr/bin/env python3
"""Checks clipboard sharing, and its fallback for a phone without Shizuku."""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Before any tessera import: a check must never write the real configuration.
from sandbox import isolate                                          # noqa: E402

isolate()

from PySide6.QtCore import QObject, Signal                           # noqa: E402
from PySide6.QtGui import QClipboard                                 # noqa: E402
from PySide6.QtWidgets import QApplication                           # noqa: E402

from tessera.backends import clipboard_adb                           # noqa: E402
from tessera.core import hub as hub_module                           # noqa: E402
from tessera.core.clipboard import ClipboardSync                     # noqa: E402
from tessera.core.config import ClipboardConfig, Config              # noqa: E402

FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"{'ok  ' if ok else 'FAIL'} {label}{f': {detail}' if detail else ''}")
    if not ok:
        FAILURES.append(label)


class FakeClient:
    def __init__(self, connected=True, caps=("clipboard",)):
        self.connected = connected
        self.caps = set(caps)
        self.sent: list[dict] = []

    def supports(self, name):
        return name in self.caps

    def send(self, message):
        self.sent.append(message)

    def request(self, message, on_reply):
        self.sent.append(message)
        on_reply({"text": "from the phone"})


class FakeHelper(QObject):
    changed = Signal(str)

    def __init__(self, running=True):
        super().__init__()
        self.running = running
        self.sent: list[str] = []
        self.pulled = 0

    def send(self, text):
        self.sent.append(text)
        return True

    def pull(self):
        self.pulled += 1
        return True


def settle(app: QApplication, sync: ClipboardSync) -> None:
    """Let the debounce run."""
    sync._debounce.stop()
    sync._push_local()
    app.processEvents()


def routes(app: QApplication) -> None:
    print("-- which route")
    config = ClipboardConfig(mode="two_way")
    helper = FakeHelper()

    phone = ClipboardSync(FakeClient(), config, helper=helper)
    check("the phone's own route wins when it has one", phone.route == "phone")
    check("and the adb helper is then not wanted", not phone.wants_helper)

    no_caps = ClipboardSync(FakeClient(caps=()), config, helper=helper)
    check("a phone without the clipboard falls back to adb", no_caps.route == "adb")
    check("which is then wanted", no_caps.wants_helper)

    offline = ClipboardSync(FakeClient(connected=False), config, helper=helper)
    check("no companion link at all still has adb", offline.route == "adb")

    nothing = ClipboardSync(FakeClient(caps=()), config, helper=FakeHelper(running=False))
    check("with no helper running there is no route", nothing.route == "")

    off = ClipboardSync(FakeClient(caps=()), ClipboardConfig(mode="off"), helper=helper)
    check("sharing switched off never wants the helper", not off.wants_helper)


def sending(app: QApplication) -> None:
    print("\n-- desktop to phone")
    clipboard = QApplication.clipboard()
    helper = FakeHelper()
    client = FakeClient(caps=())
    sync = ClipboardSync(client, ClipboardConfig(mode="two_way"), helper=helper)

    clipboard.setText("copied on the desktop", QClipboard.Mode.Clipboard)
    settle(app, sync)
    check("a desktop copy goes to the helper", helper.sent == ["copied on the desktop"], str(helper.sent))
    check("and not down the companion link", client.sent == [], str(client.sent))

    client.caps.add("clipboard")
    clipboard.setText("second copy", QClipboard.Mode.Clipboard)
    settle(app, sync)
    check("once the phone can, the companion link carries it",
          client.sent and client.sent[-1] == {"t": "clipboard_set", "text": "second copy"},
          str(client.sent))
    check("and the helper is left alone", helper.sent == ["copied on the desktop"])


def receiving(app: QApplication) -> None:
    print("\n-- phone to desktop, and no echo")
    clipboard = QApplication.clipboard()
    helper = FakeHelper()
    sync = ClipboardSync(FakeClient(caps=()), ClipboardConfig(mode="two_way"), helper=helper)

    helper.changed.emit("copied on the phone")
    app.processEvents()
    check("a phone copy from the helper reaches this clipboard",
          clipboard.text(QClipboard.Mode.Clipboard) == "copied on the phone")
    settle(app, sync)
    check("and is not sent straight back", helper.sent == [], str(helper.sent))

    sync.pull()
    check("asking for the phone's clipboard goes to the helper", helper.pulled == 1)

    one_way = ClipboardSync(FakeClient(caps=()), ClipboardConfig(mode="desktop_to_phone"),
                            helper=FakeHelper())
    clipboard.setText("unchanged", QClipboard.Mode.Clipboard)
    one_way._helper.changed.emit("should not arrive")
    app.processEvents()
    check("desktop-to-phone only ignores the phone",
          clipboard.text(QClipboard.Mode.Clipboard) == "unchanged")


def helper_lines(app: QApplication) -> None:
    print("\n-- the helper's lines")
    helper = clipboard_adb.AdbClipboard()
    seen: list[str] = []
    states: list[bool] = []
    helper.changed.connect(seen.append)
    helper.runningChanged.connect(states.append)

    helper.feed(b"Error: Could not find class 'dev.tessera.companion.shell.ClipboardHelper'")
    check("adb's own complaints are not taken for the helper", not helper.running and not seen)
    helper.feed(b'{"t":"ready","sdk":37,"listening":true}')
    check("ready means running", helper.running and states == [True], str(states))
    helper.feed('{"t":"clip","text":"line one\\nline two ✓"}'.encode())
    check("text survives newlines and non-ASCII", seen == ["line one\nline two ✓"], repr(seen))
    helper.feed(b"{not json")
    check("a garbled line is ignored", len(seen) == 1)

    command = clipboard_adb.argv("192.168.1.5:5555")
    check("adb without a terminal, so nothing is echoed or mangled", "-T" in command, str(command))
    check("the helper comes out of the installed APK",
          "pm path dev.tessera.companion" in command[-1] and "app_process" in command[-1])


def hub_wiring(app: QApplication) -> None:
    print("\n-- the hub starts the helper only when it is the only way")
    hub_module.Hub._apply_codec_preference = lambda self: None
    hub_module.Hub._watch_bluetooth = lambda self: None
    config = Config()
    config.features.clipboard = True
    config.clipboard.mode = "two_way"
    hub = hub_module.Hub(config)

    started: list[str] = []
    hub.clipboard_adb.start = lambda serial: started.append(serial)
    stopped: list[bool] = []
    hub.clipboard_adb.stop = lambda: stopped.append(True)

    hub._set_serial(("192.168.1.5:5555", ""))
    check("adb appearing, with no companion clipboard, starts it", started == ["192.168.1.5:5555"], str(started))

    hub.clipboard_adb._serial = "192.168.1.5:5555"
    hub.companion._authenticated = True
    hub.companion._socket = object()
    hub.companion._capabilities = ["clipboard"]
    hub.update_clipboard_route()
    check("the phone offering it itself stops the helper", stopped == [True])
    hub.companion._authenticated = False
    hub.companion._socket = None

    config.features.clipboard = False
    started.clear()
    hub.update_clipboard_route()
    check("clipboard sharing switched off never starts it", started == [])


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    routes(app)
    sending(app)
    receiving(app)
    helper_lines(app)
    hub_wiring(app)
    print()
    if FAILURES:
        print(f"{len(FAILURES)} failed")
        return 1
    print("all clipboard checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
