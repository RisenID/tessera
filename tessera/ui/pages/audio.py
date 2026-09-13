"""The phone's audio on this computer, by either of the two routes.

Two of them, and they are not equivalent:

* **over the link** -- the companion app sends a copy of the phone's media mix
  over the connection the app already holds. No pairing, no profile, and it
  cannot take audio away from the phone's own headphones because it is a copy.
  Being a copy also means the phone plays it too, which is why there is a box
  on the card to keep the phone quiet while it runs -- the capture happens
  before the phone's volume stage, so silencing the phone costs this side
  nothing. This is the route Phone Link uses, and it works on every platform.
* **over Bluetooth** -- the phone becomes an A2DP source, which *does* move its
  audio here, and is the only route that can carry a call's microphone.

Bluetooth leads. It *moves* the sound rather than copying it, so the phone does
not end up playing the same track alongside this computer, and it is the only
route that can carry a call. The link route is the fallback for a computer with
no Bluetooth radio, and is switched on in Settings.

Connecting still moves nothing on its own, by either route. The Bluetooth
connection made here leaves the media profile alone, so audio moves only when
the button on this page asks for it.
"""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ...backends import audio, bluetooth, btcodecs, phone_audio
from ...backends.mpris import MprisPlayer
from ...core import platform
from ...core.hub import Hub
from ...core.proc import submit
from ..theme import SPACE, Palette
from ..widgets import Card, Pill, Toast, heading


def say(label: QLabel, text: str) -> None:
    """Put *text* on *label*, and take the label away when there is none.

    An empty QLabel is not nothing: it holds its line of height and leaves a
    band of blank card under the buttons. These three labels are empty most of
    the time -- they exist for the one sentence that explains a failure.
    """
    label.setText(text)
    label.setVisible(bool(text))


