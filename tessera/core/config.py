"""Persistent settings, stored as JSON in the platform's config directory."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, get_args, get_origin, get_type_hints

from . import platform

log = logging.getLogger(__name__)

APP_ID = "tessera"


#: Re-exported so nothing outside core has to know which platform it is on.
config_dir = platform.config_dir
state_dir = platform.state_dir
cache_dir = platform.cache_dir


@dataclass
class DndConfig:
    #: off | phone_to_desktop | desktop_to_phone | two_way
    mode: str = "phone_to_desktop"
    #: seconds between polls of the phone's zen_mode (adb has no push channel)
    poll_seconds: int = 5
    #: mirror "priority only" and "alarms only" as desktop DND, not just silence
    treat_partial_as_dnd: bool = True


@dataclass
class WebcamConfig:
    #: camera | screen -- a phone lens, or a mirror of the phone display
    source: str = "camera"
    facing: str = "back"           # back | front | external
    #: Exact camera id reported by the phone. A phone has several lenses per
    #: facing, so the id is what actually picks the intended one.
    camera_id: str = ""
    size: str = "1920x1080"
    fps: int = 30
    device: str = ""               # empty -> pick the first free loopback node
    audio: bool = False


@dataclass
class HotspotConfig:
    ssid: str = ""
    passphrase: str = ""
    band: str = "2.4"              # 2.4 | 5
    #: wifi | usb -- USB tethering is the reliable fallback on locked-down ROMs
    method: str = "wifi"
    autoconnect: bool = True
    #: seconds to wait for the phone's AP to show up in a scan
    scan_timeout: int = 30


@dataclass
class CompanionConfig:
    """Everything needed to reconnect to the phone without pairing again."""

    host: str = ""
    port: int = 8765
    token: str = ""
    fingerprint: str = ""     # SHA-256 of the phone's TLS certificate
    name: str = ""
    model: str = ""           # the part number, shown under the name
    device_id: str = ""


@dataclass
class FeatureConfig:
    """Which optional features are active."""

    notifications: bool = True
    otp: bool = True             # surface one-time passcodes from notifications
    notification_popups: bool = True
    messages: bool = True
    photos: bool = True
    clipboard: bool = True
    dnd_sync: bool = True
    webcam: bool = True
    screen: bool = True          # mirroring
    apps: bool = True            # the launcher, opening one app per window
    hotspot: bool = True
    bluetooth_audio: bool = True
    #: Playing what the phone is playing, over the companion link
    #: rather than Bluetooth.
    phone_audio: bool = field(
        default_factory=lambda: not platform.supported("bluetooth_audio")
    )
    calls: bool = True
    #: Files in both directions, and the phone's share sheet.
    file_transfer: bool = True
    #: The phone's storage as a folder in the file manager, over SFTP.
    storage: bool = True


@dataclass
class PhoneAudioConfig:
    """Playing the phone's audio here, over the companion link."""

    #: Which way to play the phone's audio when the switch in the sidebar is
    #: pressed. Both routes stay available on the Audio page whatever this
    #: says; this only decides what one click does.
    #:
    #: * "auto" -- over the link when the phone offers it, else Bluetooth
    #: * "link" -- always over the companion link
    #: * "bluetooth" -- always the A2DP profile switch
    #:
    #: Bluetooth by default: it moves the audio rather than copying it, so the
    #: phone does not need silencing, and it is the same radio the call audio
    #: has to use anyway.
    route: str = "bluetooth"

    #: Whether the phone goes quiet while its audio is playing here.
    mute_phone: bool = True

    volume: int = 100              # percent
    buffer_ms: int = 120
    #: Where to play it. Empty means the system's default output, which is
    #: what most people want and what follows their headphones around.
    device: str = ""


@dataclass
class BluetoothConfig:
    #: Address of the phone's Bluetooth radio, learned on first use.
    address: str = ""
    #: Connect to the phone over Bluetooth when this app starts, and again if
    #: the link drops. On by default, because being connected is what makes
    #: track details, call control and the audio button work at all.
    #:
    #: Connecting is not playing. The connection made here leaves the media
    #: profile alone entirely, so nothing moves off the phone's own headphones
    #: -- see `bluetooth.connect_quietly`. Audio moves only from the button.
    autoconnect: bool = True
    #: Start streaming as soon as the phone connects.
    auto_stream: bool = False
    #: Move call audio to this computer when a call starts. Also off by
    #: default, for the same reason -- it silently reroutes a headset.
    route_calls: bool = False
    #: Which codecs to offer the phone for music. See backends.btcodecs.
    codec: str = "auto"
    #: What the phone said it can send, read from its Bluetooth endpoints the
    #: first time it connected.
    phone_codecs: list[str] = field(default_factory=list)


@dataclass
class FilesConfig:
    """Where files from the phone land, and what happens when they do."""

    #: Empty means the desktop's own download folder, asked for by name so it
    #: is the one the file manager already shows.
    save_to: str = ""
    #: Raise a desktop notification when a file finishes arriving. On, because
    #: a file that appears silently in a folder is a file nobody finds.
    notify: bool = True
    #: Open the Share page when a transfer starts, so progress is visible
    #: without going looking for it.
    show_progress: bool = True


