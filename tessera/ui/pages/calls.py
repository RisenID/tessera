"""Calls: answer or decline from the computer, and see recent calls."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication, QIcon
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
from ..theme import SPACE, Palette
from ..widgets import Avatar, Card, EmptyState, Pill, Toast, header_row, heading

#: How each kind of call reads in the list, and the colour it earns.
KINDS = {
    "incoming": ("Incoming", "muted"),
    "outgoing": ("Outgoing", "muted"),
    "missed": ("Missed", "danger"),
    "rejected": ("Declined", "warning"),
    "blocked": ("Blocked", "warning"),
    "voicemail": ("Voicemail", "muted"),
}


def _when(millis: int) -> str:
    if not millis:
        return ""
    moment = datetime.fromtimestamp(millis / 1000)
    now = datetime.now()
    if moment.date() == now.date():
        return moment.strftime("%H:%M")
    if (now.date() - moment.date()).days == 1:
        return f"Yesterday {moment.strftime('%H:%M')}"
    if moment.year == now.year:
        return moment.strftime("%d %b, %H:%M")
    return moment.strftime("%d %b %Y")


def _duration(seconds: int) -> str:
    if not seconds:
        return ""
    minutes, remainder = divmod(int(seconds), 60)
    if minutes >= 60:
        hours, minutes = divmod(minutes, 60)
        return f"{hours}h {minutes}m"
    return f"{minutes}m {remainder}s" if minutes else f"{remainder}s"


class CallRow(Card):
    """One past call."""

    def __init__(self, entry: dict, palette: Palette, on_dial, parent=None):
        super().__init__(parent, flat=True, padding=SPACE["md"])
        number = entry.get("number", "")
        name = entry.get("name") or number or "Unknown"
        kind_label, tone = KINDS.get(entry.get("kind", ""), ("Call", "muted"))

        row = QHBoxLayout()
        row.setSpacing(SPACE["md"])
        row.addWidget(Avatar(name, 36), 0, Qt.AlignmentFlag.AlignVCenter)

        text = QVBoxLayout()
        text.setSpacing(0)
        title = QLabel(name)
        title.setStyleSheet("font-weight: 650;")
        text.addWidget(title)

        detail = ", ".join(
            part for part in (kind_label, _when(entry.get("time", 0)),
                              _duration(entry.get("duration", 0))) if part
        )
        subtitle = QLabel(detail)
        subtitle.setStyleSheet(
            f"color: {palette.danger if tone == 'danger' else palette.muted}; font-size: 12px;"
        )
        text.addWidget(subtitle)
        row.addLayout(text, 1)

        if number:
            copy = QPushButton()
            copy.setObjectName("Ghost")
            copy.setToolTip(f"Copy {number}")
            copy.setCursor(Qt.CursorShape.PointingHandCursor)
            icon = QIcon.fromTheme("edit-copy")
            if icon.isNull():
                copy.setText("Copy")
            else:
                copy.setIcon(icon)
            copy.clicked.connect(
                lambda _c=False, value=number: QGuiApplication.clipboard().setText(value)
            )
            row.addWidget(copy, 0, Qt.AlignmentFlag.AlignVCenter)

            call = QPushButton("Call")
            call.setObjectName("Ghost")
            call.clicked.connect(lambda: on_dial(number))
            row.addWidget(call, 0, Qt.AlignmentFlag.AlignVCenter)

        self.body().addLayout(row)


class CallsPage(QWidget):
    """The live call, and the ones before it."""

    def __init__(self, hub: Hub, palette: Palette, parent: QWidget | None = None):
        super().__init__(parent)
        self.hub = hub
        self.palette_tokens = palette
        self._recent: list[dict] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(SPACE["xl"], SPACE["xl"], SPACE["xl"], SPACE["xl"])
        outer.setSpacing(SPACE["lg"])

        header = QHBoxLayout()
        header.addWidget(heading("Calls", "Answer or decline from here, and dial from your keyboard"), 1)
        refresh = QPushButton("Refresh")
        refresh.setObjectName("Ghost")
        refresh.clicked.connect(self.load)
        header.addWidget(refresh, 0, Qt.AlignmentFlag.AlignVCenter)
        outer.addWidget(header_row(header))

        # -- the live call ----------------------------------------------------
        self.live = Card(self)
        live_row = QHBoxLayout()
        live_row.setSpacing(SPACE["md"])
        self.live_avatar = Avatar("?", 52)
        live_row.addWidget(self.live_avatar, 0, Qt.AlignmentFlag.AlignVCenter)

        live_text = QVBoxLayout()
        live_text.setSpacing(2)
        self.live_name = QLabel()
        self.live_name.setStyleSheet("font-size: 18px; font-weight: 700;")
        live_text.addWidget(self.live_name)
        self.live_detail = QLabel()
        self.live_detail.setObjectName("Muted")
        live_text.addWidget(self.live_detail)
        live_row.addLayout(live_text, 1)

        self.state_pill = Pill("Idle", "muted")
        self.state_pill.apply(palette)
        live_row.addWidget(self.state_pill, 0, Qt.AlignmentFlag.AlignVCenter)
        self.live.body().addLayout(live_row)

        actions = QHBoxLayout()
        self.answer_button = QPushButton("Answer")
        self.answer_button.setObjectName("Primary")
        self.answer_button.clicked.connect(self._answer)
        actions.addWidget(self.answer_button)

        self.hangup_button = QPushButton("Hang up")
        self.hangup_button.setObjectName("Danger")
        self.hangup_button.clicked.connect(self._hang_up)
        actions.addWidget(self.hangup_button)
        actions.addStretch(1)
        self.live.body().addLayout(actions)

        self.live_note = QLabel()
        self.live_note.setObjectName("Muted")
        self.live_note.setWordWrap(True)
        self.live.add(self.live_note)
        outer.addWidget(self.live)

        # -- dial -------------------------------------------------------------
        dial = Card(self)
        dial_row = QHBoxLayout()
        self.number = QLineEdit()
        self.number.setPlaceholderText("Type a number to call")
        self.number.returnPressed.connect(self._dial_typed)
        dial_row.addWidget(self.number, 1)
        dial_button = QPushButton("Call")
        dial_button.setObjectName("Primary")
        dial_button.clicked.connect(self._dial_typed)
        dial_row.addWidget(dial_button)
        dial.body().addLayout(dial_row)
        outer.addWidget(dial)

        # -- history ----------------------------------------------------------
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        host = QWidget()
        self.list_layout = QVBoxLayout(host)
        self.list_layout.setContentsMargins(0, 0, SPACE["sm"], 0)
        self.list_layout.setSpacing(SPACE["sm"])
        self.list_layout.addStretch(1)
        self.scroll.setWidget(host)
        outer.addWidget(self.scroll, 1)

        self.empty = EmptyState(
            "📞",
            "No recent calls",
            "Connect the companion app and allow it to read your call log.",
            "Load calls",
        )
        self.empty.actionClicked.connect(self.load)
        outer.addWidget(self.empty)

        self.toast = Toast(self)
        hub.callChanged.connect(self._on_call)
        hub.connectionChanged.connect(lambda _c: self.load())
        self._on_call(hub.call)
        self.load()

    # -- live call -----------------------------------------------------------

    def _on_call(self, call: dict) -> None:
        state = call.get("state", "idle")
        name = call.get("name") or "Unknown caller"
        controllable = bool(call.get("canControl", True))

        if state == "ringing":
            self.state_pill.set_state("Ringing", "accent")
            self.live_name.setText(name)
            self.live_detail.setText(call.get("detail", "") or "Incoming call")
            self.live_avatar.set_text(name)
            self.answer_button.setVisible(True)
            self.hangup_button.setText("Decline")
            self.hangup_button.setVisible(True)
        elif state == "active":
            self.state_pill.set_state("In call", "success")
            self.live_name.setText(name)
            self.live_detail.setText(call.get("detail", "") or "Call in progress")
            self.live_avatar.set_text(name)
            self.answer_button.setVisible(False)
            self.hangup_button.setText("Hang up")
            self.hangup_button.setVisible(True)
        else:
            self.state_pill.set_state("No call", "muted")
            self.live_name.setText("No call in progress")
            self.live_detail.setText("Incoming calls appear here.")
            self.live_avatar.set_text("?")
            self.answer_button.setVisible(False)
            self.hangup_button.setVisible(False)
            # The log only gains an entry once a call ends.
            QTimer.singleShot(1500, self.load)

        if state != "idle" and not controllable:
            self.live_note.setText(
                "Tessera can see this call but cannot answer it: grant it "
                "permission to answer calls on the phone."
            )
        elif state != "idle" and not self.hub.config.features.bluetooth_audio:
            self.live_note.setText(
                "Call audio stays on the phone — switch on Calls and music in "
                "Settings to route it here over Bluetooth."
            )
        else:
            self.live_note.setText("")

    def _answer(self) -> None:
        self.hub.companion.request({"t": "call_answer"}, self._on_action)

    def _hang_up(self) -> None:
        self.hub.companion.request({"t": "call_end"}, self._on_action)

    def _on_action(self, reply: dict) -> None:
        if reply.get("t") == "error":
            self.toast.show_message(
                reply.get("message", "That did not work")[:120],
                self.palette_tokens,
                "danger",
            )

    # -- dialling ------------------------------------------------------------

    def _dial_typed(self) -> None:
        number = self.number.text().strip()
        if number:
            self._dial(number)
            self.number.clear()

    def _dial(self, number: str) -> None:
        if not self.hub.companion.connected:
            self.toast.show_message("No phone connected", self.palette_tokens, "danger")
            return
        self.hub.companion.request(
            {"t": "call_dial", "number": number},
            lambda reply: self._on_dialled(number, reply),
        )

    def _on_dialled(self, number: str, reply: dict) -> None:
        if reply.get("t") == "error":
            self.toast.show_message(reply.get("message", "")[:120], self.palette_tokens, "danger")
            return
        note = reply.get("note") or f"Calling {number}"
        self.toast.show_message(note[:120], self.palette_tokens, "success")

    # -- history -------------------------------------------------------------

    def load(self) -> None:
        if not self.hub.companion.connected:
            self._render([])
            return
        self.hub.companion.request(
            {"t": "calls_recent", "limit": 60},
            lambda reply: self._render(reply.get("items", [])),
        )

    def _render(self, items: list) -> None:
        self._recent = items
        for index in reversed(range(self.list_layout.count() - 1)):
            entry = self.list_layout.takeAt(index)
            widget = entry.widget() if entry else None
            if widget is not None:
                widget.deleteLater()

        for call in items:
            self.list_layout.insertWidget(
                self.list_layout.count() - 1,
                CallRow(call, self.palette_tokens, self._dial),
            )

        self.scroll.setVisible(bool(items))
        self.empty.setVisible(not items)
        if not items and self.hub.companion.connected:
            self.empty.update_text(
                "No recent calls",
                "Nothing in the phone's call log, or Tessera has not been "
                "allowed to read it.",
            )

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.toast._reposition()
