"""Central coordinator."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from time import monotonic, sleep
from typing import Any

from PySide6.QtCore import QCoreApplication, QObject, QTimer, Signal

from ..backends import adb, mirror
from ..backends import audio as bt_audio
from ..backends import bluetooth, btcodecs
from ..backends.companion import CompanionClient, PairedPhone, b64decode
from ..backends.dnd import MODE_OFF, DndSync, ZenMode
from ..backends import mpris_server
from ..backends import storage
from ..backends.filetransfer import FileTransfers
from ..backends.kdeconnect import KdeConnect
from ..backends.phone_audio import PhoneAudio
from ..backends.webcam import CompanionCamera, Webcam, WebcamError
from ..ui.icons import IconStore
from . import otp
from ..backends.clipboard_adb import AdbClipboard
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
    #: The phone's wallpaper arrived (a path), or its colour (#rrggbb).
    wallpaperChanged = Signal(str, str)
    #: The phone's storage was mounted, unmounted, or failed to be.
    storageChanged = Signal()
    #: A file started, progressed, or ended. Carries the Transfer.
    transferChanged = Signal(object)
    #: A file finished arriving, and its path now exists.
    fileReceived = Signal(object)
    #: Connected or not, for the sidebar's Bluetooth button.
    bluetoothConnectedChanged = Signal(bool)
    #: True while a connect or disconnect this app asked for is in flight.
    bluetoothBusyChanged = Signal(bool)
    #: The phone's audio, played here over the companion link.
    phoneAudioChanged = Signal(bool)
    phoneAudioLevel = Signal(float)
    #: Android is asking on the phone; the string says what the user must do.
    phoneAudioWaiting = Signal(str)
    hotspotChanged = Signal(bool)            # joined the phone's hotspot
    errorOccurred = Signal(str)
    #: Whether a phone is reachable over adb, which mirroring and apps need.
    adbChanged = Signal(bool)
    #: The desktop's media applet asked to see the app.
    raiseRequested = Signal()

    #: How often to try bringing adb back. A failed connect costs seconds, and
    #: the phone is usually simply not listening, so this is deliberately slow.
    ADB_RETRY_SECONDS = 60.0
    #: How long to leave a phone alone between automatic Bluetooth connects.
    BLUETOOTH_RETRY_SECONDS = 60.0
    #: How long autoconnect waits for the phone to report whether media is playing.
    BLUETOOTH_MEDIA_WAIT_SECONDS = 8.0
    #: How long the media profile must stay down after connecting.
    BLUETOOTH_SETTLE_SECONDS = 6.0
    #: After connecting, keep handing back a media profile that reappears.
    BLUETOOTH_GUARD_SECONDS = 60.0
    BLUETOOTH_GUARD_POLL_MS = 3_000
    #: Pause before resuming media that the connection interrupted.
    RESUME_DELAY_MS = 2_000
    #: How long a fetched wallpaper is trusted before it is asked for again.
    WALLPAPER_RECHECK_SECONDS = 12 * 3600.0
    #: Older notifications are listed but not popped up.
    POPUP_MAX_AGE_SECONDS = 120.0

    def __init__(self, config: Config, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.config = config

        self.companion = CompanionClient(self._load_phone(), self)
        #: A second connection to the same phone that carries file
        #: transfers and nothing else.
        self.files_link = CompanionClient(self.companion.phone, self, role="files")
        self.kdeconnect = KdeConnect(self)
        self.dnd = DndSync(config.dnd, self)
        # Two ways to get video: the companion app encodes on the phone and
        # sends frames over the existing link, or scrcpy pulls them over adb.
        self.webcam = Webcam(config.webcam, self)
        if platform.IS_WINDOWS:
            from ..backends.webcam_win import WindowsCamera
            self.companion_camera = WindowsCamera(config.webcam, self)
        else:
            self.companion_camera = CompanionCamera(config.webcam, self)
        self.phone_audio = PhoneAudio(config.phone_audio, self)
        #: Files both ways.
        self.files = FileTransfers(self.files_link, config, self, fallback=self.companion)
        self.mirrors = mirror.MirrorManager(self)
        self.icons = IconStore(self.companion, self)
        #: The phone's clipboard over adb, for a phone that cannot share it
        #: itself (no Shizuku, no accessibility switch). Runs only then.
        self.clipboard_adb = AdbClipboard(self)
        self.clipboard = ClipboardSync(
            self.companion, config.clipboard, self, helper=self.clipboard_adb
        )

        self._device_caps: dict[str, Any] = {}
        self._call: dict[str, Any] = {"state": "idle"}
        self._bluetooth_name = ""
        #: Whether the phone was connected at the previous check.
        self._bluetooth_settled: bool | None = None
        #: Set once the phone has told us which codecs it can send.
        self._codecs_learned = False
        self._bluetooth_streaming = False
        #: Whether the phone should be connected over Bluetooth.
        self._bluetooth_wanted = bool(config.bluetooth.autoconnect)
        self._bluetooth_busy = False
        #: When the last automatic attempt was made, so a phone that is off or
        #: out of range is not retried every time the watch runs.
        self._bluetooth_tried = 0.0
        self._started_at = monotonic()
        #: Until when a media profile that appears is handed back to the phone.
        self._bluetooth_guard_until = 0.0
        self._bluetooth_guard_timer = QTimer(self)
        self._bluetooth_guard_timer.setSingleShot(True)
        self._bluetooth_guard_timer.timeout.connect(self._watch_bluetooth)
        self._autoconnect_deferred = False
        #: Media was playing when Bluetooth started connecting.
        self._resume_after_bluetooth = False
        self._media_known = False
        self._media: dict[str, Any] = {}
        self._phone_status: dict[str, Any] = {}
        self._hotspot_joined = False
        #: True between asking the phone for audio and it starting, so the
        #: interface can say "waiting for the phone" rather than nothing.
        self._phone_audio_pending = False
        #: Whether the phone is silent because we asked it to be, for the
        #: stream that is running now.
        self.phone_muted = False
        #: The dominant colour of the phone's wallpaper, where it gave one.
        self.wallpaper_colour = ""
        #: When the wallpaper was last asked for, so a reconnect does not ask
        #: again. None until the first time.
        self._wallpaper_asked: float | None = None
        #: The phone's storage, while it is mounted, and what to say about it.
        self.storage_mount: storage.Mount | None = None
        self.storage_state = "idle"          # idle | starting | mounted | error
        self.storage_message = ""
        self._notifications: dict[str, Notification] = {}
        self._otp_seen: set[str] = set()
        self._serial = ""
        #: Not before this, so a phone that is simply off does not cost a
        #: round of connection attempts every fifteen seconds.
        self._adb_next_try = 0.0
        self._phone_dnd = "off"
        #: The phone published to this desktop as an MPRIS player, where the
        #: desktop has a session bus to publish it on.
        self.media_player: mpris_server.MprisServer | None = None

        self._wire_companion()
        self._wire_kdeconnect()
        self._wire_dnd()
        self._wire_camera()
        self._wire_phone_audio()
        self._wire_media_player()
        self._wire_files()
        self._wire_wallpaper()
        self._wire_storage()
        self._apply_codec_preference()

        # adb is only needed for scrcpy now, so resolve it lazily and quietly.
        self._serial_timer = QTimer(self)
        self._serial_timer.timeout.connect(self.refresh_adb)
        if platform.supported("bluetooth_audio"):
            self._serial_timer.timeout.connect(self._watch_bluetooth)
            # Once shortly after starting, rather than waiting a quarter of a
            # minute for the first tick: connecting on startup is the point of
            # the setting, and fifteen seconds of "Not connected" reads as it
            # not working. Not immediately, because the window is still being
            # built and bluetoothctl is a subprocess.
            QTimer.singleShot(1_500, self._watch_bluetooth)
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
            model=saved.model,
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
        saved.model = phone.model
        saved.device_id = phone.device_id
        self.config.save()

    # -- wiring --------------------------------------------------------------

    def _wire_companion(self) -> None:
        self.companion.notificationPosted.connect(self._on_companion_notification)
        self.companion.notificationRemoved.connect(self.remove_notification)
        self.companion.dndChanged.connect(self._on_phone_dnd)
        self.companion.clipboardChanged.connect(self.clipboard.apply_remote)
        self.companion.clipboardQueried.connect(self._answer_clipboard_query)
        # Whether the adb route is needed changes with what the phone offers.
        self.companion.connectedChanged.connect(lambda _c: self.update_clipboard_route())
        self.companion.capabilitiesChanged.connect(lambda _c: self.update_clipboard_route())
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
        self.update_clipboard_route()
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
        """Force a fresh connection attempt."""
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
        self.clipboard_adb.stop()
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

    @property
    def serial_is_usb(self) -> bool:
        """Whether the phone is on a cable rather than the network."""
        return bool(self._serial) and ":" not in self._serial

    def refresh_adb(self) -> None:
        def resolve() -> tuple[str, str]:
            try:
                return adb.resolve_serial(self.config.adb_serial), ""
            except adb.AdbError:
                pass
            return self._reconnect_adb()

        submit(
            resolve,
            on_done=self._set_serial,
            on_error=lambda _m: self._set_serial(("", "")),
        )

    def _reconnect_adb(self) -> tuple[str, str]:
        """Bring the wireless link back after it has dropped."""
        now = monotonic()
        if now < self._adb_next_try:
            return "", ""
        self._adb_next_try = now + self.ADB_RETRY_SECONDS

        for target in self._adb_targets():
            try:
                adb.connect(target)
            except adb.AdbError as exc:
                log.debug("adb connect %s: %s", target, exc)
                continue
            try:
                serial = adb.resolve_serial(self.config.adb_serial)
            except adb.AdbError:
                continue
            log.info("adb reconnected over Wi-Fi at %s", target)
            return serial, target
        return "", ""

    def _adb_targets(self) -> list[str]:
        """Where the phone might be listening for adb, best guess first."""
        targets: list[str] = []
        remembered = self.config.adb_wireless_host.strip()
        if remembered:
            targets.append(remembered)
        # Wireless debugging picks a new port every time it is switched on, so
        # the advertised one beats anything remembered.
        for found in adb.mdns_targets():
            if found not in targets:
                targets.append(found)
        # Failing that, the address the companion app is connected on, with the
        # default port -- which is where `adb tcpip` puts it.
        host = self.companion.phone.host.strip()
        if host:
            guess = f"{host}:5555"
            if guess not in targets:
                targets.append(guess)
        return targets

    def enable_wireless_adb(self) -> str:
        """Arm adb over Wi-Fi while the cable is in, so it survives unplugging."""
        serial = self._serial or adb.resolve_serial(self.config.adb_serial)
        host = adb.wifi_ip(serial)
        if not host:
            raise adb.AdbError(
                "The phone is not on Wi-Fi, so there is no address to reach it on."
            )
        adb.enable_tcpip(serial)
        endpoint = f"{host}:5555"
        # adbd restarts, so it is not listening the instant tcpip returns.
        sleep(1.5)
        adb.connect(endpoint)
        return endpoint

    def remember_wireless_adb(self, endpoint: str) -> None:
        if endpoint and endpoint != self.config.adb_wireless_host:
            self.config.adb_wireless_host = endpoint
            self.config.save()

    def _set_serial(self, found: tuple[str, str]) -> None:
        serial, endpoint = found
        self.remember_wireless_adb(endpoint)
        if serial != self._serial:
            self._serial = serial
            self.dnd.set_serial(serial)
            self.adbChanged.emit(bool(serial))
            self.update_clipboard_route()

    def update_clipboard_route(self) -> None:
        """Run the adb clipboard helper exactly when it is the only way."""
        wanted = (
            self.config.features.clipboard
            and platform.supported("clipboard")
            and self.clipboard.wants_helper
            and bool(self._serial)
        )
        if wanted:
            self.clipboard_adb.start(self._serial)
        elif self.clipboard_adb.serial:
            self.clipboard_adb.stop()

    def _answer_clipboard_query(self, req: int) -> None:
        """The phone wants this computer's clipboard, to pick the newest one."""
        text, copied_at = ("", 0)
        if self.config.features.clipboard:
            text, copied_at = self.clipboard.state()
        self.companion.send(
            {"t": "clipboard_state", "rid": req, "text": text, "copiedAt": copied_at}
        )

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

        if platform.IS_WINDOWS:
            raise WebcamError("On Windows the webcam needs the companion app's camera.")
        # Screen mirroring, or no companion app: fall back to scrcpy over adb.
        self.webcam.start(self._serial)

    def stop_camera(self) -> None:
        if self.companion_camera.running:
            self.companion.send({"t": "camera_stop"})
            self.companion_camera.stop()
        if self.webcam.running:
            self.webcam.stop()

    def _wire_camera(self) -> None:
        # The phone reports SPS/PPS once, in camera_started,
        # separately from the frames.
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

    # -- finding the phone -----------------------------------------------------

    def ring_phone(self, on_done: Callable[[str], None] | None = None) -> None:
        """Make the phone ring, so it can be found."""
        def say(message: str) -> None:
            if on_done is not None:
                on_done(message)

        if self.connected and "ring" in self.companion.capabilities:
            def replied(message: dict[str, Any]) -> None:
                if message.get("t") == "error":
                    say(message.get("message", "The phone would not ring."))
                else:
                    say("Ringing your phone")
            self.companion.request({"t": "ring"}, replied)
            return

        try:
            self.kdeconnect.ring()
            say("Ringing your phone through KDE Connect")
        except Exception:
            say(
                "Ringing needs the companion app connected, or KDE Connect "
                "paired with this phone."
            )

    def stop_ringing(self) -> None:
        if self.connected and "ring" in self.companion.capabilities:
            self.companion.send({"t": "ring_stop"})

    # -- the phone's audio, over the link --------------------------------------

    @property
    def phone_audio_active(self) -> bool:
        return self.phone_audio.running

    @property
    def phone_audio_pending(self) -> bool:
        """Asked for, not playing yet: usually waiting for consent."""
        return self._phone_audio_pending

    def start_phone_audio(self) -> None:
        """Ask the phone to send what it is playing."""
        if not self.config.features.phone_audio:
            self.errorOccurred.emit(
                "Playing the phone's audio is switched off in Settings."
            )
            return
        if "phone_audio" not in self.companion.capabilities:
            self.errorOccurred.emit(
                "This phone cannot send its audio: the companion app needs "
                "Android 10 or later, and its own version may be older than "
                "this feature."
            )
            return
        if self.phone_audio.running:
            return
        self._phone_audio_pending = True
        self.phoneAudioChanged.emit(False)
        self.companion.send({
            "t": "audio_start",
            "mute": bool(self.config.phone_audio.mute_phone),
        })

    def stop_phone_audio(self) -> None:
        self._phone_audio_pending = False
        if self.companion.connected:
            self.companion.send({"t": "audio_stop"})
        self.phone_audio.close()

    def set_phone_muted(self, muted: bool) -> None:
        """Silence the phone while its audio plays here, or let it speak."""
        self.config.phone_audio.mute_phone = bool(muted)
        self.config.save()
        if self.phone_audio.running and self.companion.connected:
            self.companion.send({"t": "audio_mute", "on": bool(muted)})

    def toggle_phone_audio(self) -> None:
        if self.phone_audio.running or self._phone_audio_pending:
            self.stop_phone_audio()
        else:
            self.start_phone_audio()

    def _wire_phone_audio(self) -> None:
        self.companion.phoneAudioStarted.connect(self._on_phone_audio_started)
        self.companion.phoneAudioFrame.connect(self.phone_audio.feed)
        self.companion.phoneAudioStopped.connect(self._on_phone_audio_stopped)
        self.companion.phoneAudioConsent.connect(self._on_phone_audio_consent)
        self.phone_audio.levelChanged.connect(self.phoneAudioLevel)
        self.phone_audio.failed.connect(self._on_phone_audio_failed)
        self.phone_audio.started.connect(lambda: self.phoneAudioChanged.emit(True))
        self.phone_audio.stopped.connect(lambda: self.phoneAudioChanged.emit(False))
        # A link that drops takes the stream with it; the phone will not be
        # sending any more, and a half-open sink would sit there silent.
        self.companion.connectedChanged.connect(self._on_link_for_audio)
        # An error from the phone while we are waiting on consent is the end of
        # that attempt, whatever it was: stop claiming to be starting.
        self.companion.errorOccurred.connect(self._on_phone_error_for_audio)

    # -- the phone's own look --------------------------------------------------

    def _wire_wallpaper(self) -> None:
        self.companion.capabilitiesChanged.connect(self._maybe_fetch_wallpaper)

    @property
    def wallpaper_path(self) -> Path:
        return platform.state_dir() / "wallpaper.jpg"

    def _maybe_fetch_wallpaper(self, capabilities: list) -> None:
        """Ask for the wallpaper once per connection, and only if it can."""
        if "wallpaper" not in capabilities:
            return
        # Once per run, not once per connection.
        now = monotonic()
        if (self._wallpaper_asked is not None
                and now - self._wallpaper_asked < self.WALLPAPER_RECHECK_SECONDS):
            return
        self._wallpaper_asked = now
        self.companion.request({"t": "wallpaper_get"}, self._on_wallpaper)

    def _on_wallpaper(self, message: dict) -> None:
        colour = str(message.get("colour") or "")
        data = message.get("data")
        path = ""
        if isinstance(data, (bytes, bytearray)) and data:
            target = self.wallpaper_path
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                # Written whole, then swapped: a half-written image would be
                # drawn as a grey box until the next connection.
                temporary = target.with_suffix(".part")
                temporary.write_bytes(bytes(data))
                temporary.replace(target)
                path = str(target)
            except OSError as exc:
                log.debug("could not save the wallpaper: %s", exc)
        if path or colour:
            self.wallpaper_colour = colour
            self.wallpaperChanged.emit(path, colour)

    # -- files ---------------------------------------------------------------

    def _wire_files(self) -> None:
        self.companion.capabilitiesChanged.connect(self._open_files_link)
        self.companion.connectedChanged.connect(self._follow_main_link)
        self.files.changed.connect(self.transferChanged)
        self.files.received.connect(self.fileReceived)
        self.files.failed.connect(self.errorOccurred)

    def _open_files_link(self, capabilities: list) -> None:
        """Open the file connection beside the main link, where the phone can."""
        address = self.companion.address
        if "file_channel" not in capabilities or address is None:
            return
        if self.files_link.connected:
            return
        self.files_link.connect_to_phone(*address)

    def _follow_main_link(self, connected: bool) -> None:
        # The file connection lives and dies with the main link: on its own it
        # would retry against a phone the main link already knows is gone.
        if not connected:
            self.files_link.disconnect_from_phone()

    # -- the phone's storage ---------------------------------------------------

    def _wire_storage(self) -> None:
        self.companion.capabilitiesChanged.connect(self._maybe_mount_storage)
        self.companion.connectedChanged.connect(self._on_link_for_storage)
        app = QCoreApplication.instance()
        if app is not None:
            # A mount left behind when the app quits is a folder that hangs
            # whatever opens it until the kernel gives up on it.
            app.aboutToQuit.connect(self._unmount_now)

    def _maybe_mount_storage(self, capabilities: list) -> None:
        if (
            self.config.features.storage
            and self.config.storage.auto_mount
            and "storage_allowed" in capabilities
            and storage.backend()
            and self.storage_mount is None
            and self.storage_state != "starting"
        ):
            self.mount_storage()

    def mount_storage(self) -> None:
        """Mount the phone's storage as a folder. Only ever after a check or a click."""
        if not self.config.features.storage:
            self._storage("idle", "Phone storage is switched off in Settings.")
            return
        if self.storage_mount is not None or self.storage_state == "starting":
            return
        if not storage.backend():
            self._storage("error", storage.missing_advice())
            return
        address = self.companion.address
        if address is None:
            self._storage("error", "The companion app is not connected.")
            return
        if "storage" not in self.companion.capabilities:
            self._storage(
                "error",
                "This phone cannot share its storage: it needs Android 11 and a "
                "companion app new enough to offer it.",
            )
            return

        self._storage("starting", "Asking the phone to start its file server...")
        host = address[0]

        def started(reply: dict) -> None:
            if reply.get("t") == "error":
                self._storage("error", str(reply.get("message") or "The phone refused."))
                return
            info = storage.ServerInfo.from_reply(host, reply)
            if info is None:
                self._storage("error", "The phone's answer about its file server was incomplete.")
                return
            name, sidebar = self.phone_name, self.config.storage.sidebar
            submit(
                lambda: storage.mount(info, name, sidebar),
                on_done=self._on_storage_mounted,
                on_error=self._on_storage_failed,
            )

        self.companion.request({"t": "storage_start"}, started)

    def _on_storage_mounted(self, mounted: storage.Mount) -> None:
        if not self.companion.connected:
            # The phone went away while the mount was being made.
            submit(lambda: storage.unmount(mounted), on_error=lambda _m: None)
            self._storage("idle", "")
            return
        self.storage_mount = mounted
        self._storage("mounted", str(mounted.local_path or mounted.location))

    def _on_storage_failed(self, message: str) -> None:
        if self.companion.connected:
            self.companion.send({"t": "storage_stop"})
        self._storage("error", message)

    def unmount_storage(self, tell_phone: bool = True) -> None:
        mounted, self.storage_mount = self.storage_mount, None
        if tell_phone and self.companion.connected:
            self.companion.send({"t": "storage_stop"})
        if mounted is not None:
            submit(
                lambda: storage.unmount(mounted),
                on_error=lambda message: log.debug("unmount: %s", message),
            )
        self._storage("idle", "")

    def grant_storage(self) -> None:
        """Ask the phone to allow All files access through Shizuku, then mount."""
        def replied(message: dict) -> None:
            if message.get("t") == "error":
                self._storage("error", str(message.get("message") or "The phone refused."))
                return
            # Out of "starting" first: mount_storage takes that state to mean a
            # mount is already under way, and would do nothing.
            self.storage_state = "idle"
            self.mount_storage()

        self._storage("starting", "Asking the phone to allow access to its files...")
        self.companion.request({"t": "storage_grant"}, replied)

    def _on_link_for_storage(self, connected: bool) -> None:
        if not connected and (self.storage_mount is not None or self.storage_state == "starting"):
            self.unmount_storage(tell_phone=False)

    def _unmount_now(self) -> None:
        mounted, self.storage_mount = self.storage_mount, None
        if mounted is not None:
            storage.unmount(mounted)

    def _storage(self, state: str, message: str) -> None:
        self.storage_state, self.storage_message = state, message
        self.storageChanged.emit()

    def send_files(self, paths) -> None:
        """Send files to the phone. Called by the page and by a drop."""
        if not self.config.features.file_transfer:
            self.errorOccurred.emit("File transfer is switched off in Settings.")
            return
        if not self.connected:
            self.errorOccurred.emit("The companion app is not connected.")
            return
        if "file_transfer" not in self.companion.capabilities:
            self.errorOccurred.emit(
                "This phone cannot take files yet: its companion app is older "
                "than this feature."
            )
            return
        self.files.send(paths)

    def _on_phone_audio_started(self, header: dict) -> None:
        self._phone_audio_pending = False
        #: Whether the phone actually went quiet.
        self.phone_muted = bool(header.get("muted"))
        if header.get("muteAsked") and not self.phone_muted:
            self.errorOccurred.emit(
                "The phone would not go quiet -- Do Not Disturb stops an app "
                "changing the volume. Its own speaker is still playing."
            )
        self.phone_audio.open(header)

    def _on_phone_audio_stopped(self) -> None:
        self._phone_audio_pending = False
        self.phone_muted = False
        self.phone_audio.close()

    def _on_phone_audio_consent(self, message: str) -> None:
        self._phone_audio_pending = True
        self.phoneAudioWaiting.emit(
            message or "Allow it on the phone to start the audio."
        )

    def _on_phone_audio_failed(self, reason: str) -> None:
        self._phone_audio_pending = False
        self.phone_audio.close()
        self.errorOccurred.emit(reason)

    def _on_phone_error_for_audio(self, _reason: str) -> None:
        if self._phone_audio_pending:
            self._phone_audio_pending = False
            self.phoneAudioChanged.emit(False)

    def _on_link_for_audio(self, connected: bool) -> None:
        if not connected and (self.phone_audio.running or self._phone_audio_pending):
            self._phone_audio_pending = False
            self.phone_audio.close()

    # -- bluetooth audio -------------------------------------------------------

    @property
    def bluetooth_name(self) -> str:
        """The phone's Bluetooth name, used to find its media player."""
        return self._bluetooth_name

    def _apply_codec_preference(self) -> None:
        """Offer the phone the codecs the settings ask for."""
        # WirePlumber's configuration: Windows picks its codec itself.
        if not platform.supported("bluetooth_codecs"):
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
        """Remember what the phone can send, and re-offer accordingly."""
        if not phone_codecs:
            return
        self._codecs_learned = True
        if list(self.config.bluetooth.phone_codecs) == list(phone_codecs):
            return
        log.info("phone offers %s", ", ".join(phone_codecs))
        self.config.bluetooth.phone_codecs = list(phone_codecs)
        self.config.save()
        self._apply_codec_preference()

    @property
    def bluetooth_busy(self) -> bool:
        return self._bluetooth_busy

    def _set_bluetooth_busy(self, busy: bool) -> None:
        if busy == self._bluetooth_busy:
            return
        self._bluetooth_busy = busy
        self.bluetoothBusyChanged.emit(busy)

    def connect_bluetooth(self, on_done=None, on_error=None) -> None:
        """Connect the phone over Bluetooth without moving any audio."""
        if not platform.supported("bluetooth_audio"):
            self.errorOccurred.emit(
                "This computer cannot do Bluetooth audio."
            )
            return
        if not self.config.features.bluetooth_audio:
            self.errorOccurred.emit(
                "Bluetooth audio is switched off in Settings."
            )
            return
        if self._bluetooth_busy:
            return

        self._bluetooth_wanted = True
        self._bluetooth_tried = monotonic()
        self._set_bluetooth_busy(True)
        # Connecting can pause the phone's media; remember whether to resume.
        self._resume_after_bluetooth = (
            self.companion.connected and bool(self._media.get("playing"))
        )
        quiet = not self.config.bluetooth.auto_stream
        settle = self.BLUETOOTH_SETTLE_SECONDS

        def work() -> str:
            device = bluetooth.find_phone(
                preferred_address=self.config.bluetooth.address,
                name_hint=self.phone_name,
            )
            if device is None:
                raise RuntimeError(
                    "No paired phone found. Pair it with this computer in the "
                    "Bluetooth settings first."
                )
            if self.config.bluetooth.address != device.address:
                self.config.bluetooth.address = device.address
                self.config.save()
            if device.connected:
                return device.label

            if not quiet:
                bluetooth.connect(device.address)
            else:
                bluetooth.connect_quietly(device.address)
                # The phone may add the media profile a few seconds later.
                bluetooth.keep_audio_on_phone(device.address, settle)
                bt_audio.ready_to_receive(device.address)
            return device.label

        def done(label: object) -> None:
            self._set_bluetooth_busy(False)
            if quiet:
                self._bluetooth_guard_until = monotonic() + self.BLUETOOTH_GUARD_SECONDS
            self._watch_bluetooth()
            self._schedule_resume()
            if on_done is not None:
                on_done(str(label))

        def failed(message: str) -> None:
            self._set_bluetooth_busy(False)
            self._schedule_resume()
            # A caller that shows the failure itself -- the Audio page, which
            # can offer to pair again -- says so by passing a handler; without
            # one the message goes to the window's own error route.
            if on_error is not None:
                on_error(message)
            else:
                self.errorOccurred.emit(message)

        submit(work, on_done=done, on_error=failed)

    def disconnect_bluetooth(self, on_done=None, on_error=None) -> None:
        """Drop the Bluetooth link, and stop trying to bring it back."""
        address = self.config.bluetooth.address
        if not address or self._bluetooth_busy:
            return
        self._bluetooth_wanted = False
        self._set_bluetooth_busy(True)

        def done(_result: object) -> None:
            self._set_bluetooth_busy(False)
            self._note_bluetooth({"connected": False})
            if on_done is not None:
                on_done("")

        def failed(message: str) -> None:
            self._set_bluetooth_busy(False)
            if on_error is not None:
                on_error(message)
            else:
                self.errorOccurred.emit(message)

        submit(bluetooth.disconnect, address, on_done=done, on_error=failed)

    def _autoconnect_bluetooth(self) -> None:
        """Bring the link up by itself, if that is what the settings say."""
        if not self._bluetooth_wanted or self._bluetooth_busy:
            return
        if not self.config.bluetooth.autoconnect:
            return
        if not self.config.features.bluetooth_audio:
            return
        if not platform.supported("bluetooth_audio"):
            return
        if self._bluetooth_settled:
            return
        now = monotonic()
        if now - self._bluetooth_tried < self.BLUETOOTH_RETRY_SECONDS:
            return
        if self._waiting_for_media(now):
            # Wait for the phone to say what is playing, so it can be resumed.
            if not self._autoconnect_deferred:
                self._autoconnect_deferred = True
                QTimer.singleShot(1_000, self._retry_autoconnect)
            return
        self._bluetooth_tried = now
        log.info("connecting the phone over Bluetooth")
        # Failures are logged, not shown: nobody pressed anything.
        self.connect_bluetooth(
            on_error=lambda message: log.info("automatic connect: %s", message)
        )

    def _waiting_for_media(self, now: float) -> bool:
        """Whether autoconnect should hold off until the media state arrives."""
        if self._media_known or not self.companion.phone.token:
            return False
        return now - self._started_at < self.BLUETOOTH_MEDIA_WAIT_SECONDS

    def _retry_autoconnect(self) -> None:
        self._autoconnect_deferred = False
        self._autoconnect_bluetooth()

    def end_bluetooth_guard(self) -> None:
        """Stop handing the media profile back; the user asked for a stream."""
        self._bluetooth_guard_until = 0.0
        self._resume_after_bluetooth = False

    def _schedule_resume(self) -> None:
        if self._resume_after_bluetooth:
            QTimer.singleShot(self.RESUME_DELAY_MS, self._resume_media)

    def _resume_media(self) -> None:
        """Resume media that was playing before Bluetooth interrupted it."""
        if not self._resume_after_bluetooth:
            return
        self._resume_after_bluetooth = False
        if not self.companion.connected or self._media.get("playing"):
            return
        log.info("resuming media paused by the Bluetooth connection")
        self.companion.send({"t": "media_command", "action": "play"})

    def _watch_bluetooth(self) -> None:
        """Park a Bluetooth link that takes the audio path without being asked."""
        if not self.config.features.bluetooth_audio:
            return

        # Parking is the whole point of this check, and turning auto_stream on
        # asks for the opposite.
        park = not self.config.bluetooth.auto_stream

        # A link seen coming up, or one we connected recently, is handed back.
        guarding = park and monotonic() < self._bluetooth_guard_until
        appeared = park and (self._bluetooth_settled is False or guarding)
        was_playing = self.companion.connected and bool(self._media.get("playing"))
        if guarding:
            self._bluetooth_guard_timer.start(self.BLUETOOTH_GUARD_POLL_MS)

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
            transport = ""
            if park or not self._codecs_learned:
                transport, codecs = bluetooth.media_state(device.address)
                if not self._codecs_learned:
                    state["phone_codecs"] = codecs

            if not park:
                return state

            # Keep the card able to accept a stream.
            bt_audio.ready_to_receive(device.address)

            if appeared and transport:
                bluetooth.release_audio(device.address)
                log.info("released the media profile nobody asked for")
                state["released"] = True
                state["was_playing"] = was_playing
                return state

            # Streaming here was chosen on the phone.
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
        was = self._bluetooth_settled
        self._bluetooth_settled = bool(state.get("connected"))
        if bool(was) != self._bluetooth_settled or was is None:
            self.bluetoothConnectedChanged.emit(self._bluetooth_settled)
        self._learn_codecs(state.get("phone_codecs") or [])
        name = state.get("name") or ""
        if name:
            self._bluetooth_name = name
        streaming = bool(state.get("streaming"))
        if streaming != self._bluetooth_streaming:
            self._bluetooth_streaming = streaming
            self.bluetoothStreamingChanged.emit(streaming)
        if state.get("released") and state.get("was_playing"):
            self._resume_after_bluetooth = True
            self._schedule_resume()

        # Last, so it acts on what was just read rather than on the previous
        # round: a phone that is not there is a phone to connect to.
        self._autoconnect_bluetooth()

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
        """Battery detail, Wi-Fi and cellular signal, ringer mode."""
        return dict(self._phone_status)

    def set_ringer(self, mode: str) -> None:
        """Put the phone on normal, vibrate or silent."""
        if not self.companion.connected or not self.companion.supports("ringer"):
            self.errorOccurred.emit(
                "Changing the ringer needs the companion app on the phone."
            )
            return
        self.companion.send({"t": "ringer_set", "mode": mode})

    def set_phone_volume(self, percent: int) -> None:
        """The phone's media volume, which its Bluetooth audio here follows."""
        if self.companion.connected and self.companion.supports("media_volume"):
            self.companion.send({"t": "volume_set", "percent": max(0, min(100, int(percent)))})

    @property
    def ringer(self) -> str:
        value = self._phone_status.get("ringer")
        return value if isinstance(value, str) else ""

    # -- hotspot ---------------------------------------------------------------

    @property
    def hotspot_joined(self) -> bool:
        """Whether this computer is on the phone's hotspot."""
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
        """What the phone is playing, as reported by the companion app."""
        return dict(self._media)

    def _on_media(self, message: dict) -> None:
        self._media = message
        self._media_known = True
        self.mediaChanged.emit(message)

    def media_command(self, action: str) -> None:
        if not self.companion.connected:
            self.errorOccurred.emit("No phone connected.")
            return
        self.companion.send({"t": "media_command", "action": action})

    def _wire_media_player(self) -> None:
        """Offer the phone to this desktop as a player of its own."""
        if not mpris_server.available():
            return
        self.media_player = mpris_server.MprisServer(parent=self)
        self.media_player.commanded.connect(self.media_command)
        self.media_player.raiseRequested.connect(self.raiseRequested)
        if not self.media_player.publish():
            self.media_player = None
            return
        self.mediaChanged.connect(self.media_player.update)
        # Whatever the phone last said, so the applet is right immediately
        # rather than after the next track change.
        if self._media:
            self.media_player.update(self._media)

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
        if not platform.supported("bluetooth_calls"):
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
        """Cameras and hotspot bands as reported by the phone."""
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
        from time import time as now

        previous = self._notifications.get(note.id)
        fresh = previous is None or note.when > previous.when
        self._notifications[note.id] = note
        self._check_otp(note)
        self.notificationsChanged.emit()
        # Only a new one, or one re-posted with a later time, is worth a popup.
        # The phone sends everything it holds on connect; old ones are not news.
        if fresh and not note.ongoing and now() - note.when < self.POPUP_MAX_AGE_SECONDS:
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
        """Surface a passcode as soon as its notification arrives."""
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
            previous = self._phone_dnd

            def replied(message: dict[str, Any]) -> None:
                # Refused, e.g. a mode set by something other than Tessera: undo the guess.
                if message.get("t") == "error":
                    self._on_phone_dnd(previous)
                    self.errorOccurred.emit(
                        message.get("message") or "The phone kept its Do Not Disturb."
                    )

            # Shown first: a refusal can come back before request() returns.
            self._on_phone_dnd(mode)
            self.companion.request({"t": "dnd_set", "mode": mode}, replied)
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
