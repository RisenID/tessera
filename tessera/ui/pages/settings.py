"""Pairing and preferences."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...backends import btcodecs, companion, ldacdec
from ...core import autostart
from ...core.clipboard import MODE_LABELS as CLIPBOARD_LABELS
from ...core.hub import Hub
from ...core.proc import ManagedProcess, submit
from ..theme import SPACE, Palette
from ..widgets import Card, Pill, Toast, heading


#: key, label, and why it costs something.
FEATURE_SWITCHES: tuple[tuple[str, str, str], ...] = (
    ("notifications", "Notifications", "Mirror notifications and replies"),
    ("otp", "One-time passcodes", "Pick codes out of notifications for one-click copying"),
    ("notification_popups", "Desktop popups", "Also raise a desktop notification for each one"),
    ("calls", "Calls", "Answer, decline and dial from the computer"),
    ("messages", "Messages", "Read and send SMS"),
    ("photos", "Photos", "Browse the phone's gallery"),
    ("clipboard", "Clipboard sharing", "Needs Shizuku; polls the phone while connected"),
    ("dnd_sync", "Do Not Disturb sync", "Keep both screens silenced together"),
    ("webcam", "Webcam", "Use a phone camera as a virtual webcam"),
    ("screen", "Screen mirroring", "The whole phone in a window, through scrcpy over adb"),
    ("apps", "Apps", "Launch one app into its own window"),
    ("hotspot", "Hotspot", "Start the phone's hotspot and join it"),
    ("bluetooth_audio", "Calls and music", "Bluetooth audio to and from the phone"),
)


class SettingsPage(QWidget):
    """Pairing, features and preferences."""

    featuresChanged = Signal()
    def __init__(self, hub: Hub, palette: Palette, parent: QWidget | None = None):
        super().__init__(parent)
        self.hub = hub
        self.palette_tokens = palette

        # The page is taller than any window, so it scrolls. Without this the
        # layout compresses every card until the text is unreadable.
        page = QVBoxLayout(self)
        page.setContentsMargins(SPACE["xl"], SPACE["xl"], SPACE["xl"], 0)
        page.setSpacing(SPACE["lg"])
        page.addWidget(heading("Settings", "Pair your phone and tune how things behave"))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        page.addWidget(scroll, 1)

        content = QWidget()
        scroll.setWidget(content)
        outer = QVBoxLayout(content)
        outer.setContentsMargins(0, 0, SPACE["md"], SPACE["xl"])
        outer.setSpacing(SPACE["lg"])

        # -- pairing ---------------------------------------------------------
        pair = Card(self)
        row = QHBoxLayout()
        title = QLabel("Companion app")
        title.setObjectName("SectionTitle")
        row.addWidget(title)
        row.addStretch(1)
        self.link_pill = Pill("Not paired", "muted")
        self.link_pill.apply(palette)
        row.addWidget(self.link_pill)
        pair.body().addLayout(row)

        self.discovered = QComboBox()
        self.discovered.setPlaceholderText("Phones found on this network")
        pair.add(self.discovered)

        find = QPushButton("Search again")
        find.clicked.connect(self.discover)
        pair.add(find)

        self.host = QLineEdit(hub.config.companion.host)
        self.host.setPlaceholderText("Or type the address shown in the phone app, e.g. 192.168.1.5")
        pair.add(self.host)

        self.code = QLineEdit()
        self.code.setPlaceholderText("Six-digit pairing code from the phone")
        self.code.setMaxLength(6)
        pair.add(self.code)

        buttons = QHBoxLayout()
        connect = QPushButton("Pair")
        connect.setObjectName("Primary")
        connect.clicked.connect(self._pair)
        buttons.addWidget(connect)

        forget = QPushButton("Forget this phone")
        forget.setObjectName("Danger")
        forget.clicked.connect(self._forget)
        buttons.addWidget(forget)
        buttons.addStretch(1)
        pair.body().addLayout(buttons)

        self.pair_status = QLabel()
        self.pair_status.setObjectName("Muted")
        self.pair_status.setWordWrap(True)
        pair.add(self.pair_status)
        outer.addWidget(pair)

        # -- startup ---------------------------------------------------------
        startup = Card(self)
        startup_title = QLabel("Startup")
        startup_title.setObjectName("SectionTitle")
        startup.add(startup_title)

        self.autostart_box = QCheckBox("Start Tessera when I log in")
        self.autostart_box.setChecked(autostart.enabled())
        startup.add(self.autostart_box)

        self.minimised_box = QCheckBox("Start minimised to the tray")
        self.minimised_box.setChecked(hub.config.start_minimised)
        startup.add(self.minimised_box)

        startup_note = QLabel(
            "At login, not at boot — Tessera needs a desktop session to run in. "
            "Your desktop's own autostart settings list it too."
        )
        startup_note.setObjectName("Muted")
        startup_note.setWordWrap(True)
        startup.add(startup_note)
        outer.addWidget(startup)

        # -- screen ----------------------------------------------------------
        screen = Card(self)
        screen_title = QLabel("Screen and windows")
        screen_title.setObjectName("SectionTitle")
        screen.add(screen_title)

        cfg = hub.config.mirror
        self.max_size = QSpinBox()
        self.max_size.setRange(480, 3840)
        self.max_size.setSingleStep(80)
        self.max_size.setValue(cfg.max_size)
        screen.add(self._labelled("Maximum size (px)", self.max_size))

        self.mirror_fps = QSpinBox()
        self.mirror_fps.setRange(15, 120)
        self.mirror_fps.setValue(cfg.fps)
        screen.add(self._labelled("Frames per second", self.mirror_fps))

        self.window_size = QLineEdit(cfg.app_window_size)
        screen.add(self._labelled("App window size", self.window_size))

        self.system_apps = QCheckBox("Show system apps in the launcher")
        self.system_apps.setChecked(cfg.show_system_apps)
        screen.add(self.system_apps)

        self.mirror_audio = QCheckBox("Forward phone audio while mirroring")
        self.mirror_audio.setChecked(cfg.audio)
        screen.add(self.mirror_audio)

        # -- feature switches ------------------------------------------------
        features = Card(self)
        features_title = QLabel("Features")
        features_title.setObjectName("SectionTitle")
        features.add(features_title)

        features_note = QLabel(
            "Turning one off stops the work on the phone, not just the page here."
        )
        features_note.setObjectName("Muted")
        features_note.setWordWrap(True)
        features.add(features_note)

        self.feature_boxes: dict[str, QCheckBox] = {}
        for key, label, hint in FEATURE_SWITCHES:
            box = QCheckBox(label)
            box.setToolTip(hint)
            box.setChecked(bool(getattr(hub.config.features, key)))
            self.feature_boxes[key] = box
            features.add(box)

        outer.addWidget(features)

        clip_title = QLabel("Clipboard")
        clip_title.setObjectName("SectionTitle")
        screen.add(clip_title)

        self.clipboard_mode = QComboBox()
        for value, label in CLIPBOARD_LABELS.items():
            self.clipboard_mode.addItem(label, value)
        index = self.clipboard_mode.findData(hub.config.clipboard.mode)
        self.clipboard_mode.setCurrentIndex(max(index, 0))
        screen.add(self._labelled("Sharing", self.clipboard_mode))

        clip_note = QLabel("Needs Shizuku on the phone.")
        clip_note.setObjectName("Muted")
        clip_note.setWordWrap(True)
        screen.add(clip_note)

        audio_title = QLabel("Bluetooth audio")
        audio_title.setObjectName("SectionTitle")
        screen.add(audio_title)

        self.codec_choice = QComboBox()
        self._fill_codecs()
        screen.add(self._labelled("Quality", self.codec_choice))

        codec_note = QLabel(
            "Forcing a codec the phone cannot manage drops it to plain SBC. "
            "Saving silences this computer for a second."
        )
        codec_note.setObjectName("Muted")
        codec_note.setWordWrap(True)
        screen.add(codec_note)

        # -- LDAC ------------------------------------------------------------
        ldac_header = QHBoxLayout()
        ldac_title = QLabel("LDAC")
        ldac_title.setStyleSheet("font-weight: 600;")
        ldac_header.addWidget(ldac_title)
        ldac_header.addStretch(1)
        self.ldac_pill = Pill("", "muted")
        self.ldac_pill.apply(palette)
        ldac_header.addWidget(self.ldac_pill)
        screen.add(self._bar(ldac_header))

        self.ldac_note = QLabel()
        self.ldac_note.setObjectName("Muted")
        self.ldac_note.setWordWrap(True)
        screen.add(self.ldac_note)

        buttons = QHBoxLayout()
        self.ldac_action = QPushButton()
        self.ldac_action.clicked.connect(self._ldac_action)
        buttons.addWidget(self.ldac_action)
        self.ldac_remove = QPushButton("Remove")
        self.ldac_remove.setObjectName("Ghost")
        self.ldac_remove.clicked.connect(self._ldac_uninstall)
        buttons.addWidget(self.ldac_remove)
        buttons.addStretch(1)
        screen.add(self._bar(buttons))

        # Hidden until something runs. The build takes a while and downloads
        # the PipeWire sources on its first run, so a silent spinner would look
        # like a hang; the script's own output is the honest progress report.
        self.ldac_log = QPlainTextEdit()
        self.ldac_log.setReadOnly(True)
        self.ldac_log.setMaximumHeight(150)
        self.ldac_log.setVisible(False)
        screen.add(self.ldac_log)

        #: Which step is running: "tools", "setup" or "remove". Chaining the
        #: build onto a package install needs this -- without it a Remove,
        #: which also ends with the decoder absent and the tools present, looks
        #: identical and reinstalls what was just removed.
        self._ldac_step = ""
        self._ldac_proc = ManagedProcess(self)
        self._ldac_proc.output.connect(self.ldac_log.appendPlainText)
        self._ldac_proc.stopped.connect(self._ldac_finished)
        self._ldac_proc.failed.connect(self._ldac_failed)
        self._refresh_ldac()

        save = QPushButton("Save")
        save.setObjectName("Primary")
        save.clicked.connect(self._save)
        screen.add(save)
        outer.addWidget(screen)
        outer.addStretch(1)

        self.toast = Toast(self)
        hub.companion.connectedChanged.connect(self._refresh)
        hub.companion.errorOccurred.connect(self._show_error)
        self._refresh()
        self.discover()

    @staticmethod
    def _bar(layout) -> QWidget:
        """A layout as a widget, so Card.add can take it."""
        container = QWidget()
        layout.setContentsMargins(0, 0, 0, 0)
        container.setLayout(layout)
        return container

    # -- LDAC setup ----------------------------------------------------------

    def _fill_codecs(self) -> None:
        """Populate the quality list, keeping whatever was selected.

        Rebuilt rather than built once: installing the decoder adds LDAC to it
        without the page being reopened.
        """
        chosen = self.codec_choice.currentData() or self.hub.config.bluetooth.codec
        offer_ldac = btcodecs.ldac_receivable()
        self.codec_choice.blockSignals(True)
        self.codec_choice.clear()
        for value, label in btcodecs.LABELS.items():
            # Offering a codec this computer cannot decode is worse than not
            # offering it: the phone would pick it and send audio into silence.
            if value == "ldac" and not offer_ldac:
                continue
            self.codec_choice.addItem(label, value)
        self.codec_choice.setCurrentIndex(max(self.codec_choice.findData(chosen), 0))
        self.codec_choice.blockSignals(False)

    def _refresh_ldac(self) -> None:
        """Show where LDAC stands, and offer the one useful next step."""
        busy = self._ldac_proc.running
        installed = ldacdec.installed()
        missing = [] if installed else ldacdec.missing_packages()

        self.ldac_pill.set_state(
            "Working…" if busy else ("Available" if installed else "Not installed"),
            "warning" if busy else ("success" if installed else "muted"),
        )
        self.ldac_remove.setVisible(installed)
        self.ldac_action.setEnabled(not busy)
        self.ldac_remove.setEnabled(not busy)

        if not ldacdec.available():
            self.ldac_action.setVisible(False)
            self.ldac_note.setText(
                "The setup script is not installed with this copy of Tessera."
            )
            return

        self.ldac_action.setVisible(True)
        # Setting up is the action worth emphasising; once it is done the
        # button is only there for a rebuild. Qt does not restyle on an
        # objectName change on its own, hence the repolish.
        self.ldac_action.setObjectName("" if installed else "Primary")
        self.ldac_action.style().unpolish(self.ldac_action)
        self.ldac_action.style().polish(self.ldac_action)

        if installed:
            self.ldac_action.setText("Rebuild")
            self.ldac_note.setText(
                "909 kbit/s at up to 96 kHz, against aptX's 352 at 44.1. "
                "Rebuild after a PipeWire update."
            )
        elif missing:
            self.ldac_action.setText("Install build tools")
            self.ldac_note.setText(
                "No distribution ships an LDAC decoder; Tessera can build one. "
                "Installing the build tools asks for your password:\n"
                + ldacdec.install_command(missing)
            )
        else:
            self.ldac_action.setText("Set up LDAC")
            self.ldac_note.setText(
                "No distribution ships an LDAC decoder; Tessera can build one "
                "under your home directory. The first run takes a minute and "
                "ends with a brief silence."
            )

    def _ldac_action(self) -> None:
        missing = ldacdec.missing_packages()
        if missing and not ldacdec.installed():
            self._run_ldac(ldacdec.install_argv(missing), "Installing build tools", step="tools")
        else:
            self._run_ldac(None, "Setting up LDAC", step="setup")

    def _ldac_uninstall(self) -> None:
        self._run_ldac(None, "Removing LDAC support", action="--uninstall", step="remove")

    def _run_ldac(self, argv: list[str] | None, what: str, action: str = "",
                  step: str = "setup") -> None:
        """Run one step, with the script's own output as the progress report.

        argv is None for the script itself, which has to be located first and
        may not be there at all.
        """
        if self._ldac_proc.running:
            return
        self._ldac_step = step
        if argv is None:
            try:
                argv = ldacdec.setup_argv(action)
            except RuntimeError as exc:
                self._ldac_failed(str(exc))
                return
        self.ldac_log.setVisible(True)
        self.ldac_log.setPlainText(f"{what}…")
        self._refresh_ldac()
        try:
            self._ldac_proc.start(argv)
        except RuntimeError as exc:
            self._ldac_failed(str(exc))
        else:
            self._refresh_ldac()

    def _ldac_failed(self, message: str) -> None:
        self.ldac_log.appendPlainText(message)
        self._refresh_ldac()
        self.toast.show_message(message[:130], self.palette_tokens, "danger")

    def _ldac_finished(self, code: int) -> None:
        self._fill_codecs()
        self._refresh_ldac()

        if code != 0:
            # 126 is pkexec's "dismissed or not authorised"; the script's own
            # failures have already said what was wrong, in the log above.
            self.toast.show_message(
                "Authentication was cancelled." if code == 126
                else "That did not work — see the log.",
                self.palette_tokens,
                "danger",
            )
            return

        # Installing the build tools only clears the way; the build is the
        # step the user actually asked for, so go straight on to it.
        if self._ldac_step == "tools" and not ldacdec.missing_packages():
            self._run_ldac(None, "Setting up LDAC", step="setup")
            return

        # The codec list advertised to the phone is written from the saved
        # preference and does not mention LDAC until it can be decoded, so
        # installing a decoder changes nothing until that file is rewritten.
        # Missing this is the difference between LDAC working and appearing to
        # be installed while the phone keeps choosing aptX.
        choice = self.hub.config.bluetooth.codec
        if btcodecs.write_preference(choice):
            submit(btcodecs.reload_session, on_error=lambda _m: None)
        self.toast.show_message(
            "LDAC is ready. Play something and press “Play phone audio here”."
            if ldacdec.installed() else "LDAC support removed.",
            self.palette_tokens,
            "success",
        )

    @staticmethod
    def _labelled(text: str, widget: QWidget) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        label = QLabel(text)
        label.setMinimumWidth(180)
        layout.addWidget(label)
        layout.addWidget(widget, 1)
        return container

    def discover(self) -> None:
        self.pair_status.setText("Looking for phones on this network...")
        submit(companion.discover, on_done=self._on_discovered, on_error=lambda _m: None)

    def _on_discovered(self, found: list) -> None:
        self.discovered.clear()
        for entry in found:
            self.discovered.addItem(entry.label, entry)
        self.pair_status.setText(
            f"Found {len(found)} phone{'s' if len(found) != 1 else ''}."
            if found
            else "No phones found. Open the companion app, then type its address below."
        )

    def _pair(self) -> None:
        entry = self.discovered.currentData()
        host = entry.host if entry is not None else self.host.text().strip()
        port = entry.port if entry is not None else companion.DEFAULT_PORT

        if ":" in host and entry is None:
            host, _, raw_port = host.partition(":")
            port = int(raw_port) if raw_port.isdigit() else companion.DEFAULT_PORT

        if not host:
            self.pair_status.setText("Enter the address shown in the phone app.")
            return

        code = self.code.text().strip()
        if not code:
            self.pair_status.setText("Tap “Show pairing code” in the phone app and enter it here.")
            return

        self.pair_status.setText(f"Pairing with {host}...")
        self.hub.companion.connect_to_phone(host, port, code)

    def _forget(self) -> None:
        self.hub.companion.disconnect_from_phone()
        self.hub.companion.phone = companion.PairedPhone()
        self.hub.save_phone()
        self._refresh()
        self.toast.show_message("Phone forgotten", self.palette_tokens)

    def _save(self) -> None:
        cfg = self.hub.config.mirror
        cfg.max_size = self.max_size.value()
        cfg.fps = self.mirror_fps.value()
        cfg.app_window_size = self.window_size.text().strip() or "1280x800"
        cfg.show_system_apps = self.system_apps.isChecked()
        cfg.audio = self.mirror_audio.isChecked()

        for key, box in self.feature_boxes.items():
            setattr(self.hub.config.features, key, box.isChecked())

        self.hub.config.start_minimised = self.minimised_box.isChecked()
        if self.autostart_box.isChecked() != autostart.enabled():
            if not autostart.set_enabled(self.autostart_box.isChecked()):
                self.toast.show_message(
                    "Could not write the autostart entry", self.palette_tokens, "danger"
                )
                self.autostart_box.setChecked(autostart.enabled())

        codec = self.codec_choice.currentData()
        self.hub.config.bluetooth.codec = codec
        # The phone's own list matters here: "best the phone offers" narrows
        # the advertised codecs to one, and it can only do that once the phone
        # has said which ones it can send.
        phone_codecs = list(self.hub.config.bluetooth.phone_codecs)
        # Only restart the audio service when the offer actually changed: it
        # cuts this computer's sound for a moment, which is not something to do
        # on every save.
        if btcodecs.write_preference(codec, phone_codecs):
            submit(btcodecs.reload_session, on_error=lambda _m: None)

        self.hub.config.clipboard.mode = self.clipboard_mode.currentData()
        if self.hub.config.features.clipboard:
            self.hub.clipboard.set_mode(self.hub.config.clipboard.mode)
        self.hub.config.save()
        # Re-subscribe so the phone stops or starts work immediately, rather
        # than at the next reconnection.
        self.hub.apply_features()
        self.featuresChanged.emit()
        self.toast.show_message("Saved", self.palette_tokens, "success")

    def _show_error(self, message: str) -> None:
        self.pair_status.setText(message)

    def _refresh(self) -> None:
        if self.hub.companion.connected:
            self.link_pill.set_state(self.hub.companion.phone.name or "Connected", "success")
            self.code.clear()
        elif self.hub.companion.phone.configured:
            self.link_pill.set_state("Paired, offline", "warning")
        else:
            self.link_pill.set_state("Not paired", "muted")

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.toast._reposition()
