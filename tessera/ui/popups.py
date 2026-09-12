"""Repeating the phone's notifications on this desktop.

Through the tray icon, which is the one notification route Qt offers on every
platform: libnotify on Linux, a real toast on Windows. That matters most on
Windows, where the phone's notifications appearing on screen is the whole point
of the app being open.

The phone's Do Not Disturb is honoured: a phone that is silenced silences its
echo here too, which on Windows is the only "silence the desktop" anyone can
offer -- Focus Assist cannot be set by another program.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QSystemTrayIcon

from ..backends import silence
from ..core.hub import Hub
from ..core.models import Notification

log = logging.getLogger(__name__)

#: How long a popup stays up. Long enough to read a message, short enough not
#: to stack up when a group chat is busy.
DURATION_MS = 6000

#: Notifications from these are noise on a desktop: they are about the phone
#: talking to this computer, which the app already shows.
QUIET_PACKAGES = frozenset({
    "dev.tessera.companion",
    "com.android.systemui",
})


class Popups(QObject):
    """Raises a desktop notification for each one that arrives."""

    def __init__(self, hub: Hub, tray: QSystemTrayIcon,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.hub = hub
        self.tray = tray
        hub.notificationArrived.connect(self.show)

    def wanted(self, note: Notification) -> bool:
        """Whether this one should appear on the desktop."""
        config = self.hub.config
        if not config.notification_popups or not config.features.notifications:
            return False
        if note.package in QUIET_PACKAGES:
            return False
        # The phone is silenced, and the sync says its state applies here.
        if config.dnd.mode in ("phone_to_desktop", "two_way"):
            if self.hub.phone_dnd not in ("", "off"):
                return False
        if silence.own_popups_silenced():
            return False
        return bool(note.title or note.text)

    def show(self, note: Notification) -> None:
        if not self.wanted(note) or not self.tray.isVisible():
            return
        title = note.app or "Phone"
        if note.title:
            title = f"{title}: {note.title}"
        try:
            self.tray.showMessage(
                title[:120],
                (note.text or "")[:400],
                QSystemTrayIcon.MessageIcon.Information,
                DURATION_MS,
            )
        except Exception as exc:      # a tray that went away mid-call
            log.debug("could not show a popup: %s", exc)