class AudioPage(QWidget):
    """One page for both Bluetooth audio roles, because they share a radio."""

    #: The phone's state changes from the phone's side, so poll while visible.
    REFRESH_MS = 4000
    #: Polls of two seconds each spent waiting for the phone to start playing.
    WATCH_TICKS = 150

    def __init__(self, hub: Hub, palette: Palette, parent: QWidget | None = None):
        super().__init__(parent)
        self.hub = hub
        self.palette_tokens = palette
        self._device: bluetooth.BtDevice | None = None
        self._card: audio.BtCard | None = None
        self._player = MprisPlayer(self)
        self._service = ""
        self._busy = False
        #: Node carrying the phone's audio while it is linked to an output.
        self._stream_node = ""
        #: Poll that links the phone's audio once it actually starts arriving.
        self._watch: QTimer | None = None
        #: Polls spent on the current wait, so it cannot run forever.
        self._waited = 0

        # Four cards do not fit in a window this tall, and a layout with no
        # room shrinks every widget towards its minimum rather than refusing:
        # word-wrapped text collapses to one clipped line and buttons lose
        # half their height, which is what this page looked like. It scrolls
        # now, as every other page of this length already does.
        page = QVBoxLayout(self)
        page.setContentsMargins(SPACE["xl"], SPACE["xl"], SPACE["xl"], 0)
        page.setSpacing(SPACE["lg"])
        page.addWidget(
            heading(
                "Audio",
                "Music and calls over Bluetooth, or over the link where there "
                "is no Bluetooth",
            )
        )

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        page.addWidget(scroll, 1)

        host = QWidget()
        scroll.setWidget(host)
        outer = QVBoxLayout(host)
        outer.setContentsMargins(0, 0, SPACE["md"], SPACE["xl"])
        outer.setSpacing(SPACE["lg"])

        # Built first because the rest of the page reads its widgets, added
        # last because it is the fallback: Bluetooth is the route this app
        # leads with, and the one that can carry a call.
        link_card = self._build_link_card(palette)

        # -- connection ------------------------------------------------------
        link = Card(self)
        row = QHBoxLayout()
        title = QLabel("Bluetooth")
        title.setObjectName("SectionTitle")
        row.addWidget(title)
        row.addStretch(1)
        self.link_pill = Pill("Not connected", "muted")
        self.link_pill.apply(palette)
        row.addWidget(self.link_pill)
        link.body().addLayout(row)

        self.device_label = QLabel()
        self.device_label.setObjectName("Muted")
        self.device_label.setWordWrap(True)
        link.add(self.device_label)

        buttons = QHBoxLayout()
        self.connect_button = QPushButton("Connect")
        self.connect_button.setObjectName("Primary")
        self.connect_button.clicked.connect(self._toggle_connection)
        buttons.addWidget(self.connect_button)

        self.forget_button = QPushButton("Forget device")
        self.forget_button.setObjectName("Danger")
        self.forget_button.clicked.connect(self._forget)
        self.forget_button.setVisible(False)
        buttons.addWidget(self.forget_button)
        buttons.addStretch(1)
        link.body().addLayout(buttons)

        self.link_status = QLabel()
        self.link_status.setObjectName("Muted")
        self.link_status.setWordWrap(True)
        link.add(self.link_status)
        outer.addWidget(link)
        self.bluetooth_card = link

        # -- what the audio link is doing ------------------------------------
        mode = Card(self)
        mode_title = QLabel("Audio mode")
        mode_title.setObjectName("SectionTitle")
        mode.add(mode_title)

        explain = QLabel(
            "Connecting moves no audio on its own. Music and calls cannot run "
            "at once — Bluetooth carries one at a time."
        )
        explain.setObjectName("Muted")
        explain.setWordWrap(True)
        mode.add(explain)

        mode_buttons = QHBoxLayout()
        self.music_button = QPushButton("Play phone audio here")
        self.music_button.setObjectName("Primary")
        self.music_button.clicked.connect(lambda: self._set_mode("music"))
        mode_buttons.addWidget(self.music_button)

        self.call_button = QPushButton("Take calls here")
        self.call_button.clicked.connect(lambda: self._set_mode("call"))
        mode_buttons.addWidget(self.call_button)

        # Named for what it does rather than for what it stops. Handing the
        # audio back to whatever the phone was using is the thing people want,
        # and "stop" read as though it silenced the music altogether.
        self.handback_button = QPushButton("Play on the phone again")
        self.handback_button.clicked.connect(self._park)
        mode_buttons.addWidget(self.handback_button)
        mode_buttons.addStretch(1)
        mode.body().addLayout(mode_buttons)

        self.mode_status = QLabel()
        self.mode_status.setObjectName("Muted")
        self.mode_status.setWordWrap(True)
        mode.add(self.mode_status)
        outer.addWidget(mode)
        self.mode_card = mode

        # -- now playing -----------------------------------------------------
        media = Card(self)
        media_title = QLabel("Now playing")
        media_title.setObjectName("SectionTitle")
        media.add(media_title)

        self.track_label = QLabel("Nothing playing")
        self.track_label.setWordWrap(True)
        self.track_label.setStyleSheet("font-weight: 600;")
        media.add(self.track_label)

        self.album_label = QLabel()
        self.album_label.setObjectName("Muted")
        media.add(self.album_label)

        transport = QHBoxLayout()
        for label, action in (("⏮", "Previous"), ("⏯", "PlayPause"), ("⏭", "Next")):
            button = QPushButton(label)
            button.setFixedWidth(56)
            button.clicked.connect(lambda _checked=False, a=action: self._control(a))
            transport.addWidget(button)
        transport.addStretch(1)
        media.body().addLayout(transport)
        outer.addWidget(media)
        self.media_card = media

        outer.addWidget(link_card)

        #: The Bluetooth half of the page, shown only where Bluetooth audio can
        #: work: it is absent on Windows, and can be switched off anywhere.
        self._bluetooth_cards = [link, mode, media]
        self._apply_availability()

        outer.addStretch(1)

        self.toast = Toast(self)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self.refresh()

    # -- the link route ------------------------------------------------------

    def _build_link_card(self, palette: Palette) -> QWidget:
        """The card for audio over the companion link."""
        card = self.link_card = Card(self)

        row = QHBoxLayout()
        title = QLabel("Over the link")
        title.setObjectName("SectionTitle")
        row.addWidget(title)
        row.addStretch(1)
        self.stream_pill = Pill("Not playing", "muted")
        self.stream_pill.apply(palette)
        row.addWidget(self.stream_pill)
        card.body().addLayout(row)

        note = QLabel(
            "The fallback for a computer with no Bluetooth. Plays whatever the "
            "phone is playing over the connection this app already has — a copy "
            "of the phone's mix, so it cannot carry a call and cannot take the "
            "sound away from the phone's own headphones."
        )
        note.setObjectName("Muted")
        note.setWordWrap(True)
        card.add(note)

        # A copy means both play it. With the phone on the desk that is the
        # same track twice, a fraction of a second apart, which is worse than
        # either on its own -- so the phone is muted by default and this is how
        # to stop that. It belongs here rather than in Settings: the answer
        # depends on where the phone is right now, not on how the app is set up.
        self.mute_phone = QCheckBox("Keep the phone quiet while it plays here")
        self.mute_phone.setChecked(self.hub.config.phone_audio.mute_phone)
        self.mute_phone.toggled.connect(self._set_muted)
        card.add(self.mute_phone)

        buttons = QHBoxLayout()
        self.stream_button = QPushButton("Play the phone's audio here")
        self.stream_button.setObjectName("Primary")
        self.stream_button.clicked.connect(self.hub.toggle_phone_audio)
        buttons.addWidget(self.stream_button)
        buttons.addStretch(1)

        volume_label = QLabel("Volume")
        volume_label.setObjectName("Muted")
        buttons.addWidget(volume_label)
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setFixedWidth(160)
        self.volume.setValue(self.hub.config.phone_audio.volume)
        self.volume.valueChanged.connect(self._set_volume)
        buttons.addWidget(self.volume)
        card.body().addLayout(buttons)

        # A meter, because "is it playing or is the phone silent?" is the first
        # question when nothing comes out, and the answer is not otherwise
        # visible anywhere.
        self.level = QProgressBar()
        self.level.setRange(0, 100)
        self.level.setTextVisible(False)
        self.level.setFixedHeight(6)
        self.level.setValue(0)
        self.level.setVisible(False)
        card.add(self.level)

        self.stream_status = QLabel()
        self.stream_status.setObjectName("Muted")
        self.stream_status.setWordWrap(True)
        card.add(self.stream_status)

        self.hub.phoneAudioChanged.connect(self._on_stream_changed)
        self.hub.phoneAudioWaiting.connect(self._on_stream_waiting)
        self.hub.phoneAudioLevel.connect(self._on_level)
        self._apply_stream_state()
        return card

    def _set_muted(self, muted: bool) -> None:
        self.hub.set_phone_muted(muted)
        # Only worth saying while it is playing; otherwise it is a preference
        # about something that is not happening.
        if self.hub.phone_audio_active:
            self._apply_stream_state()

    def _set_volume(self, value: int) -> None:
        self.hub.phone_audio.set_volume(value)
        self.hub.config.phone_audio.volume = int(value)
        self.hub.config.save()

    def _on_stream_changed(self, _playing: bool) -> None:
        self._apply_stream_state()

    def _on_stream_waiting(self, message: str) -> None:
        self.stream_pill.setText("Asking the phone")
        self.stream_pill.apply(self.palette_tokens, "warning")
        say(self.stream_status, message)
        self.stream_button.setText("Cancel")

    def _on_level(self, peak: float) -> None:
        if not self.isVisible():
            return
        self.level.setValue(int(min(1.0, peak) * 100))

    def _apply_stream_state(self) -> None:
        playing = self.hub.phone_audio_active
        pending = self.hub.phone_audio_pending

        if playing:
            self.level.setVisible(True)
            self.stream_pill.setText("Playing here")
            self.stream_pill.apply(self.palette_tokens, "success")
            self.stream_button.setText("Stop")
            quiet = (
                "The phone is silent while this plays; its volume comes back "
                "when it stops. "
                if self.hub.phone_muted else
                "The phone is playing this too — tick the box above to silence "
                "it. "
            )
            say(self.stream_status, 
                quiet + "An app on the phone can refuse to be captured, and "
                "most that play protected audio do; those arrive as silence."
            )
        elif pending:
            self._on_stream_waiting(
                "Waiting for the phone. Android asks every time."
            )
            return
        else:
            self.stream_pill.setText("Not playing")
            self.stream_pill.apply(self.palette_tokens, "muted")
            self.stream_button.setText("Play the phone's audio here")
            say(self.stream_status, self._link_note())
            self.level.setValue(0)
            self.level.setVisible(False)

    def _link_note(self) -> str:
        """Why the button might not work, before it is pressed."""
        if not phone_audio.available():
            return "This computer has no audio output Qt can play through."
        if not self.hub.config.features.phone_audio:
            return "Switched off in Settings."
        if not self.hub.connected:
            return "The companion app is not connected."
        caps = self.hub.companion.capabilities
        if "phone_audio" not in caps:
            return (
                "This phone is not offering audio: the companion app needs "
                "Android 10 or later, and may be older than this feature."
            )
        if "phone_audio_silent" not in caps:
            return (
                "Android will ask on the phone before it starts. Settings can "
                "grant the one-time permission that stops it asking."
            )
        return ""

    def _apply_availability(self) -> None:
        """Show only the routes this computer and these settings allow."""
        # An older companion app takes the audio commands but not this one, and
        # a checkbox that silently does nothing is worse than no checkbox.
        known = self.hub.companion.capabilities
        self.mute_phone.setEnabled(not known or "phone_audio_mute" in known)
        bluetooth_possible = (
            platform.supported("bluetooth_audio")
            and self.hub.config.features.bluetooth_audio
        )
        for card in self._bluetooth_cards:
            card.setVisible(bluetooth_possible)
        self.link_card.setVisible(self.hub.config.features.phone_audio)

    # -- only while anyone is looking ----------------------------------------
    #
    # Reading this page's state is not cheap: finding the phone, reading the
    # PipeWire card and asking BlueZ what the media transport is doing, plus a
    # pw-dump when audio is actually flowing. On a timer that ran regardless of
    # whether the page was on screen, that came to several thousand process
    # spawns and tens of megabytes of JSON parsed per hour for a page nobody
    # was looking at -- and it kept going while the window was minimised.
    #
    # A page in a QStackedWidget is hidden when another is selected and when
    # the window is minimised, so these two are exactly the right hooks.

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._apply_availability()
        self._apply_stream_state()
        self._timer.start(self.REFRESH_MS)
        self.refresh()

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        self._timer.stop()
        # Nobody is waiting to be told the audio arrived, either.
        self._stop_watch()

    # -- state ---------------------------------------------------------------

    def refresh(self) -> None:
        if self._busy:
            return
        submit(self._read_state, on_done=self._apply_state, on_error=lambda _m: None)

    def quick_toggle(self) -> None:
        """Play the phone's audio here, or stop.

        The panel's switch calls this. Either route only ever starts on a
        click, and this is that click; which route is the user's choice, in
        Settings. "Whichever works" prefers Bluetooth, because it moves the
        sound rather than copying it -- and falls back to the link on a
        computer whose Bluetooth cannot, or will not, carry it.
        """
        if self.hub.phone_audio_active or self.hub.phone_audio_pending:
            self.hub.stop_phone_audio()
            return
        if self.hub.bluetooth_streaming or self._stream_node:
            self._park()
            return

        route = self.hub.config.phone_audio.route
        if route == "link":
            if self._link_usable():
                self.hub.start_phone_audio()
                return
            # Asked for explicitly, so say why it cannot rather than quietly
            # doing something else to the phone's audio.
            self.toast.show_message(
                self._link_note() or "The link route is not available.",
                self.palette_tokens, "danger",
            )
            return

        # Bluetooth, or whichever works. "Whichever" means the card this
        # computer can actually stream through: without a connected phone
        # there is no profile to switch, and the link is then the only route
        # that can answer the button at all.
        if route == "bluetooth" or self._card is not None:
            self._set_mode("music")
            return
        if self._link_usable():
            self.hub.start_phone_audio()
            return
        self._set_mode("music")

    def _link_usable(self) -> bool:
        """Whether audio over the link could start right now."""
        return (
            self.hub.config.features.phone_audio
            and self.hub.connected
            and "phone_audio" in self.hub.companion.capabilities
            and phone_audio.available()
        )

    def _read_state(self) -> tuple:
        """Gather Bluetooth and audio state off the GUI thread.

        Read from what is happening -- is the media profile connected, is a
        stream flowing -- not from PipeWire's profile, which is always left
        ready to receive and so would claim playback the moment it connected.
        """
        device = bluetooth.find_phone(
            preferred_address=self.hub.config.bluetooth.address,
            name_hint=self.hub.phone_name,
        )
        if device is None:
            return None, None, "", audio.Stream()

        card = audio.bluetooth_card(device.address)
        if not device.connected:
            return device, card, "", audio.Stream()

        transport = bluetooth.audio_transport(device.address)
        # Only look for the stream when BlueZ says audio is on the wire. A
        # stream node exists exactly while that is true, so asking otherwise
        # tells us nothing -- and pw-dump is a fifth of a megabyte of JSON.
        stream = (audio.music_stream()
                  if transport in bluetooth.TRANSPORT_STREAMING else audio.Stream())
        return device, card, transport, stream

    def _apply_state(self, state: tuple) -> None:
        device, card, transport, stream = state
        self._device, self._card = device, card

        if device is None:
            self.link_pill.set_state("No phone paired", "muted")
            self.device_label.setText(
                "No paired phone found. Pair the phone with this computer in "
                "the Bluetooth settings first."
            )
            self.connect_button.setEnabled(False)
            self.forget_button.setVisible(False)
            return

        # Learn the address once, so the right device is used next time even if
        # its name changes.
        if self.hub.config.bluetooth.address != device.address:
            self.hub.config.bluetooth.address = device.address
            self.hub.config.save()

        self.connect_button.setEnabled(True)
        self.connect_button.setText("Disconnect" if device.connected else "Connect")
        self.link_pill.set_state(
            "Connected" if device.connected else "Not connected",
            "success" if device.connected else "muted",
        )

        capabilities = []
        if device.can_stream_music:
            capabilities.append("music")
        if device.can_take_calls:
            capabilities.append("calls")
        self.device_label.setText(
            f"{device.label} · {device.address}"
            + (f" · supports {' and '.join(capabilities)}" if capabilities else "")
        )

        self._apply_audio(device, transport, stream)
        self._refresh_media(device)

    @property
    def _address(self) -> str:
        return self._device.address if self._device is not None else ""

    def _stop_routing(self) -> None:
        """Tear down any route we created, so stopping is complete."""
        self._stop_watch()
        if self._stream_node:
            audio.unlink_from_sink(self._stream_node)
            self._stream_node = ""

    def _release(self) -> None:
        """Give playback back to whatever the phone was using before."""
        if self._device is not None:
            bluetooth.release_audio(self._device.address)

    def _park(self) -> None:
        """Stop moving audio without disconnecting the phone.

        Only the Bluetooth profile is dropped; PipeWire's silent profile is
        never selected -- WirePlumber remembers it and restores it on every
        later connection, which is what stopped the audio arriving at all.
        """
        if self._device is None:
            return
        self._stop_routing()
        submit(
            self._release,
            on_done=lambda _r: self.refresh(),
            on_error=lambda m: self.toast.show_message(m[:120], self.palette_tokens, "danger"),
        )

    def _apply_audio(
        self,
        device: bluetooth.BtDevice,
        transport: str,
        stream: audio.Stream,
    ) -> None:
        """Say what the audio is doing, from what it is actually doing."""
        self._stream_node = stream.node
        streaming = bool(stream)
        claimed = bool(transport)

        if not device.connected:
            self.music_button.setEnabled(False)
            self.call_button.setEnabled(False)
            self.handback_button.setEnabled(False)
            say(self.mode_status, 
                "Connect the phone to play its audio here or take calls."
            )
            return

        self.music_button.setEnabled(not streaming)
        self.call_button.setEnabled(True)
        self.handback_button.setEnabled(claimed)

        if streaming:
            say(self.mode_status, self._playing_note(stream))
        elif claimed:
            # The profile is connected and nothing is coming down it, which is
            # what a paused phone looks like. Saying "playing" here was the
            # old bug: it read PipeWire's profile, which is now always left
            # ready, so it announced playback the moment Bluetooth connected.
            say(self.mode_status, 
                "Ready. The phone's audio plays here as soon as it starts."
            )
        else:
            say(self.mode_status, 
                "Connected for track details and call control. No audio is "
                "being moved, so anything already playing is untouched."
            )

    def _refresh_media(self, device: bluetooth.BtDevice) -> None:
        if not device.connected or not device.has_media_controls:
            self.track_label.setText("Nothing playing")
            self.album_label.setText("")
            return

        # bluez's bridge publishes the phone's player on the session bus; start
        # it on demand so the phone also appears in the desktop's media applet.
        if not self._player.proxy_running and self._player.proxy_available():
            try:
                self._player.start_proxy()
            except RuntimeError as exc:
                self.album_label.setText(str(exc))
                return

        self._service = self._player.find_player(device.address, device.name)
        track = self._player.track(self._service)
        self.track_label.setText(track.summary)
        self.album_label.setText(track.album)

    # -- actions -------------------------------------------------------------

    def _toggle_connection(self) -> None:
        """Connect or disconnect, through the hub rather than directly.

        The hub owns the quiet connect and, more importantly, owns whether the
        phone is *meant* to be connected: it reconnects by itself on a timer,
        and a disconnect made here has to stop that. Doing the work locally
        left the two disagreeing -- the link came back fifteen seconds after
        being dropped on purpose.
        """
        device = self._device
        if device is None or self._busy:
            return
        self._busy = True
        connecting = not device.connected
        say(self.link_status, "Connecting..." if connecting else "Disconnecting...")

        def done(_label: object = "") -> None:
            self._busy = False
            say(self.link_status, "")
            self.forget_button.setVisible(False)
            self.refresh()

        def failed(message: str) -> None:
            self._busy = False
            say(self.link_status, message)
            # A stale pairing is the common failure and needs an explicit fix.
            self.forget_button.setVisible("pair again" in message.lower())
            self.toast.show_message("Bluetooth failed", self.palette_tokens, "danger")

        if connecting:
            self.hub.connect_bluetooth(on_done=done, on_error=failed)
        else:
            self.hub.disconnect_bluetooth(on_done=done, on_error=failed)

    def _forget(self) -> None:
        device = self._device
        if device is None:
            return
        submit(
            bluetooth.forget,
            device.address,
            on_done=lambda _r: (
                say(self.link_status, 
                    "Removed. Now pair the phone again from the Bluetooth settings "
                    "on both devices."
                ),
                self.refresh(),
            ),
            on_error=lambda m: say(self.link_status, m),
        )

    def _set_mode(self, mode: str) -> None:
        card = self._card
        if card is None:
            return
        profile = card.music_profile if mode == "music" else card.call_profile
        if not profile:
            self.toast.show_message(
                "The phone did not offer that profile", self.palette_tokens, "warning"
            )
            return

        def work() -> str:
            audio.set_profile(card.name, profile)

            if mode == "call":
                if self.hub.config.bluetooth.route_calls:
                    sink, source = audio.nodes_for(card.name)
                    if sink:
                        audio.set_default_sink(sink)
                    if source:
                        audio.set_default_source(source)
                return "Call audio is connected."

            self._stop_routing()

            # Order matters. The card has to be able to accept a stream before
            # the phone is asked for one, because the phone withdraws its offer
            # within a few seconds of making it.
            audio.ready_to_receive(self._address)

            # The received audio appears as a playback stream rather than a
            # source: PipeWire names it bluez_input.<address>.<n> with media
            # class Stream/Output/Audio. It exists only while audio is actually
            # flowing, so its absence usually means nothing is playing yet
            # rather than that anything is broken.
            #
            # Asking twice is not belt and braces. Android decides where a
            # playback session goes when it handles the connection, and a
            # request that lands while it is busy -- mid-track, screen off --
            # is simply dropped, which is why the button worked only sometimes.
            for attempt in range(2):
                bluetooth.claim_audio(self._address)
                stream = audio.wait_for_stream(attempts=16 if attempt == 0 else 24)
                if stream:
                    self._link(stream)
                    return self._playing_note(stream)

            return self._explain_silence()

        submit(
            work,
            on_done=lambda note: (self.refresh(), self.toast.show_message(
                str(note)[:130], self.palette_tokens, "success")),
            on_error=lambda m: self.toast.show_message(m[:130], self.palette_tokens, "danger"),
        )

    # -- waiting for the phone to play ---------------------------------------

    @staticmethod
    def _playing_note(stream: audio.Stream) -> str:
        """What is playing, and the ceiling the codec puts on it.

        Codec, sample rate and bit rate together: the codec sets the ceiling,
        the rate is read from the stream, and the bit rate is the number that
        actually separates LDAC from aptX from SBC.
        """
        codec = btcodecs.CODEC_NAMES.get(stream.codec, stream.codec)
        kbps = btcodecs.BITRATES.get(codec)
        detail = " · ".join(
            part for part in (codec, stream.quality, f"{kbps} kbit/s" if kbps else "")
            if part
        )
        suffix = f" — {detail}" if detail else ""
        return f"Playing the phone's audio through this computer{suffix}."

    def _link(self, stream: audio.Stream) -> None:
        """Make a received stream audible, unless something already has."""
        if not audio.stream_linked(stream.node):
            audio.link_to_sink(stream.node)
        self._stream_node = stream.node

    def _explain_silence(self) -> str:
        """Say why no sound is arriving, distinguishing the causes.

        A media transport exists only once the phone has this computer as an
        output, so BlueZ can tell "not selected" from "selected but paused".
        """
        self._watch_for_stream()
        state = bluetooth.audio_transport(self._address)

        if not state:
            return (
                "Ready, but the phone has not sent its audio here yet. On the "
                "phone, pick this computer in the output picker."
            )

        media = getattr(self.hub, "media", None) or {}

        # Android suspends Bluetooth music outright while the phone is ringing
        # or in a call, including calls owned by an app rather than the dialler
        # -- and a call left ringing keeps it suspended indefinitely. Nothing on
        # this side can tell that from a phone that simply is not playing, so
        # the companion reports the phone's audio mode.
        busy = {
            "ringtone": "The phone is ringing",
            "in_call": "The phone is on a call",
            "in_communication": "The phone is on a call",
        }.get(str(media.get("audioMode") or ""))
        if busy:
            return (
                f"Connected and ready. {busy}, and Android holds Bluetooth music "
                "back until that ends — playback starts here by itself once it does."
            )

        if media and not media.get("playing"):
            title = str(media.get("title") or "").strip()
            paused = f" {title} is paused." if title else ""
            return (
                "Connected and ready." + paused + " Press play on the phone and "
                "the sound will arrive here."
            )

        return (
            "Connected and ready, but the phone is not sending audio yet. It "
            "starts playing here as soon as it does — a call or an alarm on "
            "the phone holds its music back until that finishes."
        )

    def _watch_for_stream(self) -> None:
        """Link the phone's audio the moment it starts arriving.

        Pressing play happens on the phone, seconds or minutes after the button
        here, so waiting once and giving up would leave the audio unlinked for
        exactly the common case.
        """
        if self._watch is not None:
            return
        self._waited = 0
        self._watch = QTimer(self)
        self._watch.setInterval(2000)
        self._watch.timeout.connect(self._poll_stream)
        self._watch.start()

    def _stop_watch(self) -> None:
        self._waited = 0
        if self._watch is not None:
            self._watch.stop()
            self._watch.deleteLater()
            self._watch = None

    def _poll_stream(self) -> None:
        # The profile is always left ready to receive now, so it says nothing
        # about whether the wait is still worth making. Being connected does.
        if self._device is None or not self._device.connected:
            self._stop_watch()
            return

        # Give up eventually. Without this the wait outlives any plausible
        # "I am about to press play": the phone stays connected, nothing ever
        # arrives, and the poll goes on reading pw-dump every two seconds for
        # as long as the app is open.
        self._waited += 1
        if self._waited > self.WATCH_TICKS:
            self._stop_watch()
            say(self.mode_status, 
                "Stopped waiting for the phone to play. Press the button again "
                "when something is playing on it."
            )
            return

        def look() -> audio.Stream:
            stream = audio.music_stream()
            if stream and not audio.stream_linked(stream.node):
                audio.link_to_sink(stream.node)
            return stream

        def arrived(stream: object) -> None:
            if not stream:
                return
            self._stream_node = stream.node  # type: ignore[union-attr]
            self._stop_watch()
            say(self.mode_status, self._playing_note(stream))  # type: ignore[arg-type]
            self.toast.show_message(
                "The phone's audio is playing here", self.palette_tokens, "success"
            )

        submit(look, on_done=arrived, on_error=lambda _m: None)

    def _control(self, action: str) -> None:
        try:
            self._player.control(self._service, action)
        except RuntimeError as exc:
            self.toast.show_message(str(exc)[:120], self.palette_tokens, "warning")
        QTimer.singleShot(400, self.refresh)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.toast._reposition()
