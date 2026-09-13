"""Files between the phone and this computer, in both directions.

Two ways in and two ways out, because the moment a file needs moving is never
the same moment twice:

* drop files anywhere on this window, or press the button here;
* share to Tessera from any app on the phone, which puts the file straight in
  the desktop's download folder.

The page is mostly a list. What matters while a file is moving is how far it
has got and the ability to stop it; what matters afterwards is opening it, or
finding it in the file manager -- so those are the only buttons.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...backends import filetransfer
from ...backends.filetransfer import DONE, RECEIVING, SENDING, Transfer
from ...core.hub import Hub
from ..theme import RADIUS, SPACE, Palette
from ..widgets import Card, Pill, Toast, heading


class DropArea(QWidget):
    """The target for dragged files, and the button for everyone else.

    A drop zone alone would be a trap for anyone driving by keyboard, and a
    button alone throws away the gesture people actually reach for. It is one
    control doing both: click it, or drop on it -- or on the window.
    """

    def __init__(self, palette: Palette, on_files, parent: QWidget | None = None):
        super().__init__(parent)
        self._palette = palette
        self._on_files = on_files
        self._hot = False
        self.setAcceptDrops(True)
        self.setMinimumHeight(96)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE["lg"], SPACE["lg"], SPACE["lg"], SPACE["lg"])
        layout.setSpacing(SPACE["sm"])

        self.label = QLabel("Drop files here to send them to the phone")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setStyleSheet("font-weight: 600;")
        layout.addWidget(self.label)

        row = QHBoxLayout()
        row.addStretch(1)
        self.button = QPushButton("Choose files...")
        self.button.setObjectName("Primary")
        self.button.clicked.connect(self._choose)
        row.addWidget(self.button)
        row.addStretch(1)
        layout.addLayout(row)

        self._paint()

    def _paint(self) -> None:
        p = self._palette
        colour = p.accent if self._hot else p.border
        self.setStyleSheet(
            f"DropArea {{ border: 2px dashed {colour}; "
            f"border-radius: {RADIUS['lg']}px; }}"
        )

    def _choose(self) -> None:
        paths, _filter = QFileDialog.getOpenFileNames(
            self, "Send to the phone", str(Path.home())
        )
        if paths:
            self._on_files(paths)

    # -- dragging ------------------------------------------------------------

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._hot = True
            self._paint()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self._hot = False
        self._paint()
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        self._hot = False
        self._paint()
        paths = [
            url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()
        ]
        if paths:
            event.acceptProposedAction()
            self._on_files(paths)


class TransferRow(Card):
    """One file, moving or moved."""

    def __init__(self, transfer: Transfer, palette: Palette, page: "SharePage"):
        super().__init__(page, flat=True)
        self.palette_tokens = palette
        self.page = page
        self.transfer = transfer

        top = QHBoxLayout()
        self.name = QLabel(transfer.name)
        self.name.setStyleSheet("font-weight: 600;")
        self.name.setWordWrap(True)
        top.addWidget(self.name, 1)

        self.pill = Pill("", "muted")
        self.pill.apply(palette)
        top.addWidget(self.pill)
        self.body().addLayout(top)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        self.add(self.bar)

        bottom = QHBoxLayout()
        self.detail = QLabel("")
        self.detail.setObjectName("Muted")
        bottom.addWidget(self.detail, 1)

        self.open_button = QPushButton("Open")
        self.open_button.clicked.connect(self._open)
        bottom.addWidget(self.open_button)

        self.reveal_button = QPushButton("Show in folder")
        self.reveal_button.clicked.connect(self._reveal)
        bottom.addWidget(self.reveal_button)

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("Danger")
        self.cancel_button.clicked.connect(self._cancel)
        bottom.addWidget(self.cancel_button)
        self.body().addLayout(bottom)

        self.apply(transfer)

    def apply(self, transfer: Transfer) -> None:
        self.transfer = transfer
        self.name.setText(transfer.name)
        self.bar.setValue(transfer.percent)
        self.detail.setText(transfer.summary)

        arrow = "To the phone" if transfer.direction == SENDING else "From the phone"
        tone, text = {
            "waiting": ("muted", "Waiting"),
            "running": ("accent", arrow),
            "finishing": ("accent", "Finishing"),
            "done": ("success", "Sent" if transfer.direction == SENDING else "Saved"),
            "failed": ("danger", "Failed"),
            "cancelled": ("warning", "Cancelled"),
        }.get(transfer.state, ("muted", arrow))
        self.pill.set_state(text, tone)

        self.bar.setVisible(transfer.active)
        self.cancel_button.setVisible(transfer.active)
        # Only a file that is here can be opened: a file sent from here is
        # already wherever it came from, and one that failed never arrived.
        landed = (
            transfer.direction == RECEIVING
            and transfer.state == DONE
            and transfer.path is not None
        )
        self.open_button.setVisible(landed)
        self.reveal_button.setVisible(landed)

    def _open(self) -> None:
        if self.transfer.path is not None:
            filetransfer.open_path(self.transfer.path)

    def _reveal(self) -> None:
        if self.transfer.path is not None:
            filetransfer.reveal(self.transfer.path)

    def _cancel(self) -> None:
        self.page.hub.files.cancel(self.transfer.id)


class SharePage(QWidget):
    """Send, receive, and see what is happening."""

    def __init__(self, hub: Hub, palette: Palette, parent: QWidget | None = None):
        super().__init__(parent)
        self.hub = hub
        self.palette_tokens = palette
        self._rows: dict[str, TransferRow] = {}

        page = QVBoxLayout(self)
        page.setContentsMargins(SPACE["xl"], SPACE["xl"], SPACE["xl"], 0)
        page.setSpacing(SPACE["lg"])
        page.addWidget(heading(
            "Share", "Files both ways, over the connection the app already has"
        ))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        page.addWidget(scroll, 1)

        host = QWidget()
        scroll.setWidget(host)
        outer = QVBoxLayout(host)
        outer.setContentsMargins(0, 0, SPACE["md"], SPACE["xl"])
        outer.setSpacing(SPACE["lg"])

        send = Card(self)
        send_title = QLabel("Send to the phone")
        send_title.setObjectName("SectionTitle")
        send.add(send_title)
        self.drop = DropArea(palette, self._send, self)
        send.add(self.drop)
        self.send_note = QLabel()
        self.send_note.setObjectName("Muted")
        self.send_note.setWordWrap(True)
        send.add(self.send_note)
        outer.addWidget(send)

        receive = Card(self)
        row = QHBoxLayout()
        receive_title = QLabel("From the phone")
        receive_title.setObjectName("SectionTitle")
        row.addWidget(receive_title)
        row.addStretch(1)
        change = QPushButton("Change folder...")
        change.clicked.connect(self._choose_folder)
        row.addWidget(change)
        receive.body().addLayout(row)

        self.folder_note = QLabel()
        self.folder_note.setObjectName("Muted")
        self.folder_note.setWordWrap(True)
        receive.add(self.folder_note)
        outer.addWidget(receive)

        history = Card(self)
        history_row = QHBoxLayout()
        history_title = QLabel("Transfers")
        history_title.setObjectName("SectionTitle")
        history_row.addWidget(history_title)
        history_row.addStretch(1)
        clear = QPushButton("Clear finished")
        clear.clicked.connect(self._clear)
        history_row.addWidget(clear)
        history.body().addLayout(history_row)

        self.empty = QLabel("Nothing has been sent or received yet.")
        self.empty.setObjectName("Muted")
        history.add(self.empty)

        self.list = QVBoxLayout()
        self.list.setSpacing(SPACE["sm"])
        history.body().addLayout(self.list)
        outer.addWidget(history)
        self.history_card = history

        outer.addStretch(1)

        self.toast = Toast(self)
        hub.transferChanged.connect(self._on_changed)
        self._refresh_notes()
        self.rebuild()

    # -- actions -------------------------------------------------------------

    def _send(self, paths: list[str]) -> None:
        self.hub.send_files(paths)

    def _choose_folder(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "Where should files from the phone go?",
            str(self.hub.files.directory()),
        )
        if not chosen:
            return
        self.hub.config.files.save_to = chosen
        self.hub.config.save()
        self._refresh_notes()
        self.toast.show_message("Saved", self.palette_tokens, "success", 1200)

    def _clear(self) -> None:
        self.hub.files.forget_finished()
        self.rebuild()

    # -- state ---------------------------------------------------------------

    def _refresh_notes(self) -> None:
        self.folder_note.setText(
            f"Files arrive in {self.hub.files.directory()}. On the phone, share "
            "anything to Tessera and it appears here — no need to open this "
            "page first."
        )
        caps = self.hub.companion.capabilities
        if not self.hub.connected:
            self.send_note.setText("The companion app is not connected.")
        elif caps and "file_transfer" not in caps:
            self.send_note.setText(
                "This phone's companion app is older than this feature, so it "
                "cannot take files yet."
            )
        else:
            self.send_note.setText(
                "Files go to the phone's Downloads folder. One at a time, so "
                "each one is as fast as the link allows."
            )

    def _on_changed(self, transfer: Transfer) -> None:
        row = self._rows.get(transfer.id)
        if row is None:
            self.rebuild()
            return
        row.apply(transfer)

    def rebuild(self) -> None:
        while self.list.count():
            item = self.list.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._rows.clear()

        transfers = self.hub.files.transfers
        self.empty.setVisible(not transfers)
        for transfer in transfers:
            row = TransferRow(transfer, self.palette_tokens, self)
            self._rows[transfer.id] = row
            self.list.addWidget(row)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._refresh_notes()
        self.rebuild()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.toast._reposition()
