#!/usr/bin/env python3
"""Checks the desktop-notification path.

Raises real notifications on whatever server this desktop runs, then closes
them, so nothing is left on screen. What cannot be checked without a person is
someone typing into the reply box; everything up to and including "the server
told us the notification closed" is.

Run it from the repository root:  python3 scripts/check-notifications.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QTimer                      # noqa: E402
from PySide6.QtWidgets import QApplication, QSystemTrayIcon        # noqa: E402

from tessera.backends import notify                                # noqa: E402
from tessera.core.config import Config                             # noqa: E402
from tessera.core.hub import Hub                                   # noqa: E402
from tessera.core.models import Notification                       # noqa: E402
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
    check("an arriving notification raises a popup", popup._by_phone.get("n1", 0) > 0)

    first = popup._by_phone["n1"]
    popup.show(Notification(**{**note.__dict__, "text": "Or 9?"}))
    check(
        "a second message replaces its own popup rather than stacking",
        len(popup._by_phone) == 1 and popup._by_phone["n1"] == first,
        str(popup._by_phone),
    )

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
    hub.config.notification_popups = False
    check("switched off, nothing is raised", not popup.wanted(note))
    hub.config.notification_popups = True
    hub.config.dnd.mode = "phone_to_desktop"
    hub._phone_dnd = "priority"
    check("a silenced phone silences the desktop", not popup.wanted(note))
    hub._phone_dnd = "off"
    check("and an unsilenced one does not", popup.wanted(note))

    for given in list(popup._live):
        popup.notifier.close(given)
    settle(300)


def main() -> int:
    app = QApplication(sys.argv)

    print("-- the text gdbus reads")
    gvariant()
    print("\n-- the desktop's notification server")
    server()
    print("\n-- the app's popups")
    popups()

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
