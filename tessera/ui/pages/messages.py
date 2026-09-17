"""SMS conversations."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtWidgets import (
    QHBoxLayout,
    QSizePolicy,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ...core import otp
from ...core.hub import Hub
from ..theme import RADIUS, SPACE, Palette
from ..widgets import EmptyState, Toast, header_row, heading

#: How wide a message may get before it wraps. Long lines are hard to read, and
#: a bubble spanning the whole window stops looking like a message.
MAX_BUBBLE_WIDTH = 460
#: Horizontal padding plus a little slack, so the cap is measured fairly.
BUBBLE_PADDING = 52
#: Width available to a conversation preview once the row's padding is removed.
PREVIEW_WIDTH = 215


class ThreadRow(QWidget):
    """One conversation in the list: who it is, and the latest line."""

    def __init__(self, name: str, preview: str, when: int, unread: bool, palette: Palette, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE["md"], SPACE["sm"], SPACE["md"], SPACE["sm"])
        layout.setSpacing(2)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)

        title = QLabel(name)
        title.setStyleSheet(
            f"font-weight: {'700' if unread else '600'}; font-size: 13px;"
            f"color: {palette.text};"
        )
        top.addWidget(title, 1)

        if when:
            stamp = QLabel(_short_time(when))
            stamp.setStyleSheet(f"color: {palette.muted}; font-size: 11px;")
            top.addWidget(stamp, 0, Qt.AlignmentFlag.AlignRight)
        layout.addLayout(top)

        body = QLabel()
        body.setStyleSheet(
            f"color: {palette.text if unread else palette.muted}; font-size: 12px;"
        )
        body.ensurePolished()
        # Elide rather than clip: a cut-off word looks like a rendering fault,
        # an ellipsis reads as intentional.
        body.setText(
            body.fontMetrics().elidedText(
                " ".join(preview.split()), Qt.TextElideMode.ElideRight, PREVIEW_WIDTH
            )
        )
        layout.addWidget(body)


def _short_time(when: int) -> str:
    moment = datetime.fromtimestamp(when / 1000)
    now = datetime.now()
    if moment.date() == now.date():
        return moment.strftime("%H:%M")
    if moment.year == now.year:
        return moment.strftime("%d %b")
    return moment.strftime("%d/%m/%y")


class DaySeparator(QWidget):
    """A dated divider between messages sent on different days."""

    def __init__(self, when: datetime, palette: Palette, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, SPACE["md"], 0, SPACE["sm"])

        today = datetime.now().date()
        if when.date() == today:
            text = "Today"
        elif (today - when.date()).days == 1:
            text = "Yesterday"
        elif when.year == today.year:
            text = when.strftime("%A, %d %B")
        else:
            text = when.strftime("%d %B %Y")

        label = QLabel(text)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet(
            f"color: {palette.muted}; font-size: 11px; font-weight: 600;"
            f"background: {palette.surface_alt}; border-radius: {RADIUS['pill']}px;"
            "padding: 3px 12px;"
        )
        layout.addStretch(1)
        layout.addWidget(label)
        layout.addStretch(1)


def _copy_code(code: str) -> None:
    QGuiApplication.clipboard().setText(code)


class Bubble(QWidget):
    """One message, aligned by direction."""

    def __init__(
        self,
        text: str,
        when: int,
        outgoing: bool,
        palette: Palette,
        show_time: bool = True,
        code: str = "",
        grouped: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        # The gap above each message carries the grouping: tight within a run
        # from one sender, wide when the sender changes.
        outer.setContentsMargins(0, 2 if grouped else SPACE["md"], 0, 0)
        outer.setSpacing(2)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)

        bubble = QLabel(text)
        bubble.setWordWrap(True)
        bubble.setMaximumWidth(MAX_BUBBLE_WIDTH)
        bubble.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        # A word-wrapped label reports a minimal width hint, so without this
        # a short message collapses into a sliver and a long one wraps every
        # two or three words.
        bubble.ensurePolished()
        metrics = bubble.fontMetrics()
        longest = max(
            (metrics.horizontalAdvance(line) for line in text.splitlines()),
            default=0,
        )
        natural = longest + BUBBLE_PADDING
        bubble.setMinimumWidth(min(natural, MAX_BUBBLE_WIDTH))
        bubble.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Minimum)

        background = palette.accent if outgoing else palette.surface_alt
        colour = palette.accent_text if outgoing else palette.text
        radius = RADIUS["lg"]
        # Square off the corner nearest the previous bubble in a run, so a
        # group reads as one block and separate messages read as separate.
        tail = "4px" if grouped else f"{radius}px"
        corners = (
            f"border-radius: {radius}px {radius}px {tail} {radius}px;"
            if outgoing
            else f"border-radius: {radius}px {radius}px {radius}px {tail};"
        )
        bubble.setStyleSheet(
            f"background: {background}; color: {colour}; padding: 10px 14px; {corners}"
        )
        bubble.setToolTip(
            datetime.fromtimestamp(when / 1000).strftime("%d %b %Y, %H:%M") if when else ""
        )

        if outgoing:
            row.addStretch(1)
        row.addWidget(bubble)
        if not outgoing:
            row.addStretch(1)
        outer.addLayout(row)

        # A passcode in the message itself is copyable here, not only from the
        # notifications page -- the same code, the same click, wherever you
        # happen to be reading it.
        if code:
            copy_row = QHBoxLayout()
            copy_row.setContentsMargins(0, 2, 0, 0)
            copy = QPushButton(f"Copy {code}")
            copy.setObjectName("Ghost")
            copy.setCursor(Qt.CursorShape.PointingHandCursor)
            icon = QIcon.fromTheme("edit-copy")
            if not icon.isNull():
                copy.setIcon(icon)
            copy.clicked.connect(lambda _c=False, value=code: _copy_code(value))
            if outgoing:
                copy_row.addStretch(1)
            copy_row.addWidget(copy)
            if not outgoing:
                copy_row.addStretch(1)
            outer.addLayout(copy_row)

        if show_time and when:
            stamp = QLabel(datetime.fromtimestamp(when / 1000).strftime("%H:%M"))
            stamp.setStyleSheet(f"color: {palette.muted}; font-size: 11px;")
            stamp.setAlignment(
                Qt.AlignmentFlag.AlignRight if outgoing else Qt.AlignmentFlag.AlignLeft
            )
            outer.addWidget(stamp)


class MessagesPage(QWidget):
    def __init__(self, hub: Hub, palette: Palette, parent: QWidget | None = None):
        super().__init__(parent)
        self.hub = hub
        self.palette_tokens = palette
        self._threads: list[dict] = []
        self._current: dict | None = None
        #: Set while the thread list is being rebuilt, so the selection Qt
        #: moves around during the rebuild is not mistaken for the user picking
        #: a different conversation.
        self._rebuilding = False
        #: True when the transcript is scrolled to the newest message, which is
        #: the only case where a refresh should scroll it again.
        self._at_latest = True

        # A text arrives as a notification long before anything asks the phone
        # for it, so the page reloads on that rather than polling.
        self._pending = QTimer(self)
        self._pending.setSingleShot(True)
        self._pending.setInterval(700)
        self._pending.timeout.connect(self.load)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(SPACE["xl"], SPACE["xl"], SPACE["xl"], SPACE["xl"])
        outer.setSpacing(SPACE["lg"])

        header = QHBoxLayout()
        header.addWidget(heading("Messages", "Read and reply to texts from your computer"), 1)
        refresh = QPushButton("Refresh")
        refresh.setObjectName("Ghost")
        refresh.clicked.connect(self.load)
        header.addWidget(refresh, 0, Qt.AlignmentFlag.AlignVCenter)
        outer.addWidget(header_row(header))

        splitter = QSplitter(Qt.Orientation.Horizontal)

        self.threads = QListWidget()
        self.threads.setMinimumWidth(260)
        self.threads.setSpacing(2)
        self.threads.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.threads.setMaximumWidth(300)
        self.threads.setStyleSheet(
            # Separators between conversations, and a clear selected state.
            f"QListWidget::item {{ border-bottom: 1px solid {palette.border};"
            f" border-radius: {RADIUS['sm']}px; }}"
            f"QListWidget::item:selected {{ background: {palette.surface_hover};"
            f" border-bottom-color: transparent; }}"
        )
        self.threads.currentRowChanged.connect(self._open_thread)
        splitter.addWidget(self.threads)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(SPACE["md"], 0, 0, 0)

        self.title = QLabel("Pick a conversation")
        self.title.setObjectName("SectionTitle")
        right_layout.addWidget(self.title)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.transcript_host = QWidget()
        self.transcript = QVBoxLayout(self.transcript_host)
        self.transcript.setSpacing(0)
        self.transcript.setContentsMargins(SPACE["sm"], SPACE["sm"], SPACE["sm"], SPACE["sm"])
        # The stretch sits at the top so a short conversation rests just above
        # the composer rather than floating at the top of an empty panel.
        self.transcript.addStretch(1)
        self.scroll.setWidget(self.transcript_host)
        right_layout.addWidget(self.scroll, 1)

        compose = QHBoxLayout()
        self.entry = QLineEdit()
        self.entry.setPlaceholderText("Write a message...")
        self.entry.returnPressed.connect(self._send)
        compose.addWidget(self.entry, 1)

        send = QPushButton("Send")
        send.setObjectName("Primary")
        send.clicked.connect(self._send)
        compose.addWidget(send)
        right_layout.addLayout(compose)

        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([260, 640])
        outer.addWidget(splitter, 1)

        self.empty = EmptyState(
            "💬",
            "No messages",
            "Connect the companion app and grant it SMS access to see conversations.",
            "Load messages",
        )
        self.empty.actionClicked.connect(self.load)
        outer.addWidget(self.empty)
        self.splitter = splitter

        self.toast = Toast(self)
        hub.connectionChanged.connect(lambda _c: self.load())
        hub.textMessageArrived.connect(self._pending.start)
        self.load()

    def load(self) -> None:
        if not self.hub.companion.connected:
            self._render_threads([])
            return
        self.hub.companion.request(
            {"t": "sms_threads", "limit": 100},
            lambda reply: self._render_threads(reply.get("items", [])),
        )

    def _render_threads(self, items: list) -> None:
        # Rebuilding the list drops the selection, and a list that rebuilds
        # whenever a text arrives would throw the user out of the
        # conversation they are reading.
        open_thread = (self._current or {}).get("thread")

        self._threads = items
        self._rebuilding = True
        self.threads.clear()
        for thread in items:
            name = thread.get("name") or thread.get("address") or "Unknown"
            row = ThreadRow(
                name,
                thread.get("body") or "",
                thread.get("time", 0) or 0,
                not thread.get("read", True) and not thread.get("outgoing"),
                self.palette_tokens,
            )
            entry = QListWidgetItem(self.threads)
            entry.setSizeHint(row.sizeHint())
            self.threads.addItem(entry)
            self.threads.setItemWidget(entry, row)

        reopen = -1
        if open_thread:
            for row, thread in enumerate(items):
                if thread.get("thread") == open_thread:
                    self.threads.setCurrentRow(row)
                    reopen = row
                    break
        self._rebuilding = False

        # Re-read the conversation, which is how a newly arrived message
        # reaches the transcript.
        if reopen >= 0:
            self._open_thread(reopen)

        has_any = bool(items)
        self.splitter.setVisible(has_any)
        self.empty.setVisible(not has_any)

    def _open_thread(self, row: int) -> None:
        if self._rebuilding or row < 0 or row >= len(self._threads):
            return
        bar = self.scroll.verticalScrollBar()
        # Within a bubble's height of the end still counts as being at the end.
        self._at_latest = (
            self._current is None
            or self._threads[row].get("thread") != self._current.get("thread")
            or bar.value() >= bar.maximum() - 80
        )
        thread = self._threads[row]
        self._current = thread
        self.title.setText(thread.get("name") or thread.get("address") or "Conversation")
        self.hub.companion.request(
            {"t": "sms_messages", "thread": thread.get("thread", ""), "limit": 200},
            lambda reply: self._render_messages(reply.get("items", [])),
        )

    def _render_messages(self, items: list) -> None:
        for index in reversed(range(1, self.transcript.count())):
            entry = self.transcript.takeAt(index)
            widget = entry.widget() if entry else None
            if widget is not None:
                widget.deleteLater()

        # The phone returns newest first; a transcript reads oldest first.
        ordered = list(reversed(items))
        previous_day = None
        GROUP_WINDOW = 120  # seconds within which messages count as one run

        for index, message in enumerate(ordered):
            when = message.get("time", 0) or 0
            outgoing = bool(message.get("outgoing"))
            moment = datetime.fromtimestamp(when / 1000) if when else None

            if moment and moment.date() != previous_day:
                self.transcript.addWidget(DaySeparator(moment, self.palette_tokens))
                previous_day = moment.date()

            following = ordered[index + 1] if index + 1 < len(ordered) else None
            preceding = ordered[index - 1] if index else None

            def same_run(other, gap_from: int) -> bool:
                if other is None or bool(other.get("outgoing")) != outgoing:
                    return False
                other_when = other.get("time", 0) or 0
                if not when or not other_when:
                    return False
                return abs(other_when - gap_from) / 1000 <= GROUP_WINDOW

            grouped = same_run(preceding, when)
            # Only the final message of a run is stamped, so a burst of replies
            # is not repeated six times over.
            show_time = not same_run(following, when)

            body = message.get("body", "")
            # Only what arrived: a code you sent is not one you need back.
            found = None if outgoing else otp.find_code(body)
            self.transcript.addWidget(
                Bubble(
                    body,
                    when,
                    outgoing,
                    self.palette_tokens,
                    show_time=show_time,
                    grouped=grouped,
                    code=found.code if found else "",
                ),
            )

        # The newest message is the one worth showing, and the scroll area only
        # knows how tall the transcript is once the bubbles have been laid out
        # -- hence the deferral rather than setting the value here.
        if self._at_latest:
            QTimer.singleShot(0, self._scroll_to_latest)

    def _scroll_to_latest(self) -> None:
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())


    def _send(self) -> None:
        text = self.entry.text().strip()
        if not text or self._current is None:
            return
        if not self.hub.companion.supports("sms_send"):
            self.toast.show_message(
                "Grant SMS sending in the phone app first", self.palette_tokens, "warning"
            )
            return
        self.hub.companion.request(
            {"t": "sms_send", "address": self._current.get("address", ""), "text": text},
            self._on_sent,
        )
        self.entry.clear()

    def _on_sent(self, reply: dict) -> None:
        if reply.get("t") == "error":
            self.toast.show_message(reply.get("message", "Not sent"), self.palette_tokens, "danger")
            return
        self.toast.show_message("Sent", self.palette_tokens, "success")
        # Same delay as an incoming text: the send returns once the message is
        # handed to the radio, a moment before it appears in the provider.
        self._pending.start()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.toast._reposition()
