#!/usr/bin/env python3
"""Checks the desktop-notification path."""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Before any tessera import: a check must never write the real configuration.
from sandbox import isolate                                          # noqa: E402

isolate()

from PySide6.QtCore import QEventLoop, QTimer                      # noqa: E402
from PySide6.QtWidgets import QApplication, QSystemTrayIcon        # noqa: E402

from tessera.backends import notify                                # noqa: E402
from tessera.core.config import Config                             # noqa: E402
from tessera.core.hub import Hub                                   # noqa: E402
from tessera.core.models import Notification                       # noqa: E402
from tessera.ui import popups as popups_module                     # noqa: E402
from tessera.ui.popups import Popups                               # noqa: E402

FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"{'ok  ' if ok else 'FAIL'} {label}{f': {detail}' if detail else ''}")
    if not ok:
        FAILURES.append(label)


def settle(milliseconds: int = 400) -> None:
    """Let Qt deliver D-Bus signals, which arrive on the event loop."""
    loop = QEventLoop()
    QTimer.singleShot(milliseconds, loop.quit)
    loop.exec()


def same(label: str, produced: object, wanted: object) -> None:
    """Checks a value rather than a condition, and shows both when they differ."""
    check(label, produced == wanted, f"{produced!r}" if produced != wanted else f"{produced!r}")


def gvariant() -> None:
    """The text gdbus reads. Quoting mistakes here become silent failures."""
    same("a plain string is quoted", notify._string("hello"), "'hello'")
    same("an apostrophe is escaped", notify._string("it's"), "'it\\'s'")
    same(
        "a backslash is escaped",
        notify._string("back\\slash"),
        "'back\\\\slash'",
    )
    check(
        "a newline does not break the literal",
        "\\n" in notify._string("two\nlines"),
        notify._string("two\nlines"),
    )
    same("an empty action list is empty", notify._array([]), "[]")
    same(
        "actions are key then label",
        notify._array(["inline-reply", "Reply"]),
        "['inline-reply', 'Reply']",
    )
    same(
        "hints are a dictionary of variants",
        notify._dictionary({"urgency": notify._variant(1, "byte")}),
        "{'urgency': <byte 1>}",
    )
    same("the id is read back out of gdbus", notify._first_id("(uint32 42,)"), 42)
    same("nothing in, nothing out", notify._first_id(""), 0)


def server() -> None:
    """The desktop's own notification server, for real."""
    notifier = notify.Notifier()
    if not notifier.available:
        print("  (no notification server here; the tray is used instead)")
        return

    check("the server is reachable", notifier.available)
    print(f"     it offers: {', '.join(notifier.capabilities)}")

    closed: list[int] = []
    notifier.closed.connect(closed.append)

    given = notifier.send(
        "Tessera self-check",
        "Raised by scripts/check-notifications.py, and closed again.",
        repliable=True,
        clearable=True,
    )
    check("a notification is accepted", given > 0, str(given))

    notifier.close(given)
    settle(800)
    check(
        "and the server reports it closed",
        given in closed,
        f"heard about {closed}",
    )
    check(
        "an inline reply is offered where the server supports it",
        notifier.can_reply or "inline-reply" not in notifier.capabilities,
    )

    # Plasma refuses a repeat of a popup it still shows. That is not a failure.
    from tessera.core.proc import Result

    refused = Result(("gdbus",), 1, "", "GDBus.Error:org.freedesktop.Notifications.Error."
                     "ExcessNotificationGeneration: Created too many similar notifications")
    notifier._gdbus = lambda *_a: refused                             # type: ignore[method-assign]
    same("a refused repeat is reported as such", notifier.send("x", "y"), notify.REPEATED)
    notifier._gdbus = lambda *_a: Result(("gdbus",), 1, "", "something else")  # type: ignore[method-assign]
    same("any other failure is a failure", notifier.send("x", "y"), 0)


