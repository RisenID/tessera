"""Phone screen mirroring. The app launcher is pages/apps.py."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...backends import mirror
from ...core.hub import Hub
from ...core import packages
from ..theme import SPACE, Palette
from ..widgets import Card, Toast, heading


class ScreenPage(QWidget):
    """Mirror the whole phone."""

    def __init__(self, hub: Hub, palette: Palette, parent: QWidget | None = None):
        super().__init__(parent)
        self.hub = hub
        self.palette_tokens = palette

        outer = QVBoxLayout(self)
        outer.setContentsMargins(SPACE["xl"], SPACE["xl"], SPACE["xl"], SPACE["xl"])
        outer.setSpacing(SPACE["lg"])
        outer.addWidget(heading("Screen", "Your phone's screen in a window"))

        # -- full mirror -----------------------------------------------------
        mirror_card = Card(self)
        title = QLabel("Whole phone")
        title.setObjectName("SectionTitle")
        mirror_card.add(title)

        description = QLabel(
            "Opens your phone's screen in a window you can touch, type and scroll in."
        )
        description.setObjectName("Muted")
        description.setWordWrap(True)
        mirror_card.add(description)

        controls = QHBoxLayout()
        self.mirror_button = QPushButton("Start mirroring")
        self.mirror_button.setObjectName("Primary")
        self.mirror_button.clicked.connect(self._toggle_mirror)
        controls.addWidget(self.mirror_button)

        self.screen_off = QPushButton("Mirror with phone screen off")
        self.screen_off.setCheckable(True)
        self.screen_off.setChecked(hub.config.mirror.turn_screen_off)
        self.screen_off.toggled.connect(self._set_screen_off)
        controls.addWidget(self.screen_off)
        controls.addStretch(1)
        mirror_card.body().addLayout(controls)

        self.mirror_status = QLabel()
        self.mirror_status.setObjectName("Muted")
        self.mirror_status.setWordWrap(True)
        mirror_card.add(self.mirror_status)
        outer.addWidget(mirror_card)
        outer.addStretch(1)

        self.toast = Toast(self)

        hub.mirrors.changed.connect(self._update_state)
        hub.mirrors.failed.connect(
            lambda m: self.toast.show_message(m[:120], palette, "danger")
        )
        self._update_state()

    # -- mirroring -----------------------------------------------------------

    def _options(self) -> mirror.MirrorOptions:
        cfg = self.hub.config.mirror
        return mirror.MirrorOptions(
            max_size=cfg.max_size,
            fps=cfg.fps,
            bitrate=cfg.bitrate,
            audio=cfg.audio,
            stay_awake=cfg.stay_awake,
            turn_screen_off=cfg.turn_screen_off,
        )

    def _set_screen_off(self, enabled: bool) -> None:
        self.hub.config.mirror.turn_screen_off = enabled
        self.hub.config.save()

    #: The panel's switch; mirroring is already a toggle.
    def quick_toggle(self) -> None:
        self._toggle_mirror()

    def _toggle_mirror(self) -> None:
        if self.hub.mirrors.is_running("screen"):
            self.hub.mirrors.close("screen")
            return
        try:
            argv = mirror.mirror_command(
                self.hub.serial, self._options(), self.hub.phone_name
            )
            self.hub.mirrors.open("screen", "Phone screen", argv)
        except mirror.MirrorError as exc:
            self.toast.show_message(str(exc)[:140], self.palette_tokens, "danger")

    def _update_state(self) -> None:
        running = self.hub.mirrors.is_running("screen")
        self.mirror_button.setText("Stop mirroring" if running else "Start mirroring")

        if not mirror.available():
            self.mirror_status.setText(
                "scrcpy is not installed. " + packages.advice("scrcpy")
            )
        elif not self.hub.serial:
            self.mirror_status.setText(
                "No phone reachable over adb, which screen control needs."
            )
        else:
            open_windows = len(self.hub.mirrors.sessions)
            self.mirror_status.setText(
                f"{open_windows} window{'s' if open_windows != 1 else ''} open"
                if open_windows
                else "Ready."
            )

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        self.toast._reposition()
