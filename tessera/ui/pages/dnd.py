"""Do Not Disturb, phone and desktop kept in step."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QButtonGroup, QComboBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ...backends.dnd import MODE_LABELS, MODE_OFF
from ...core.hub import Hub
from ..theme import SPACE, Palette
from ..widgets import Card, Pill, Toast, heading

MODES = [
    ("off", "Off", "Everything comes through"),
    ("priority", "Priority only", "Only starred contacts and repeat callers"),
    ("alarms", "Alarms only", "Alarms, nothing else"),
    ("none", "Total silence", "Nothing at all"),
]


class DndPage(QWidget):
    def __init__(self, hub: Hub, palette: Palette, parent: QWidget | None = None):
        super().__init__(parent)
        self.hub = hub
        self.palette_tokens = palette

        outer = QVBoxLayout(self)
        outer.setContentsMargins(SPACE["xl"], SPACE["xl"], SPACE["xl"], SPACE["xl"])
        outer.setSpacing(SPACE["lg"])
        outer.addWidget(
            heading("Do Not Disturb", "Silence both screens at once, in whichever direction you choose")
        )

        # -- current state ---------------------------------------------------
        state_card = Card(self)
        row = QHBoxLayout()
        phone_label = QLabel("On your phone")
        phone_label.setObjectName("SectionTitle")
        row.addWidget(phone_label)
        row.addStretch(1)
        self.phone_pill = Pill("Off", "muted")
        self.phone_pill.apply(palette)
        row.addWidget(self.phone_pill)
        state_card.body().addLayout(row)

        buttons = QHBoxLayout()
        buttons.setSpacing(SPACE["sm"])
        self.mode_buttons: dict[str, QPushButton] = {}
        group = QButtonGroup(self)
        group.setExclusive(True)
        for value, label, tip in MODES:
            button = QPushButton(label)
            button.setCheckable(True)
            button.setToolTip(tip)
            button.clicked.connect(lambda _c=False, v=value: self._set_phone(v))
            group.addButton(button)
            buttons.addWidget(button)
            self.mode_buttons[value] = button
        buttons.addStretch(1)
        state_card.body().addLayout(buttons)
        outer.addWidget(state_card)

        # -- desktop state ---------------------------------------------------
        desktop_card = Card(self)
        row = QHBoxLayout()
        desktop_label = QLabel("On this computer")
        desktop_label.setObjectName("SectionTitle")
        row.addWidget(desktop_label)
        row.addStretch(1)
        self.desktop_pill = Pill("Off", "muted")
        self.desktop_pill.apply(palette)
        row.addWidget(self.desktop_pill)
        desktop_card.body().addLayout(row)

        note = QLabel(
            "Changes here show up in the desktop's own notification controls."
        )
        note.setObjectName("Muted")
        note.setWordWrap(True)
        desktop_card.add(note)
        outer.addWidget(desktop_card)

        # -- sync direction --------------------------------------------------
        sync_card = Card(self)
        sync_label = QLabel("Keep them in sync")
        sync_label.setObjectName("SectionTitle")
        sync_card.add(sync_label)

        self.mode_box = QComboBox()
        for value, label in MODE_LABELS.items():
            self.mode_box.addItem(label, value)
        index = self.mode_box.findData(hub.config.dnd.mode)
        self.mode_box.setCurrentIndex(max(index, 0))
        self.mode_box.currentIndexChanged.connect(self._change_sync)
        sync_card.add(self.mode_box)

        self.sync_note = QLabel()
        self.sync_note.setObjectName("Muted")
        self.sync_note.setWordWrap(True)
        sync_card.add(self.sync_note)
        outer.addWidget(sync_card)
        outer.addStretch(1)

        self.toast = Toast(self)
        hub.dndChanged.connect(self._on_phone_dnd)
        hub.dnd.desktopStateChanged.connect(self._on_desktop_dnd)
        self._on_phone_dnd(hub.phone_dnd)
        self._on_desktop_dnd(bool(hub.dnd.desktop_state))
        self._describe_sync()

    def _set_phone(self, mode: str) -> None:
        self.hub.set_phone_dnd(mode)

    def _on_phone_dnd(self, mode: str) -> None:
        label = dict((v, l) for v, l, _ in MODES).get(mode, "Off")
        self.phone_pill.set_state(label, "muted" if mode == "off" else "accent")
        button = self.mode_buttons.get(mode)
        if button is not None:
            button.setChecked(True)

    def _on_desktop_dnd(self, enabled: bool) -> None:
        self.desktop_pill.set_state("Silenced" if enabled else "Off", "accent" if enabled else "muted")

    def _change_sync(self) -> None:
        mode = self.mode_box.currentData()
        self.hub.dnd.set_mode(mode)
        self.hub.config.save()
        self._describe_sync()
        self.toast.show_message(f"Sync: {MODE_LABELS[mode]}", self.palette_tokens)

    def _describe_sync(self) -> None:
        mode = self.hub.config.dnd.mode
        self.sync_note.setText({
            MODE_OFF: "Nothing is synchronised; each device is on its own.",
            "phone_to_desktop": "Turning on Do Not Disturb on the phone silences this computer.",
            "desktop_to_phone": "Silencing this computer turns on Do Not Disturb on the phone.",
            "two_way": "Whichever device you change, the other follows.",
        }.get(mode, ""))

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.toast._reposition()
