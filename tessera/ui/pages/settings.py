"""Pairing and preferences."""

from __future__ import annotations

from PySide6.QtCore import QTimer, Signal
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
from ...core import autostart, platform
from ...core.clipboard import MODE_LABELS as CLIPBOARD_LABELS
from ...core.hub import Hub
from ...core.proc import ManagedProcess, submit
from ..panel import MAX_WIDTH, MIN_WIDTH, TILE_LABELS, TILES
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
    ("file_transfer", "File transfer",
     "Files both ways, and the phone's share sheet"),
    ("phone_audio", "Phone audio over the link",
     "The fallback where there is no Bluetooth: a copy of the phone's mix, "
     "which cannot carry a call"),
)


class SettingsPage(QWidget):
    """Pairing, features and preferences.

    Every control applies itself. There is no Save button: it used to sit
    inside the "Screen and windows" card at the bottom of a scrolling page,
    so a checkbox ticked in Startup or Sidebar looked like it had done
    something and was thrown away at the next launch.
    """

    featuresChanged = Signal()

    def __init__(self, hub: Hub, palette: Palette, parent: QWidget | None = None):
        super().__init__(parent)
        self.hub = hub
        self.palette_tokens = palette
        #: Set while the widgets are being filled in, so that filling them in
        #: does not look like the user changing them.
        self._loading = True

        # The page is taller than any window, so it scrolls. Without this the
        # layout compresses every card until the text is unreadable.
        page = QVBoxLayout(self)
        page.setContentsMargins(SPACE["xl"], SPACE["xl"], SPACE["xl"], 0)
        page.setSpacing(SPACE["lg"])
        page.addWidget(heading(
            "Settings", "Changes apply as you make them"
        ))

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

        # -- the device panel ------------------------------------------------
        rail = Card(self)
        rail_title = QLabel("Sidebar")
        rail_title.setObjectName("SectionTitle")
        rail.add(rail_title)

        rail_note = QLabel(
            "Drag the edge of the sidebar to resize it, or set it here. The "
            "width is kept separately for a window and for a full screen."
        )
        rail_note.setObjectName("Muted")
        rail_note.setWordWrap(True)
        rail.add(rail_note)

        self.panel_width = QSpinBox()
        self.panel_width.setRange(MIN_WIDTH, MAX_WIDTH)
        self.panel_width.setSingleStep(10)
        self.panel_width.setSuffix(" px")
        self.panel_width.setValue(hub.config.panel.width)
        rail.add(self._labelled("Width in a window", self.panel_width))

        self.panel_width_full = QSpinBox()
        self.panel_width_full.setRange(MIN_WIDTH, MAX_WIDTH)
        self.panel_width_full.setSingleStep(10)
        self.panel_width_full.setSuffix(" px")
        self.panel_width_full.setValue(hub.config.panel.width_fullscreen)
        rail.add(self._labelled("Width full screen", self.panel_width_full))

        tiles_label = QLabel("Quick switches")
        tiles_label.setStyleSheet("font-weight: 600;")
        rail.add(tiles_label)

        self.tile_boxes: dict[str, QCheckBox] = {}
        chosen = hub.config.panel.tiles
        for key in TILES:
            box = QCheckBox(TILE_LABELS.get(key, key))
            box.setToolTip(TILES[key][2])
            box.setChecked(key in chosen)
            self.tile_boxes[key] = box
            rail.add(box)

        tiles_note = QLabel(
            "A switch whose feature is turned off above stays hidden either way."
        )
        tiles_note.setObjectName("Muted")
        tiles_note.setWordWrap(True)
        rail.add(tiles_note)
        outer.addWidget(rail)

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
            impossible = platform.reason(key)
            box.setToolTip(impossible or hint)
            box.setChecked(bool(getattr(hub.config.features, key)) and not impossible)
            # Not the user's choice to make: the platform has already made it.
            box.setEnabled(not impossible)
            self.feature_boxes[key] = box
            features.add(box)
            if impossible:
                note = QLabel(impossible)
                note.setObjectName("Muted")
                note.setWordWrap(True)
                features.add(note)

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

        # -- which way the phone's audio comes ---------------------------------
        #
        # Both routes stay; this is only what the sidebar's switch does. They
        # are not equivalent: the link route cannot disturb the phone's own
        # headphones, Bluetooth carries a call's microphone. Neither is right
        # for everyone, so neither is imposed.
        self.route_card = Card(self)
        route_title = QLabel("The phone's audio")
        route_title.setObjectName("SectionTitle")
        self.route_card.add(route_title)

        self.audio_route = QComboBox()
        self.audio_route.addItem("Over Bluetooth", "bluetooth")
        self.audio_route.addItem("Whichever works (prefers Bluetooth)", "auto")
        self.audio_route.addItem("Over the link", "link")
        index = self.audio_route.findData(hub.config.phone_audio.route)
        self.audio_route.setCurrentIndex(max(index, 0))
        self.route_card.add(self._labelled("One-click route", self.audio_route))

        route_note = QLabel(
            "Bluetooth moves the audio here, and is the only route that can "
            "carry a call, so it is the one this app leads with. Over the link "
            "the phone sends a copy of what it is playing instead — the "
            "fallback for a computer with no Bluetooth radio, switched on "
            "above. Both are on the Audio page whatever is chosen, and neither "
            "moves any audio until its button is pressed."
        )
        route_note.setObjectName("Muted")
        route_note.setWordWrap(True)
        self.route_card.add(route_note)

        grant_row = QHBoxLayout()
        self.grant_button = QPushButton("Stop the phone asking")
        self.grant_button.clicked.connect(self._grant_projection)
        grant_row.addWidget(self.grant_button)
        grant_row.addStretch(1)
        self.grant_state = QLabel()
        self.grant_state.setObjectName("Muted")
        self.grant_state.setWordWrap(True)
        grant_row.addWidget(self.grant_state, 1)
        self.route_card.add(self._bar(grant_row))
        outer.addWidget(self.route_card)

        # Bluetooth audio and the LDAC decoder are PipeWire and BlueZ
        # machinery, so the whole card belongs to the platforms that have them.
        self.audio_card = Card(self)
        audio = self.audio_card
        audio_title = QLabel("Bluetooth audio")
        audio_title.setObjectName("SectionTitle")
        audio.add(audio_title)

        self.codec_choice = QComboBox()
        self._fill_codecs()
        audio.add(self._labelled("Quality", self.codec_choice))

        codec_note = QLabel(
            "Forcing a codec the phone cannot manage drops it to plain SBC. "
            "Changing this silences this computer for a second."
        )
        codec_note.setObjectName("Muted")
        codec_note.setWordWrap(True)
        audio.add(codec_note)

        # -- LDAC ------------------------------------------------------------
        ldac_header = QHBoxLayout()
        ldac_title = QLabel("LDAC")
        ldac_title.setStyleSheet("font-weight: 600;")
        ldac_header.addWidget(ldac_title)
        ldac_header.addStretch(1)
        self.ldac_pill = Pill("", "muted")
        self.ldac_pill.apply(palette)
        ldac_header.addWidget(self.ldac_pill)
        audio.add(self._bar(ldac_header))

        self.ldac_note = QLabel()
        self.ldac_note.setObjectName("Muted")
        self.ldac_note.setWordWrap(True)
        audio.add(self.ldac_note)

        buttons = QHBoxLayout()
        self.ldac_action = QPushButton()
        self.ldac_action.clicked.connect(self._ldac_action)
        buttons.addWidget(self.ldac_action)
        self.ldac_remove = QPushButton("Remove")
        self.ldac_remove.setObjectName("Ghost")
        self.ldac_remove.clicked.connect(self._ldac_uninstall)
        buttons.addWidget(self.ldac_remove)
        buttons.addStretch(1)
        audio.add(self._bar(buttons))

        # Hidden until something runs. The build takes a while and downloads
        # the PipeWire sources on its first run, so a silent spinner would look
        # like a hang; the script's own output is the honest progress report.
        self.ldac_log = QPlainTextEdit()
        self.ldac_log.setReadOnly(True)
        self.ldac_log.setMaximumHeight(150)
        self.ldac_log.setVisible(False)
        audio.add(self.ldac_log)

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

        outer.addWidget(screen)
        self.audio_card.setVisible(platform.supported("bluetooth_audio"))
        outer.addWidget(self.audio_card)
        outer.addStretch(1)

        self.toast = Toast(self)
        hub.companion.connectedChanged.connect(self._refresh)
        # The phone re-sends its capability list after the grant is made.
        hub.companion.capabilitiesChanged.connect(lambda _c: self._refresh_grant())
        hub.companion.errorOccurred.connect(self._show_error)
        self._refresh()
        self.discover()

        # Applied a beat after the last change: a spin box being dragged emits
        # on every step, and writing the config on each one is wasteful.
        self._commit_timer = QTimer(self)
        self._commit_timer.setSingleShot(True)
        self._commit_timer.setInterval(350)
        self._commit_timer.timeout.connect(self._commit)
        self._wire_controls()
        self._loading = False

    # -- applying ------------------------------------------------------------

    def _wire_controls(self) -> None:
        """Every control writes the config as soon as it is touched."""
        for box in (
            self.autostart_box, self.minimised_box, self.system_apps,
            self.mirror_audio, *self.feature_boxes.values(),
            *self.tile_boxes.values(),
        ):
            box.toggled.connect(self._touch)
        for spin in (self.max_size, self.mirror_fps, self.panel_width,
                     self.panel_width_full):
            spin.valueChanged.connect(self._touch)
        for combo in (self.clipboard_mode, self.codec_choice, self.audio_route):
            combo.currentIndexChanged.connect(self._touch)
        self.window_size.textEdited.connect(self._touch)

    def _touch(self, *_args) -> None:
        if not self._loading:
            self._commit_timer.start()

    # -- the phone's audio ---------------------------------------------------

    def _grant_projection(self) -> None:
        """Ask the phone to stop asking, through Shizuku.

        Android makes consent single-use, so without this the phone has to be
        picked up before every stream -- which defeats the point of the feature.
        The grant is made once and can be undone in the companion app.
        """
        self.grant_state.setText("Asking the phone...")
        self.grant_button.setEnabled(False)

        def replied(message: dict) -> None:
            self.grant_button.setEnabled(True)
            if message.get("t") == "error":
                self.grant_state.setText(message.get("message", "It was refused."))
                return
            self.grant_state.setText("Done. Audio now starts without the phone.")
            self.toast.show_message(
                "The phone will not ask again", self.palette_tokens, "success"
            )
            self._refresh_grant()

        self.hub.companion.request({"t": "audio_grant"}, replied)

    def _refresh_grant(self) -> None:
        """Say where the one-time permission stands, in plain words."""
        caps = self.hub.companion.capabilities
        if not self.hub.connected:
            self.grant_button.setVisible(False)
            self.grant_state.setText("")
            return
        if "phone_audio" not in caps:
            self.grant_button.setVisible(False)
            self.grant_state.setText(
                "This phone's companion app cannot send audio."
            )
            return
        if "phone_audio_silent" in caps:
            self.grant_button.setVisible(False)
            self.grant_state.setText(
                "The phone starts audio without asking."
            )
            return
        if "phone_audio_grant" in caps:
            self.grant_button.setVisible(True)
            self.grant_state.setText(
                "Android asks on the phone before every stream. This grants the "
                "one-time permission that stops it, through Shizuku."
            )
            return
        self.grant_button.setVisible(False)
        self.grant_state.setText(
            "Android asks on the phone before every stream. Shizuku is needed "
            "to stop that; start it on the phone and this will offer to."
        )

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

    def _commit(self) -> None:
        """Write what the controls say, and make it take effect now."""
        cfg = self.hub.config.mirror
        cfg.max_size = self.max_size.value()
        cfg.fps = self.mirror_fps.value()
        cfg.app_window_size = self.window_size.text().strip() or "1280x800"
        cfg.show_system_apps = self.system_apps.isChecked()
        cfg.audio = self.mirror_audio.isChecked()

        for key, box in self.feature_boxes.items():
            setattr(self.hub.config.features, key, box.isChecked())

        panel = self.hub.config.panel
        panel.width = self.panel_width.value()
        panel.width_fullscreen = self.panel_width_full.value()
        # Kept in the catalogue's order, which is the order they are drawn in.
        panel.tiles = [
            key for key, box in self.tile_boxes.items() if box.isChecked()
        ]

        self.hub.config.start_minimised = self.minimised_box.isChecked()
        if self.autostart_box.isChecked() != autostart.enabled():
            if not autostart.set_enabled(self.autostart_box.isChecked()):
                self.toast.show_message(
                    "Could not write the autostart entry", self.palette_tokens, "danger"
                )
                self.autostart_box.setChecked(autostart.enabled())

        self.hub.config.phone_audio.route = (
            self.audio_route.currentData() or "bluetooth"
        )

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
        self.toast.show_message("Saved", self.palette_tokens, "success", 1200)

    def _show_error(self, message: str) -> None:
        self.pair_status.setText(message)

    def reload_panel_widths(self) -> None:
        """Pick up a width the user set by dragging instead of typing."""
        panel = self.hub.config.panel
        for box, value in ((self.panel_width, panel.width),
                           (self.panel_width_full, panel.width_fullscreen)):
            box.blockSignals(True)
            box.setValue(value)
            box.blockSignals(False)

    def _refresh(self) -> None:
        self._refresh_grant()
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
