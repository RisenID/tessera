"""Files between the phone and this computer, in both directions."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
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

from ...backends import filetransfer, storage
from ...backends.filetransfer import DONE, RECEIVING, SENDING, Transfer
from ...core import platform
from ...core.hub import Hub
from ..theme import RADIUS, SPACE, Palette
from ..widgets import Card, Pill, Toast, heading


class DropArea(QWidget):
    """The target for dragged files, and the button for everyone else."""

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


class _NoteRow(Card):
    """One piece of shared text, with a copy button."""

    def __init__(self, when: float, text: str, palette: Palette, on_copy, parent=None):
        super().__init__(parent, flat=True, padding=SPACE["md"])
        row = QHBoxLayout()
        column = QVBoxLayout()
        column.setSpacing(2)
        body = QLabel(text if len(text) <= 400 else text[:400] + "…")
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        column.addWidget(body)
        stamp = QLabel(datetime.fromtimestamp(when).strftime("%d %b, %H:%M"))
        stamp.setStyleSheet(f"color: {palette.muted}; font-size: 11px;")
        column.addWidget(stamp)
        row.addLayout(column, 1)
        copy = QPushButton("Copy")
        copy.setObjectName("Copy")
        copy.clicked.connect(lambda: on_copy(text))
        row.addWidget(copy, 0, Qt.AlignmentFlag.AlignTop)
        self.body().addLayout(row)


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

        self.storage_card = self._build_storage_card(palette)
        outer.addWidget(self.storage_card)
        outer.addWidget(self._build_notes_card(palette))

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
        self._refresh_storage()
        self.rebuild()

    # -- text from the share sheet ---------------------------------------------

    def _build_notes_card(self, palette: Palette) -> Card:
        card = Card(self)
        row = QHBoxLayout()
        title = QLabel("Text from the phone")
        title.setObjectName("SectionTitle")
        row.addWidget(title)
        row.addStretch(1)
        clear = QPushButton("Clear")
        clear.setObjectName("Ghost")
        clear.clicked.connect(self._clear_notes)
        row.addWidget(clear)
        card.body().addLayout(row)

        note = QLabel(
            "Anything shared to Tessera from the phone lands on the clipboard "
            "and stays here until cleared."
        )
        note.setObjectName("Muted")
        note.setWordWrap(True)
        card.add(note)

        self.notes = QVBoxLayout()
        self.notes.setSpacing(SPACE["sm"])
        card.body().addLayout(self.notes)
        self.notes_empty = QLabel("Nothing shared yet.")
        self.notes_empty.setObjectName("Muted")
        card.add(self.notes_empty)

        self.hub.textShared.connect(lambda _t: self._render_notes())
        self._render_notes()
        return card

    def _render_notes(self) -> None:
        while self.notes.count():
            item = self.notes.takeAt(0)
            widget = item.widget() if item else None
            if widget is not None:
                widget.deleteLater()
        texts = self.hub.shared_texts
        self.notes_empty.setVisible(not texts)
        for when, text in texts[:20]:
            self.notes.addWidget(_NoteRow(when, text, self.palette_tokens, self._copy_note))

    def _copy_note(self, text: str) -> None:
        QGuiApplication.clipboard().setText(text)
        self.toast.show_message("Copied", self.palette_tokens, "success", 1200)

    def _clear_notes(self) -> None:
        self.hub.shared_texts.clear()
        self._render_notes()

    # -- the phone's storage ---------------------------------------------------

    def _build_storage_card(self, palette: Palette) -> Card:
        card = Card(self)
        row = QHBoxLayout()
        title = QLabel("Phone storage")
        title.setObjectName("SectionTitle")
        row.addWidget(title)
        row.addStretch(1)
        self.storage_pill = Pill("Not mounted", "muted")
        self.storage_pill.apply(palette)
        row.addWidget(self.storage_pill)
        card.body().addLayout(row)

        self.storage_note = QLabel()
        self.storage_note.setObjectName("Muted")
        self.storage_note.setWordWrap(True)
        card.add(self.storage_note)

        buttons = QHBoxLayout()
        self.storage_open = QPushButton("Open in file manager")
        self.storage_open.setObjectName("Primary")
        self.storage_open.clicked.connect(self._open_storage)
        buttons.addWidget(self.storage_open)
        self.storage_toggle = QPushButton("Mount")
        self.storage_toggle.clicked.connect(self._toggle_storage)
        buttons.addWidget(self.storage_toggle)
        self.storage_allow = QPushButton("Allow on the phone")
        self.storage_allow.clicked.connect(self.hub.grant_storage)
        buttons.addWidget(self.storage_allow)
        buttons.addStretch(1)
        card.body().addLayout(buttons)

        self.hub.storageChanged.connect(self._refresh_storage)
        self.hub.connectionChanged.connect(lambda _c: self._refresh_storage())
        self.hub.companion.capabilitiesChanged.connect(lambda _c: self._refresh_storage())
        return card

    def _refresh_storage(self) -> None:
        hub = self.hub
        possible = hub.config.features.storage and platform.supported("storage")
        self.storage_card.setVisible(possible)
        if not possible:
            return

        caps = hub.companion.capabilities
        mounted = hub.storage_mount is not None
        state = hub.storage_state
        connected = hub.connected
        backend = storage.backend()

        if mounted:
            self.storage_pill.set_state("Mounted", "success")
        elif state == "starting":
            self.storage_pill.set_state("Connecting", "warning")
        elif state == "error":
            self.storage_pill.set_state("Not mounted", "danger")
        else:
            self.storage_pill.set_state("Not mounted", "muted")

        if mounted and platform.IS_WINDOWS:
            note = (
                f"The phone is in File Explorer's navigation pane, as "
                f"{hub.storage_mount.name}. Files download when opened, and "
                "changes go back to the phone."
            )
        elif mounted:
            where = hub.storage_mount.local_path or hub.storage_mount.location
            note = (
                f"The phone's storage is at {where}, and in the file manager's "
                "sidebar. It is unmounted when the phone disconnects."
            )
        elif state in ("starting", "error") and hub.storage_message:
            note = hub.storage_message
        elif not connected:
            note = "The companion app is not connected."
        elif not backend:
            note = storage.missing_advice()
        elif caps and "storage" not in caps:
            note = (
                "This phone needs Android 11 or later, and a companion app new "
                "enough to share its storage."
            )
        elif caps and "storage_allowed" not in caps:
            note = "The phone has not allowed All files access yet." + (
                " It can be allowed from here, through Shizuku."
                if "storage_grant" in caps
                else " Allow it in the companion app on the phone."
            )
        else:
            note = (
                "The phone's files as a folder, over SFTP. The connection is "
                "checked against a key the phone sends over the paired link, so "
                "nothing else on the network can stand in for it."
            )
        self.storage_note.setText(note)

        self.storage_open.setVisible(mounted)
        self.storage_toggle.setText("Unmount" if mounted else "Mount")
        self.storage_toggle.setEnabled(
            mounted or (connected and bool(backend) and state != "starting"
                        and (not caps or "storage" in caps))
        )
        self.storage_allow.setVisible(
            connected and not mounted and state != "starting"
            and "storage_grant" in caps and "storage_allowed" not in caps
        )

    def _toggle_storage(self) -> None:
        if self.hub.storage_mount is not None:
            self.hub.unmount_storage()
        else:
            self.hub.mount_storage()

    def _open_storage(self) -> None:
        if self.hub.storage_mount is not None:
            storage.open_location(self.hub.storage_mount)

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
        self._refresh_storage()
        self.rebuild()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.toast._reposition()
