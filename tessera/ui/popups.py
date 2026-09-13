"""Repeating the phone's notifications on this desktop."""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QSystemTrayIcon

from ..backends import notify, silence
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

#: Never track more than this many live popups. A busy group chat replaces its
#: own popup, so this only grows with the number of distinct conversations.
MAX_TRACKED = 64


class Popups(QObject):
    """Raises a desktop notification for each one that arrives."""

    #: The user pressed the popup itself rather than an action: show them the
    #: notification in the app.
    opened = Signal(str)              # the phone's notification id

    def __init__(self, hub: Hub, tray: QSystemTrayIcon,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.hub = hub
        self.tray = tray

        self.notifier = notify.Notifier(self)
        #: The desktop server's id -> the phone's notification id, so a reply
        #: typed into a popup reaches the right conversation.
        self._live: dict[int, str] = {}
        #: And back, so a second message in one chat replaces its own popup
        #: instead of stacking another identical one.
        self._by_phone: dict[str, int] = {}

        if self.notifier.available:
            self.notifier.replied.connect(self._on_replied)
            self.notifier.activated.connect(self._on_action)
            self.notifier.closed.connect(self._on_closed)
            log.info(
                "using the desktop's notification server%s",
                " with inline replies" if self.notifier.can_reply else "",
            )

        hub.notificationArrived.connect(self.show)
        hub.notificationsChanged.connect(self._prune)

    # -- what to show --------------------------------------------------------

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
        if not self.wanted(note):
            return
        if self.notifier.available:
            self._show_rich(note)
        else:
            self._show_tray(note)

    def _show_rich(self, note: Notification) -> None:
        """Through the desktop's own server, with something to press."""
        summary = note.title or note.app or "Phone"
        body = note.text or ""
        if note.title and note.app:
            # The app's name belongs somewhere: as a prefix in the body rather
            # than crowding the title, which is usually the sender.
            body = f"{body}\n{note.app}" if body else note.app

        given = self.notifier.send(
            summary[:120],
            body[:400],
            icon=self._icon_for(note),
            replace=self._by_phone.get(note.id, 0),
            repliable=note.repliable,
            clearable=note.clearable,
            reply_placeholder=f"Reply to {note.title}" if note.title else "Reply",
        )
        if not given:
            self._show_tray(note)
            return
        self._live[given] = note.id
        self._by_phone[note.id] = given
        self._trim()

    def _show_tray(self, note: Notification) -> None:
        if not self.tray.isVisible():
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

    def _icon_for(self, note: Notification) -> str:
        """The sending app's own icon, where the phone has sent us one."""
        if not note.package:
            return ""
        path = self.hub.icons.path_for(note.package)
        return str(path) if path else ""

    # -- what the user pressed -----------------------------------------------

    def _on_replied(self, given: int, text: str) -> None:
        phone_id = self._live.get(given)
        if not phone_id or not text.strip():
            return
        log.info("replying to %s from the popup", phone_id)
        self.hub.reply(phone_id, text)

    def _on_action(self, given: int, key: str) -> None:
        phone_id = self._live.get(given)
        if not phone_id:
            return
        if key == notify.DISMISS:
            self.hub.dismiss(phone_id)
        elif key == notify.OPEN:
            self.opened.emit(phone_id)

    def _on_closed(self, given: int) -> None:
        phone_id = self._live.pop(given, None)
        if phone_id is not None and self._by_phone.get(phone_id) == given:
            self._by_phone.pop(phone_id, None)

    def _prune(self) -> None:
        """Drop popups for notifications the phone no longer has."""
        current = {note.id for note in self.hub.notifications}
        for phone_id in list(self._by_phone):
            if phone_id in current:
                continue
            given = self._by_phone.pop(phone_id, 0)
            self._live.pop(given, None)
            self.notifier.close(given)

    def _trim(self) -> None:
        while len(self._live) > MAX_TRACKED:
            given = next(iter(self._live))
            phone_id = self._live.pop(given, None)
            if phone_id is not None:
                self._by_phone.pop(phone_id, None)