def popups() -> None:
    """What the app does with an arriving notification."""
    hub = Hub(Config())
    tray = QSystemTrayIcon()
    popup = Popups(hub, tray)

    if not popup.notifier.available:
        print("  (no server; skipping the popup checks)")
        return

    note = Notification(
        id="n1", app="WhatsApp", package="com.whatsapp",
        title="Aai", text="Dinner at 8?", repliable=True,
    )
    popup.show(note)
    settle(1500)                    # sent from a worker now
    check("an arriving notification raises a popup", popup._by_phone.get("n1", 0) > 0)

    first = popup._by_phone["n1"]
    popup.show(Notification(**{**note.__dict__, "text": "Or 9?"}))
    settle(1500)
    check(
        "a second message replaces its own popup rather than stacking",
        len(popup._by_phone) == 1 and popup._by_phone["n1"] == first,
        str(popup._by_phone),
    )

    # The phone re-posts the same notification unchanged (a player on every
    # pause), and a chat can post twice before the first send returns. Neither
    # may stack a second popup or fall back to the tray beside the first.
    shown: list[tuple[str, str]] = []
    tray.showMessage = lambda title, body, *_a: shown.append((title, body))
    tray.isVisible = lambda: True
    popup.show(Notification(**{**note.__dict__, "text": "Or 9?"}))
    popup.show(Notification(**{**note.__dict__, "text": "Or 9?"}))
    popup.show(Notification(**{**note.__dict__, "text": "Or 9?"}))
    settle(2500)
    check(
        "a repeat, even mid-send, is still one popup",
        len(popup._by_phone) == 1 and popup._by_phone["n1"] == first and not popup._sending,
        f"{popup._by_phone} sending={popup._sending}",
    )
    check("and never a tray copy beside it", not shown, str(shown))

    sent: list[tuple[str, str]] = []
    hub.reply = lambda i, t: sent.append((i, t))          # type: ignore[method-assign]
    dismissed: list[str] = []
    hub.dismiss = lambda i: dismissed.append(i)           # type: ignore[method-assign]

    popup._on_replied(first, "on my way")
    check(
        "a reply typed into the popup goes to that conversation",
        sent == [("n1", "on my way")],
        str(sent),
    )
    popup._on_replied(first, "   ")
    check("an empty reply is not sent", len(sent) == 1)

    popup._on_action(first, notify.DISMISS)
    check("the dismiss action clears it on the phone", dismissed == ["n1"], str(dismissed))

    opened: list[str] = []
    popup.opened.connect(opened.append)
    popup._on_action(first, notify.OPEN)
    check("clicking the popup asks the window to show it", opened == ["n1"])

    # A notification cleared on the phone must not leave a popup offering to
    # reply to something that is gone.
    popup._prune()
    settle(300)
    check("a popup for a gone notification is withdrawn", not popup._by_phone,
          str(popup._by_phone))

    # Silence must be honoured whichever route is in use.
    hub.config.features.notification_popups = False
    check("switched off, nothing is raised", not popup.wanted(note))
    hub.config.features.notification_popups = True
    hub.config.dnd.mode = "phone_to_desktop"
    hub._phone_dnd = "priority"
    check("a silenced phone silences the desktop", not popup.wanted(note))
    hub._phone_dnd = "off"
    check("and an unsilenced one does not", popup.wanted(note))

    for given in list(popup._live):
        popup.notifier.close(given)
    settle(300)


def bursts() -> None:
    """Connecting brings everything the phone holds; only news pops up."""
    import time

    hub = Hub(Config())
    arrived: list[str] = []
    hub.notificationArrived.connect(lambda n: arrived.append(n.id))
    hub._add(Notification(id="old", app="Mail", title="Yesterday", when=time.time() - 3600))
    check("an old notification sent on connect is listed", "old" in {n.id for n in hub.notifications})
    check("but does not pop up", "old" not in arrived, str(arrived))
    hub._add(Notification(id="new", app="Mail", title="Now"))
    check("a new one does", "new" in arrived, str(arrived))

    tray = QSystemTrayIcon()
    popup = Popups(hub, tray)
    shown: list[tuple[str, str]] = []
    tray.showMessage = lambda title, body, *_a: shown.append((title, body))
    tray.isVisible = lambda: True
    for index in range(3):
        popup._show_tray(Notification(id=f"b{index}", app="WhatsApp", title=f"Chat {index}"))
    settle(popups_module.TRAY_GATHER_MS + 300)
    check("a burst through the tray is one message, not a flicker", len(shown) == 1, str(shown))
    check("which says how many", bool(shown) and shown[0][0] == "3 new notifications", str(shown))
    shown.clear()
    popup._show_tray(Notification(id="one", app="WhatsApp", title="Aai", text="Dinner?"))
    settle(popups_module.TRAY_GATHER_MS + 300)
    check("a single one is shown as itself", shown == [("WhatsApp: Aai", "Dinner?")], str(shown))


def main() -> int:
    app = QApplication(sys.argv)

    print("-- the text gdbus reads")
    gvariant()
    print("\n-- the desktop's notification server")
    server()
    print("\n-- the app's popups")
    popups()
    print("\n-- bursts")
    bursts()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed:")
        for name in FAILURES:
            print(f"  - {name}")
        return 1
    print("all notification checks passed")
    app.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
