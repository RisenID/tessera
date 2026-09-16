"""Notification mirror, with a one-time passcode strip pinned to the top."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...core.hub import Hub
from ...core.models import Notification
from ..theme import SPACE, Palette
from ..widgets import (
    header_row,
    Avatar,
    Card,
    EmptyState,
    OtpCard,
    Pill,
    Toast,
    divider,
    ghost_button,
    heading,
)


class NotificationCard(Card):
    """One notification, with dismiss and inline reply where available."""

    dismissed = Signal(str)
    replied = Signal(str, str)

    def __init__(
        self,
        note: Notification,
        palette: Palette,
        icons=None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent, flat=True, padding=SPACE["md"])
        self.note = note

        top = QHBoxLayout()
        top.setSpacing(SPACE["md"])

        # Fall back to initials until the phone sends the real icon.
        self.avatar = Avatar(note.app or "?", 38)
        if icons is not None and note.package:
            pixmap = icons.get(note.package)
            if pixmap is not None:
                self.avatar.set_pixmap_rounded(pixmap)
        top.addWidget(self.avatar, 0, Qt.AlignmentFlag.AlignTop)

        body = QVBoxLayout()
        body.setSpacing(2)

        header = QHBoxLayout()
        app = QLabel(note.app or note.package or "Notification")
        app.setStyleSheet("font-weight: 650;")
        header.addWidget(app)
        header.addStretch(1)
        self.when_label = QLabel(note.time_text)
        self.when_label.setObjectName("Muted")
        self.when_label.setStyleSheet(f"color: {palette.muted}; font-size: 12px;")
        header.addWidget(self.when_label)
        body.addLayout(header)

        if note.title:
            title = QLabel(note.title)
            title.setWordWrap(True)
            title.setStyleSheet("font-weight: 600;")
            body.addWidget(title)

        if note.text:
            text = QLabel(note.text)
            text.setWordWrap(True)
            text.setObjectName("Muted")
            text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            body.addWidget(text)

        actions = QHBoxLayout()
        actions.setSpacing(SPACE["sm"])
        actions.addStretch(1)

        if note.repliable:
            self.reply_box = QLineEdit()
            self.reply_box.setPlaceholderText("Reply...")
            self.reply_box.returnPressed.connect(self._send_reply)
            body.addSpacing(SPACE["xs"])
            body.addWidget(self.reply_box)

            send = QPushButton("Send")
            send.setObjectName("Primary")
            send.clicked.connect(self._send_reply)
            actions.addWidget(send)
        else:
            self.reply_box = None

        if note.clearable:
            dismiss = QPushButton("Dismiss")
            dismiss.setObjectName("Ghost")
            dismiss.setCursor(Qt.CursorShape.PointingHandCursor)
            dismiss.clicked.connect(lambda: self.dismissed.emit(note.id))
            actions.addWidget(dismiss)

        body.addLayout(actions)
        top.addLayout(body, 1)
        self.body().addLayout(top)

    def _send_reply(self) -> None:
        if self.reply_box is None:
            return
        text = self.reply_box.text().strip()
        if text:
            self.replied.emit(self.note.id, text)
            self.reply_box.clear()


class NotificationsPage(QWidget):
    """The default page: everything happening on the phone right now."""

    def __init__(self, hub: Hub, palette: Palette, parent: QWidget | None = None):
        super().__init__(parent)
        self.hub = hub
        self.palette_tokens = palette

        outer = QVBoxLayout(self)
        outer.setContentsMargins(SPACE["xl"], SPACE["xl"], SPACE["xl"], SPACE["xl"])
        outer.setSpacing(SPACE["lg"])

        header = QHBoxLayout()
        header.addWidget(heading("Notifications", "Mirrored from your phone as they arrive"), 1)

        self.count_pill = Pill("0", "muted")
        self.count_pill.apply(palette)
        header.addWidget(self.count_pill, 0, Qt.AlignmentFlag.AlignVCenter)

        refresh = ghost_button("Refresh", "view-refresh")
        refresh.clicked.connect(self.hub.refresh_notifications)
        header.addWidget(refresh, 0, Qt.AlignmentFlag.AlignVCenter)

        clear = ghost_button("Dismiss all", "edit-clear-all")
        clear.clicked.connect(self._dismiss_all)
        header.addWidget(clear, 0, Qt.AlignmentFlag.AlignVCenter)
        outer.addWidget(header_row(header))

        # -- passcode strip --------------------------------------------------
        self.otp_section = QWidget()
        otp_layout = QVBoxLayout(self.otp_section)
        otp_layout.setContentsMargins(0, 0, 0, 0)
        otp_layout.setSpacing(SPACE["sm"])

        label = QLabel("One-time passcodes")
        label.setObjectName("SectionTitle")
        otp_layout.addWidget(label)

        self.otp_container = QVBoxLayout()
        self.otp_container.setSpacing(SPACE["sm"])
        otp_layout.addLayout(self.otp_container)
        otp_layout.addWidget(divider())
        outer.addWidget(self.otp_section)
        self.otp_section.setVisible(False)

        # -- the list --------------------------------------------------------
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.list_host = QWidget()
        self.list_layout = QVBoxLayout(self.list_host)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(SPACE["sm"])
        self.list_layout.addStretch(1)
        self.scroll.setWidget(self.list_host)
        outer.addWidget(self.scroll, 1)

        self.empty = EmptyState(
            "🔔",
            "No notifications",
            "Anything that arrives on your phone will show up here.",
        )
        outer.addWidget(self.empty)

        self.toast = Toast(self)

        self._cards: dict[str, NotificationCard] = {}
        self._stale = True
        self._otp_shown: list[tuple[str, str]] = []
        # One rebuild per burst: connecting sends every notification at once.
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(60)
        self._refresh_timer.timeout.connect(self.refresh)
        hub.notificationsChanged.connect(self._refresh_timer.start)
        hub.icons.iconReady.connect(self._on_icon)
        hub.otpArrived.connect(self._on_otp)
        self.refresh()

    # -- rendering -----------------------------------------------------------

    def showEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().showEvent(event)
        if self._stale:
            self.refresh()

    def refresh(self) -> None:
        # A hidden page is rebuilt once when it is next shown, not per burst.
        if not self.isVisible():
            self._stale = True
            return
        self._stale = False
        notifications = self.hub.notifications
        self.count_pill.set_state(str(len(notifications)), "accent" if notifications else "muted")

        # Keep the cards that are unchanged: connecting resends every one.
        wanted = {note.id: note for note in notifications}
        for note_id, card in list(self._cards.items()):
            note = wanted.get(note_id)
            if note is not None and note.when == card.note.when and note.text == card.note.text:
                card.when_label.setText(note.time_text)     # "just now" ages
                continue
            self.list_layout.removeWidget(card)
            card.hide()
            card.deleteLater()
            del self._cards[note_id]
        for position, note in enumerate(notifications):
            card = self._cards.get(note.id)
            if card is None:
                card = NotificationCard(note, self.palette_tokens, self.hub.icons)
                card.dismissed.connect(self.hub.dismiss)
                card.replied.connect(self._reply)
                self._cards[note.id] = card
            elif self.list_layout.indexOf(card) == position:
                continue
            else:
                self.list_layout.removeWidget(card)
            self.list_layout.insertWidget(position, card)

        has_any = bool(notifications)
        self.scroll.setVisible(has_any)
        self.empty.setVisible(not has_any)
        if not has_any and not self.hub.connected:
            self.empty.update_text(
                "No phone connected",
                "Pair the companion app, or connect your phone with KDE Connect, "
                "to see notifications here.",
            )
        self._refresh_otp()

    def _refresh_otp(self) -> None:
        codes = self.hub.recent_codes(limit=3)
        shown = [(match.code, note.id) for match, note in codes]
        if shown == self._otp_shown:
            return
        self._otp_shown = shown
        self._clear(self.otp_container)
        for match, note in codes:
            card = OtpCard(match.code, f"{note.app} · {note.time_text}", self.palette_tokens)
            card.copied.connect(self._on_copied)
            self.otp_container.addWidget(card)
        self.otp_section.setVisible(bool(codes))

    def _on_icon(self, package: str, pixmap) -> None:
        """Fill in an icon that arrived after its card was built."""
        for card in self._cards.values():
            if card.note.package == package:
                card.avatar.set_pixmap_rounded(pixmap)

    def _on_otp(self, match, note: Notification) -> None:
        self._refresh_otp()
        self.toast.show_message(
            f"Passcode from {note.app}: {match.code}", self.palette_tokens, "success"
        )

    def _on_copied(self, code: str) -> None:
        self.toast.show_message(f"Copied {code}", self.palette_tokens, "success")

    def _reply(self, notification_id: str, text: str) -> None:
        self.hub.reply(notification_id, text)
        self.toast.show_message("Reply sent", self.palette_tokens, "success")

    def _dismiss_all(self) -> None:
        for note in self.hub.notifications:
            if note.clearable:
                self.hub.dismiss(note.id)

    @staticmethod
    def _clear(layout, keep_stretch: bool = False) -> None:
        limit = layout.count() - (1 if keep_stretch else 0)
        for index in reversed(range(limit)):
            item = layout.takeAt(index)
            widget = item.widget() if item else None
            if widget is not None:
                widget.hide()
                widget.deleteLater()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        self.toast._reposition()
