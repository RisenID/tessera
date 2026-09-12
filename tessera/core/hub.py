"""Central coordinator.

Owns every backend and presents one surface to the UI, so pages never have to
know whether a notification arrived over the companion link or KDE Connect, or
whether Do Not Disturb is being read from the phone or mirrored to Plasma.

Source preference is deliberate: the companion app first, because it is
event-driven and costs no battery; KDE Connect and adb fill in when it is not
paired yet.
"""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal

from ..backends import adb, mirror
from ..backends import audio as bt_audio
from ..backends import bluetooth, btcodecs
from ..backends.companion import CompanionClient, PairedPhone, b64decode
from ..backends.dnd import MODE_OFF, DndSync, ZenMode
from ..backends.kdeconnect import KdeConnect
from ..backends.webcam import CompanionCamera, Webcam, WebcamError
from ..ui.icons import IconStore
from . import otp
from .clipboard import ClipboardSync
from . import platform
from .config import Config
from .models import Notification
from .proc import submit

log = logging.getLogger(__name__)

SOURCE_COMPANION = "companion"
SOURCE_KDECONNECT = "kdeconnect"
SOURCE_NONE = "none"


class Hub(QObject):
    """The application's model layer."""

    statusChanged = Signal(str)
    connectionChanged = Signal(bool)
    notificationsChanged = Signal()
    notificationArrived = Signal(object)     # Notification, for a desktop popup
    otpArrived = Signal(object, object)      # OtpMatch, Notification
    textMessageArrived = Signal()            # a text landed; reload the thread list
    dndChanged = Signal(str)
    batteryChanged = Signal(int, bool)
    phoneStatusChanged = Signal(dict)        # battery detail, signal, ringer
    capabilitiesChanged = Signal(list)
    deviceCapsChanged = Signal(dict)         # cameras + hotspot bands
    cameraStarted = Signal(str)              # /dev/videoN
    cameraStopped = Signal()
    cameraFailed = Signal(str)
    callChanged = Signal(dict)
    mediaChanged = Signal(dict)
    bluetoothStreamingChanged = Signal(bool)
    hotspotChanged = Signal(bool)            # joined the phone's hotspot
    errorOccurred = Signal(str)

    def __init__(self, config: Config, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.config = config

        self.companion = CompanionClient(self._load_phone(), self)
        self.kdeconnect = KdeConnect(self)
        self.dnd = DndSync(config.dnd, self)
        # Two ways to get video: the companion app encodes on the phone and
        # sends frames over the existing link, or scrcpy pulls them over adb.
        # The companion path is preferred -- it needs no adb at all.
        self.webcam = Webcam(config.webcam, self)
        self.companion_camera = CompanionCamera(config.webcam, self)
        self.mirrors = mirror.MirrorManager(self)
        self.icons = IconStore(self.companion, self)
        self.clipboard = ClipboardSync(self.companion, config.clipboard, self)

        self._device_caps: dict[str, Any] = {}
        self._call: dict[str, Any] = {"state": "idle"}
        self._bluetooth_name = ""
        #: Whether the phone was connected at the previous check. None until
        #: the first one, which is what tells a link coming up now -- worth
        #: acting on -- apart from one that was already up before Tessera
        #: started, which is somebody's existing arrangement to leave alone.
        self._bluetooth_settled: bool | None = None
        #: Set once the phone has told us which codecs it can send.
        self._codecs_learned = False
        self._bluetooth_streaming = False
        self._media: dict[str, Any] = {}
        self._phone_status: dict[str, Any] = {}
        self._hotspot_joined = False
        self._notifications: dict[str, Notification] = {}
        self._otp_seen: set[str] = set()
        self._serial = ""
        self._phone_dnd = "off"

        self._wire_companion()
        self._wire_kdeconnect()
        self._wire_dnd()
        self._wire_camera()
        self._apply_codec_preference()

        # adb is only needed for scrcpy now, so resolve it lazily and quietly.
        self._serial_timer = QTimer(self)
        self._serial_timer.timeout.connect(self.refresh_adb)
        if platform.supported("bluetooth_audio"):
            self._serial_timer.timeout.connect(self._watch_bluetooth)
        self._serial_timer.start(15_000)

    # -- persistence ---------------------------------------------------------

    def _load_phone(self) -> PairedPhone:
        saved = self.config.companion
        return PairedPhone(
            host=saved.host,
            port=saved.port or 8765,
            token=saved.token,
            fingerprint=saved.fingerprint,
            name=saved.name,
            device_id=saved.device_id,
        )

    def save_phone(self) -> None:
        phone = self.companion.phone
        saved = self.config.companion
        saved.host = phone.host
        saved.port = phone.port
        saved.token = phone.token
        saved.fingerprint = phone.fingerprint
        saved.name = phone.name
        saved.device_id = phone.device_id
        self.config.save()

    # -- wiring --------------------------------------------------------------

    def _wire_companion(self) -> None:
        self.companion.notificationPosted.connect(self._on_companion_notification)
        self.companion.notificationRemoved.connect(self.remove_notification)
        self.companion.dndChanged.connect(self._on_phone_dnd)
        self.companion.clipboardChanged.connect(self.clipboard.apply_remote)
        self.companion.callChanged.connect(self._on_call)
        self.companion.mediaChanged.connect(self._on_media)
        self.companion.batteryChanged.connect(self.batteryChanged)
        self.companion.phoneStatusChanged.connect(self._on_phone_status)
        self.companion.statusChanged.connect(self.statusChanged)
        self.companion.errorOccurred.connect(self.errorOccurred)
        self.companion.capabilitiesChanged.connect(self.capabilitiesChanged)
        self.companion.connectedChanged.connect(self._on_companion_connected)
        self.companion.paired.connect(lambda _phone: self.save_phone())

    def _wire_kdeconnect(self) -> None:
        self.kdeconnect.notificationPosted.connect(self._on_kdeconnect_notification)
        self.kdeconnect.notificationRemoved.connect(self.remove_notification)
        self.kdeconnect.notificationsCleared.connect(self.clear_notifications)
        self.kdeconnect.batteryChanged.connect(self.batteryChanged)
        if self.config.device_id:
            self.kdeconnect.select_device(self.config.device_id)

    def _wire_dnd(self) -> None:
        self.dnd.errorOccurred.connect(self.errorOccurred)
        self.dnd.statusChanged.connect(self.statusChanged)
        # Anything that changes whether the phone is reporting DND itself.
        self.companion.connectedChanged.connect(lambda _c: self._update_dnd_source())
        self.companion.capabilitiesChanged.connect(lambda _c: self._update_dnd_source())

    def _update_dnd_source(self) -> None:
        """Stop polling over adb while the companion app is pushing DND."""
        self.dnd.set_pushed(
            self.companion.connected
            and self.config.features.dnd_sync
            and self.companion.supports("dnd")
        )

    # -- lifecycle -----------------------------------------------------------

    def apply_features(self) -> None:
        """Push the feature switches down to the pieces that act on them."""
        features = self.config.features
        # A feature the platform cannot do is off regardless of the file: the
        # switch is hidden in Settings, and nothing here should try.
        for name in ("bluetooth_audio", "webcam"):
            if not platform.supported(name) and getattr(features, name, False):
                setattr(features, name, False)
        # "status" carries battery, signal and ringer; the phone sends the whole
        # frame regardless, but the list is what the desktop says it wants.
        topics = ["battery", "status"]
        if features.notifications:
            topics.append("notifications")
        if features.dnd_sync:
            topics.append("dnd")
        if features.clipboard:
            topics.append("clipboard")
        self.companion.wanted_topics = topics

        if not features.clipboard:
            self.clipboard.set_mode("off")
        if not features.dnd_sync:
            self.dnd.set_mode("off")
        self._update_dnd_source()

    def start(self) -> None:
        self.apply_features()
        phone = self.companion.phone
        if phone.configured:
            self.companion.connect_to_phone()
        elif self.kdeconnect.available and not self.config.device_id:
            devices = self.kdeconnect.devices(only_paired=True)
            if devices:
                self.config.device_id = devices[0].id
                self.kdeconnect.select_device(devices[0].id)
        self.refresh_adb()
        self.refresh_notifications()

    def reconnect(self) -> None:
        """Force a fresh connection attempt.

        Starts the address search again from scratch rather than continuing a
        backoff, which is what someone wants after moving networks or waking
        the phone.
        """
        self.statusChanged.emit("Reconnecting...")
        phone = self.companion.phone
        if phone.token and phone.fingerprint:
            self.companion.disconnect_from_phone()
            self.companion.connect_to_phone()
        else:
            self.statusChanged.emit("Pair a phone first, in Settings.")
        self.refresh_adb()

    def stop(self) -> None:
        self.mirrors.close_all()
        self.stop_camera()
        self.dnd.stop()
        self.companion.disconnect_from_phone()

    @property
    def source(self) -> str:
        if self.companion.connected:
            return SOURCE_COMPANION
        selected = self.kdeconnect.selected
        if selected is not None and selected.reachable:
            return SOURCE_KDECONNECT
        return SOURCE_NONE

    @property
    def connected(self) -> bool:
        return self.source != SOURCE_NONE

    @property
    def phone_name(self) -> str:
        if self.companion.phone.name:
            return self.companion.phone.name
        selected = self.kdeconnect.selected
        return selected.label if selected is not None else "No phone"

    def _on_companion_connected(self, connected: bool) -> None:
        self.connectionChanged.emit(connected)
        if connected:
            self.icons.forget_missing()
            self.refresh_notifications()
            self.refresh_device_caps()
            return
        # Battery and signal came from the phone; showing the last reading as
        # if it were current is worse than showing nothing.
        if self._phone_status:
            self._phone_status = {}
            self.phoneStatusChanged.emit({})

    # -- adb (scrcpy only) ---------------------------------------------------

    @property
    def serial(self) -> str:
        return self._serial

    def refresh_adb(self) -> None:
        def resolve() -> str:
            try:
                return adb.resolve_serial(self.config.adb_serial)
            except adb.AdbError:
                return ""

        submit(resolve, on_done=self._set_serial, on_error=lambda _m: self._set_serial(""))

    def _set_serial(self, serial: str) -> None:
        if serial != self._serial:
            self._serial = serial
            self.dnd.set_serial(serial)

    # -- webcam ---------------------------------------------------------------

    @property
    def camera_running(self) -> bool:
        return self.companion_camera.running or self.webcam.running

    @property
    def camera_uses_companion(self) -> bool:
        """Whether the phone can encode for us, avoiding adb entirely."""
        return self.companion.connected and self.companion.supports("camera")

    def start_camera(self) -> None:
        """Begin streaming into the virtual camera by whichever route works."""
        if self.camera_running:
            return
        if not self.config.features.webcam:
            raise WebcamError("The webcam is switched off in Settings.")

        if self.camera_uses_companion and self.config.webcam.source == "camera":
            device = self.companion_camera.start()
            self.companion.send(
                {
                    "t": "camera_start",
                    "cameraId": self.config.webcam.camera_id,
                    "facing": self.config.webcam.facing,
                    "width": int(self.config.webcam.size.split("x")[0]),
                    "height": int(self.config.webcam.size.split("x")[1]),
                    "fps": self.config.webcam.fps,
                }
            )
            self.cameraStarted.emit(device)
            return

        # Screen mirroring, or no companion app: fall back to scrcpy over adb.
        self.webcam.start(self._serial)

    def stop_camera(self) -> None:
        if self.companion_camera.running:
            self.companion.send({"t": "camera_stop"})
            self.companion_camera.stop()
        if self.webcam.running:
            self.webcam.stop()

    def _wire_camera(self) -> None:
        # The phone reports SPS/PPS once, in camera_started, separately from the
        # frames. ffmpeg cannot decode a single frame without it, so it has to
        # be pushed into the pipeline before anything else arrives.
        self.companion.cameraStarted.connect(self._on_phone_camera_started)
        self.companion.cameraFrame.connect(self.companion_camera.feed)
        self.companion_camera.stopped.connect(self.cameraStopped)
        self.companion_camera.failed.connect(self.cameraFailed)
        self.webcam.started.connect(self.cameraStarted)
        self.webcam.stopped.connect(self.cameraStopped)
        self.webcam.failed.connect(self.cameraFailed)
        # An error from the phone while streaming must stop the local pipeline,
        # otherwise ffmpeg sits waiting on a stream that will never arrive.
        self.companion.cameraStopped.connect(self._on_phone_camera_stopped)

    def _on_phone_camera_started(self, info: dict) -> None:
        header = b64decode(str(info.get("sps_pps", "")))
        if header:
            log.info("codec config: %d bytes", len(header))
            self.companion_camera.feed(header)
        else:
            log.warning("the phone sent no codec configuration; video may not decode")

    def _on_phone_camera_stopped(self) -> None:
        if self.companion_camera.running:
            self.companion_camera.stop()

    # -- bluetooth audio -------------------------------------------------------

    @property
    def bluetooth_name(self) -> str:
        """The phone's Bluetooth name, used to find its media player."""
        return self._bluetooth_name

    def _apply_codec_preference(self) -> None:
        """Offer the phone the codecs the settings ask for.

        The list is advertised to BlueZ once, when the session manager starts,
        so a changed list means restarting it -- done here only when the file
        actually changes, which is at most once after an upgrade or a settings
        change.
        """
        if not platform.supported("bluetooth_audio"):
            return
        if not self.config.features.bluetooth_audio:
            return

        codec = self.config.bluetooth.codec
        phone_codecs = list(self.config.bluetooth.phone_codecs)

        def apply() -> bool:
            if not btcodecs.write_preference(codec, phone_codecs):
                return False
            return btcodecs.reload_session()

        submit(apply, on_error=lambda message: log.debug("codec setup: %s", message))

    def _learn_codecs(self, phone_codecs: list) -> None:
        """Remember what the phone can send, and re-offer accordingly.

        Only useful once: the list is a property of the phone, not of this
        connection. Acting on it changes the advertised codecs, which restarts
        the audio service, so it must not be repeated on every check -- and
        _apply_codec_preference is itself a no-op when the file already says
        what it should.
        """
        if not phone_codecs:
            return
        self._codecs_learned = True
        if list(self.config.bluetooth.phone_codecs) == list(phone_codecs):
            return
        log.info("phone offers %s", ", ".join(phone_codecs))
        self.config.bluetooth.phone_codecs = list(phone_codecs)
        self.config.save()
        self._apply_codec_preference()

    def _watch_bluetooth(self) -> None:
        """Park a Bluetooth link that takes the audio path without being asked.

        Parking on our own connect button is not enough: the phone usually
        initiates, and WirePlumber then picks a profile straight away. That is
        what pulled audio off the headphones -- the phone switched its output
        to this computer while nothing here was playing it back.

        Only the moment the link comes up is treated that way. Once the phone
        has been connected for a while, audio appearing on it is a deliberate
        choice made on the phone -- the output picker -- and parking it then
        snatched the sound back a few seconds later, which is why setting the
        output to this computer appeared to do nothing at all. So after the
        first sighting the link is left alone and the stream is made audible
        instead.
        """
        if not self.config.features.bluetooth_audio:
            return

        # Parking is the whole point of this check, and turning auto_stream on
        # asks for the opposite. The codec read below still has to happen
        # either way: it is the only chance to learn what the phone can send.
        park = not self.config.bluetooth.auto_stream

        # Only a link seen coming up counts. Treating "the first check of this
        # session" as unsolicited tore down a stream that was already playing
        # when Tessera started.
        appeared = park and self._bluetooth_settled is False

        def check() -> dict:
            device = bluetooth.find_phone(
                preferred_address=self.config.bluetooth.address,
                name_hint=self.phone_name,
            )
            if device is None or not device.connected:
                return {"connected": False}

            state = {"connected": True, "name": device.name, "streaming": False}

            # One question to BlueZ answers both of the next two: what the
            # media transport is doing, and which codecs the phone can send.
            # The latter is only readable while it is connected, and "best
            # available" cannot narrow the offer safely without it.
            transport = ""
            if park or not self._codecs_learned:
                transport, codecs = bluetooth.media_state(device.address)
                if not self._codecs_learned:
                    state["phone_codecs"] = codecs

            if not park:
                return state

            # Keep the card able to accept a stream. The phone offers one the
            # instant it connects and withdraws it within seconds, so a card
            # left on the silent profile misses the offer every time -- which
            # is precisely why the phone's audio never arrived here.
            bt_audio.ready_to_receive(device.address)

            if appeared and transport:
                # Nobody asked for this. Dropping the media profile is what
                # Android acts on: it hands playback straight back to whatever
                # was playing it before, usually a pair of headphones.
                bluetooth.release_audio(device.address)
                log.info("released the media profile nobody asked for")
                return state

            # Chosen on the phone. Make it audible: the received audio is a
            # playback stream nothing is connected to until something links it.
            #
            # Only worth looking while BlueZ says audio is on the wire. The
            # stream node exists exactly then, and finding it means reading a
            # fifth of a megabyte of pw-dump output -- not something to do
            # every fifteen seconds for as long as the phone stays connected.
            if transport in bluetooth.TRANSPORT_STREAMING:
                node = bt_audio.phone_stream()
                if node:
                    if not bt_audio.stream_linked(node):
                        bt_audio.link_to_sink(node)
                        log.info("linked %s to the default output", node)
                    state["streaming"] = True
            return state

        submit(check, on_done=self._note_bluetooth, on_error=lambda _m: None)

    def _note_bluetooth(self, state: dict) -> None:
        self._bluetooth_settled = bool(state.get("connected"))
        self._learn_codecs(state.get("phone_codecs") or [])
        name = state.get("name") or ""
        if name:
            self._bluetooth_name = name
        streaming = bool(state.get("streaming"))
        if streaming != self._bluetooth_streaming:
            self._bluetooth_streaming = streaming
            self.bluetoothStreamingChanged.emit(streaming)

    @property
    def bluetooth_connected(self) -> bool:
        """Whether the phone is paired and linked over Bluetooth right now."""
        return bool(self._bluetooth_settled)

    @property
    def bluetooth_streaming(self) -> bool:
        """Whether the phone's audio is currently playing through this computer."""
        return self._bluetooth_streaming

    # -- phone status ----------------------------------------------------------

    @property
    def phone_status(self) -> dict[str, Any]:
        """Battery detail, Wi-Fi and cellular signal, ringer mode.

        Empty until the phone reports; only the companion app sends it.
        """
        return dict(self._phone_status)

    def set_ringer(self, mode: str) -> None:
        """Put the phone on normal, vibrate or silent."""
        if not self.companion.connected or not self.companion.supports("ringer"):
            self.errorOccurred.emit(
                "Changing the ringer needs the companion app on the phone."
            )
            return
        self.companion.send({"t": "ringer_set", "mode": mode})

    @property
    def ringer(self) -> str:
        value = self._phone_status.get("ringer")
        return value if isinstance(value, str) else ""

    # -- hotspot ---------------------------------------------------------------

    @property
    def hotspot_joined(self) -> bool:
        """Whether this computer is on the phone's hotspot.

        Held here rather than on the page so the panel's tile and the page
        agree; the page is what actually starts and stops it.
        """
        return self._hotspot_joined

    def set_hotspot_joined(self, joined: bool) -> None:
        if joined == self._hotspot_joined:
            return
        self._hotspot_joined = joined
        self.hotspotChanged.emit(joined)

    def _on_phone_status(self, message: dict) -> None:
        self._phone_status = {k: v for k, v in message.items() if k != "t"}
        self.phoneStatusChanged.emit(self._phone_status)

    # -- media -----------------------------------------------------------------

    @property
    def media(self) -> dict:
        """What the phone is playing, as reported by the companion app.

        Read from MediaSession rather than over Bluetooth: AVRCP would require
        connecting A2DP, which makes this computer the phone's active output
        and drags playback off whatever headphones are in use.
        """
        return dict(self._media)

    def _on_media(self, message: dict) -> None:
        self._media = message
        self.mediaChanged.emit(message)

    def media_command(self, action: str) -> None:
        if not self.companion.connected:
            self.errorOccurred.emit("No phone connected.")
            return
        self.companion.send({"t": "media_command", "action": action})

    # -- calls -----------------------------------------------------------------

    @property
    def call(self) -> dict:
        """The current call, as last reported by the phone."""
        return dict(self._call)

    def _on_call(self, message: dict) -> None:
        self._call = message
        self.callChanged.emit(message)

        # A call is no use if its audio stays on the phone, so move the
        # Bluetooth link to the call profile for the duration.
        if not self.config.features.bluetooth_audio:
            return
        if not self.config.bluetooth.route_calls:
            # Left off by default: silently moving a call onto the computer
            # would cut off a headset mid-conversation.
            return
        state = message.get("state")
        if state in ("ringing", "active"):
            submit(self._use_call_audio, on_error=lambda m: log.debug("call audio: %s", m))
        elif state == "idle":
            submit(self._use_music_audio, on_error=lambda m: log.debug("call audio: %s", m))

    def _use_call_audio(self) -> None:
        card = bt_audio.bluetooth_card(self.config.bluetooth.address)
        if card is not None and card.call_profile and card.mode != "call":
            bt_audio.set_profile(card.name, card.call_profile)
            sink, source = bt_audio.nodes_for(card.name)
            if sink:
                bt_audio.set_default_sink(sink)
            if source:
                bt_audio.set_default_source(source)

    def _use_music_audio(self) -> None:
        card = bt_audio.bluetooth_card(self.config.bluetooth.address)
        if card is not None and card.music_profile and card.mode == "call":
            bt_audio.set_profile(card.name, card.music_profile)

    # -- device capabilities -------------------------------------------------

    @property
    def device_caps(self) -> dict[str, Any]:
        """Cameras and hotspot bands as reported by the phone.

        Empty until the phone has answered; pages fall back to a conservative
        default so they still render while offline.
        """
        return dict(self._device_caps)

    def refresh_device_caps(self) -> None:
        if not self.companion.connected:
            return
        self.companion.request({"t": "device_caps"}, self._on_device_caps)

    def _on_device_caps(self, reply: dict) -> None:
        if reply.get("t") == "error":
            return
        self._device_caps = {
            "cameras": reply.get("cameras", []),
            "hotspotBands": reply.get("hotspotBands", []),
        }
        self.deviceCapsChanged.emit(self._device_caps)

    # -- notifications -------------------------------------------------------

    @property
    def notifications(self) -> list[Notification]:
        return sorted(self._notifications.values(), key=lambda n: n.when, reverse=True)

    def refresh_notifications(self) -> None:
        if self.companion.connected:
            self.companion.request(
                {"t": "notif_list"},
                lambda reply: self._ingest_many(reply.get("items", [])),
            )
        elif self.source == SOURCE_KDECONNECT:
            submit(
                self.kdeconnect.active_notifications,
                on_done=lambda notes: self._ingest_many(
                    [Notification.from_kdeconnect(n) for n in notes], raw=False
                ),
            )

    def _ingest_many(self, items: list[Any], raw: bool = True) -> None:
        for item in items:
            note = Notification.from_companion(item) if raw else item
            self._notifications[note.id] = note
            self._check_otp(note)
        self.notificationsChanged.emit()

    def _on_companion_notification(self, message: dict) -> None:
        self._add(Notification.from_companion(message))

    def _on_kdeconnect_notification(self, note: Any) -> None:
        self._add(Notification.from_kdeconnect(note))

    def _add(self, note: Notification) -> None:
        if not self.config.features.notifications:
            return
        previous = self._notifications.get(note.id)
        fresh = previous is None or note.when > previous.when
        self._notifications[note.id] = note
        self._check_otp(note)
        self.notificationsChanged.emit()
        # Only a new one, or one that has been re-posted with a later time,
        # is worth a popup; the phone re-sends the whole list on reconnect.
        if fresh and not note.ongoing:
            self.notificationArrived.emit(note)
        # A messaging app keeps one notification per conversation and re-posts
        # it with a later time for each message, so the id alone cannot tell a
        # new text from the same notification being repeated -- a reply that
        # continues an open conversation reuses the id. The post time can.
        if note.is_text_message and fresh:
            self.textMessageArrived.emit()

    def remove_notification(self, notification_id: str) -> None:
        if self._notifications.pop(notification_id, None) is not None:
            self.notificationsChanged.emit()

    def clear_notifications(self) -> None:
        self._notifications.clear()
        self.notificationsChanged.emit()

    def dismiss(self, notification_id: str) -> None:
        if self.companion.connected:
            self.companion.send({"t": "notif_dismiss", "id": notification_id})
        elif self.source == SOURCE_KDECONNECT:
            submit(self.kdeconnect.dismiss, notification_id,
                   on_error=lambda m: self.errorOccurred.emit(m))
        self.remove_notification(notification_id)

    def reply(self, notification_id: str, text: str) -> None:
        if self.companion.connected:
            self.companion.send({"t": "notif_reply", "id": notification_id, "text": text})
        elif self.source == SOURCE_KDECONNECT:
            submit(self.kdeconnect.reply, notification_id, text,
                   on_error=lambda m: self.errorOccurred.emit(m))
        else:
            self.errorOccurred.emit("No phone is connected.")

    # -- one-time passcodes --------------------------------------------------

    def _check_otp(self, note: Notification) -> None:
        """Surface a passcode as soon as its notification arrives.

        Reading codes from notifications rather than the SMS database is not
        just convenient: from Android 17, apps targeting API 37 have OTP-bearing
        SMS withheld from the provider for three hours, while the notification
        the messaging app posts is unaffected.
        """
        if not self.config.features.otp or note.id in self._otp_seen:
            return
        match = otp.find_code(note.body, note.app)
        if match is None:
            return
        self._otp_seen.add(note.id)
        self.otpArrived.emit(match, note)

    def recent_codes(self, limit: int = 12) -> list[tuple[Any, Notification]]:
        found = []
        for note in self.notifications:
            match = otp.find_code(note.body, note.app)
            if match is not None:
                found.append((match, note))
            if len(found) >= limit:
                break
        return found

    # -- do not disturb ------------------------------------------------------

    @property
    def phone_dnd(self) -> str:
        return self._phone_dnd

    def _on_phone_dnd(self, mode: str) -> None:
        self._phone_dnd = mode
        self.dndChanged.emit(mode)
        if self.config.dnd.mode != MODE_OFF:
            # The companion app pushes the phone's state; mirror it to Plasma.
            self.dnd._apply_to_desktop(_zen_from_name(mode))

    def set_phone_dnd(self, mode: str) -> None:
        if self.companion.connected:
            self.companion.send({"t": "dnd_set", "mode": mode})
            self._on_phone_dnd(mode)
        elif self._serial:
            self.dnd.set_phone(_zen_from_name(mode))
        else:
            self.errorOccurred.emit(
                "Do Not Disturb needs the companion app, or a phone attached over adb."
            )


def _zen_from_name(mode: str) -> ZenMode:
    return {
        "off": ZenMode.OFF,
        "priority": ZenMode.PRIORITY,
        "alarms": ZenMode.ALARMS_ONLY,
        "none": ZenMode.TOTAL_SILENCE,
    }.get(mode, ZenMode.OFF)