@dataclass
class StorageConfig:
    """The phone's storage, mounted as a folder on this computer."""

    #: Mount it whenever the phone connects.
    auto_mount: bool = True
    #: Put it in the file manager's sidebar while it is mounted.
    sidebar: bool = True


@dataclass
class ClipboardConfig:
    #: off | phone_to_desktop | desktop_to_phone | two_way
    mode: str = "two_way"


@dataclass
class MirrorConfig:
    max_size: int = 1600
    fps: int = 60
    bitrate: str = "8M"
    audio: bool = True
    stay_awake: bool = True
    turn_screen_off: bool = False
    app_window_size: str = "1280x800"
    show_system_apps: bool = False


#: The switches offered in the device panel, and the six shown by default.
#: Keys are defined in ui/panel.py, which knows how to draw and drive them.
PANEL_TILES = (
    "dnd", "ringer", "clipboard", "ring", "hotspot", "camera", "mirror", "audio",
)
DEFAULT_TILES = ("dnd", "ringer", "clipboard", "ring", "hotspot", "camera")


@dataclass
class PanelConfig:
    """The device panel: how wide, and which switches it carries."""

    width: int = 320
    width_fullscreen: int = 400
    tiles: list[str] = field(default_factory=lambda: list(DEFAULT_TILES))


@dataclass
class Config:
    device_id: str = ""            # KDE Connect device id
    adb_serial: str = ""           # empty -> use the only attached device
    adb_wireless_host: str = ""    # host:port remembered from a previous pairing
    start_minimised: bool = False
    notification_popups: bool = True
    companion: CompanionConfig = field(default_factory=CompanionConfig)
    dnd: DndConfig = field(default_factory=DndConfig)
    mirror: MirrorConfig = field(default_factory=MirrorConfig)
    clipboard: ClipboardConfig = field(default_factory=ClipboardConfig)
    features: FeatureConfig = field(default_factory=FeatureConfig)
    bluetooth: BluetoothConfig = field(default_factory=BluetoothConfig)
    webcam: WebcamConfig = field(default_factory=WebcamConfig)
    phone_audio: PhoneAudioConfig = field(default_factory=PhoneAudioConfig)
    files: FilesConfig = field(default_factory=FilesConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    hotspot: HotspotConfig = field(default_factory=HotspotConfig)
    panel: PanelConfig = field(default_factory=PanelConfig)

    # -- persistence ---------------------------------------------------------

    @classmethod
    def path(cls) -> Path:
        return config_dir() / "config.json"

    @classmethod
    def load(cls) -> "Config":
        path = cls.path()
        if not path.exists():
            return cls()
        try:
            raw = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("ignoring unreadable config %s: %s", path, exc)
            return cls()
        if not isinstance(raw, dict):
            log.warning("ignoring malformed config %s", path)
            return cls()
        return _from_dict(cls, raw)

    def save(self) -> None:
        """Write atomically so a crash mid-save cannot truncate the config."""
        path = self.path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(asdict(self), indent=2, sort_keys=True) + "\n"
        tmp = None
        try:
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=path.parent, prefix=".config-", delete=False
            ) as handle:
                tmp = Path(handle.name)
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            tmp.replace(path)
        except OSError as exc:
            log.error("could not save config: %s", exc)
            if tmp is not None and tmp.exists():
                tmp.unlink(missing_ok=True)


def _from_dict(cls: type, raw: dict[str, Any]) -> Any:
    """Rebuild a dataclass from JSON, dropping unknown or mistyped keys."""
    # `from __future__ import annotations` makes Field.type a string, so the
    # annotations have to be resolved before they can be compared against.
    hints = get_type_hints(cls)
    kwargs: dict[str, Any] = {}
    for f in fields(cls):
        if f.name not in raw:
            continue
        declared = hints.get(f.name, Any)
        value = raw[f.name]
        if is_dataclass(declared) and isinstance(value, dict):
            kwargs[f.name] = _from_dict(declared, value)
        elif get_origin(declared) is list and isinstance(value, list):
            # list[str] is not a type, so the isinstance check below silently
            # rejects it and the field falls back to its default.
            item = (get_args(declared) or (Any,))[0]
            kwargs[f.name] = [
                entry for entry in value
                if item is Any or (isinstance(entry, item) and not isinstance(entry, bool))
            ]
        elif declared is float and isinstance(value, (int, float)) and not isinstance(value, bool):
            kwargs[f.name] = float(value)
        elif isinstance(declared, type) and isinstance(value, declared):
            # bool is a subclass of int; do not let `true` satisfy an int field.
            if declared is not bool and isinstance(value, bool):
                log.debug("dropping config key %s: expected %s", f.name, declared.__name__)
                continue
            kwargs[f.name] = value
        else:
            log.debug("dropping config key %s with unexpected value %r", f.name, value)
    try:
        return cls(**kwargs)
    except TypeError as exc:  # pragma: no cover - defensive
        log.warning("could not apply saved config: %s", exc)
        return cls()
