"""Calls: answer or decline from the computer, and see recent calls."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...core.hub import Hub
from ..theme import SPACE, Palette
from ..widgets import Avatar, Card, EmptyState, Pill, Toast, header_row, heading

#: Contacts listed under the dial box at most; the search narrows it.
MAX_MATCHES = 8

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
        self._contacts: list[dict] = []

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

        # Mute and loudspeaker: the phone's own audio switches, driven from here.
        self.mute_button = QPushButton("Mute")
        self.mute_button.setCheckable(True)
        self.mute_button.clicked.connect(lambda on: self._audio("call_mute", on))
        actions.addWidget(self.mute_button)
        self.speaker_button = QPushButton("Speaker")
        self.speaker_button.setCheckable(True)
        self.speaker_button.clicked.connect(lambda on: self._audio("call_speaker", on))
        actions.addWidget(self.speaker_button)
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
        self.number.setPlaceholderText("Type a name or a number")
        self.number.returnPressed.connect(self._dial_typed)
        self.number.textChanged.connect(self._filter_contacts)
        dial_row.addWidget(self.number, 1)
        keypad_button = QPushButton("Keypad")
        keypad_button.setObjectName("Ghost")
        keypad_button.setCheckable(True)
        keypad_button.toggled.connect(lambda on: self.keypad.setVisible(on))
        dial_row.addWidget(keypad_button)
        dial_button = QPushButton("Call")
        dial_button.setObjectName("Primary")
        dial_button.clicked.connect(self._dial_typed)
        dial_row.addWidget(dial_button)
        dial.body().addLayout(dial_row)

        # Contacts that match what was typed; a click dials them.
        self.matches = QListWidget()
        self.matches.setMaximumHeight(180)
        self.matches.setVisible(False)
        self.matches.itemClicked.connect(self._dial_match)
        dial.add(self.matches)

        self.keypad = self._build_keypad()
        self.keypad.setVisible(False)
        dial.add(self.keypad)

        self.contacts_note = QLabel()
        self.contacts_note.setObjectName("Muted")
        self.contacts_note.setWordWrap(True)
        self.contacts_note.setVisible(False)
        dial.add(self.contacts_note)
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
        # Takes the list's space when the list is hidden, not the cards.
        outer.addWidget(self.empty, 1)

        self.toast = Toast(self)
        hub.callChanged.connect(self._on_call)
        hub.connectionChanged.connect(lambda _c: self.load())
        hub.companion.capabilitiesChanged.connect(lambda _c: self.load_contacts())
        self._on_call(hub.call)
        self.load()

    # -- contacts --------------------------------------------------------------

    def _build_keypad(self) -> QWidget:
        pad = QWidget()
        grid = QGridLayout(pad)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(SPACE["xs"])
        for index, key in enumerate("123456789*0#"):
            button = QPushButton(key)
            button.setFixedSize(56, 40)
            button.clicked.connect(lambda _c=False, k=key: self.number.insert(k))
            grid.addWidget(button, index // 3, index % 3)
        grid.setColumnStretch(3, 1)
        return pad

    def load_contacts(self) -> None:
        if not self.hub.companion.connected or not self.hub.companion.supports("contacts"):
            self._contacts = []
            self.contacts_note.setVisible(
                self.hub.companion.connected and bool(self.hub.companion.capabilities)
            )
            self.contacts_note.setText(
                "Allow Contacts in the companion app to search and call people by name."
            )
            return
        self.contacts_note.setVisible(False)
        self.hub.companion.request(
            {"t": "contacts_list"}, lambda reply: self._on_contacts(reply.get("items", []))
        )

    def _on_contacts(self, items: list) -> None:
        self._contacts = [c for c in items if isinstance(c, dict) and c.get("number")]
        self._filter_contacts(self.number.text())

    def _filter_contacts(self, text: str) -> None:
        needle = text.strip().lower()
        digits = "".join(ch for ch in needle if ch.isdigit())
        self.matches.clear()
        if not needle or not self._contacts:
            self.matches.setVisible(False)
            return
        shown = 0
        for contact in self._contacts:
            name = str(contact.get("name", ""))
            number = str(contact.get("number", ""))
            if needle in name.lower() or (digits and digits in "".join(filter(str.isdigit, number))):
                kind = str(contact.get("type", ""))
                item = QListWidgetItem(f"{name}  ·  {number}" + (f"  ({kind})" if kind else ""))
                item.setData(Qt.ItemDataRole.UserRole, number)
                self.matches.addItem(item)
                shown += 1
                if shown >= MAX_MATCHES:
                    break
        self.matches.setVisible(shown > 0)

    def _dial_match(self, item: QListWidgetItem) -> None:
        number = str(item.data(Qt.ItemDataRole.UserRole) or "")
        if number:
            self._dial(number)
            self.number.clear()

    # -- live call -----------------------------------------------------------

    def _audio(self, command: str, on: bool) -> None:
        if not self.hub.companion.supports("call_audio"):
            self.toast.show_message("The companion app is too old for this", self.palette_tokens, "warning")
            return
        self.hub.companion.request({"t": command, "on": bool(on)}, self._on_audio)

    def _on_audio(self, reply: dict) -> None:
        if reply.get("t") == "error":
            self.toast.show_message(reply.get("message", "")[:120], self.palette_tokens, "danger")
        for button, key in ((self.mute_button, "muted"), (self.speaker_button, "speaker")):
            if key in reply:
                button.blockSignals(True)
                button.setChecked(bool(reply[key]))
                button.blockSignals(False)

    def _on_call(self, call: dict) -> None:
        state = call.get("state", "idle")
        name = call.get("name") or "Unknown caller"
        controllable = bool(call.get("canControl", True))

        audio = state == "active" and self.hub.companion.supports("call_audio")
        self.mute_button.setVisible(audio)
        self.speaker_button.setVisible(audio)
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
            if audio:
                self.hub.companion.request({"t": "call_audio"}, self._on_audio)
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
        self.load_contacts()
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
