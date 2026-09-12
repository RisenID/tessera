"""Publishing the phone's music to this desktop, as an MPRIS player.

The other MPRIS module *reads* a player: BlueZ republishes the phone's AVRCP
data on the session bus, but only while the phone is connected over Bluetooth,
which means making this computer the phone's audio output. This one goes the
other way. What the companion app already reports -- title, artist, album, and
whether it is playing -- is offered to the desktop as a player of our own, so:

* Plasma's media applet, the lock screen and the task manager show the phone's
  track, with buttons that work;
* the keyboard's play, next and previous keys control the phone, because the
  desktop routes them to whichever MPRIS player is current;
* none of it needs Bluetooth, so the phone's own headphones are untouched.

It pairs with playing the phone's audio here: once the sound is coming out of
this computer, the controls for it should be where every other player's are.

The interface is the MPRIS v2 specification, of which this implements the part
a remote player can honestly answer -- no seeking, no track list, no volume:
those belong to the phone, and a control that lies is worse than one that is
absent.
"""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import ClassInfo, Property, QObject, Signal, Slot
from PySide6.QtDBus import QDBusAbstractAdaptor, QDBusObjectPath

from ..core import platform
from .dbus import HAVE_QTDBUS, QDBusConnection, QDBusMessage, QDBusVariant, session

log = logging.getLogger(__name__)

#: Every MPRIS player owns a bus name under this prefix and this one path.
SERVICE = "org.mpris.MediaPlayer2.tessera"
PATH = "/org/mpris/MediaPlayer2"

ROOT_IFACE = "org.mpris.MediaPlayer2"
PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"
PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"

#: A track needs an object path as its id. It never has to resolve to anything.
TRACK_PATH = "/dev/tessera/track"


@ClassInfo({"D-Bus Interface": ROOT_IFACE})
class RootAdaptor(QDBusAbstractAdaptor):
    """The player's identity, and the window it belongs to."""

    #: Asked to show itself: the desktop's "open this player" button.
    raiseRequested = Signal()

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)

    @Slot()
    def Raise(self) -> None:  # noqa: N802 - the specification's name
        self.raiseRequested.emit()

    @Slot()
    def Quit(self) -> None:  # noqa: N802
        # Deliberately nothing: quitting Tessera because a media applet asked
        # would be a surprise. CanQuit says so.
        log.debug("a desktop asked the player to quit; ignoring")

    @Property(bool)
    def CanQuit(self) -> bool:  # noqa: N802
        return False

    @Property(bool)
    def CanRaise(self) -> bool:  # noqa: N802
        return True

    @Property(bool)
    def HasTrackList(self) -> bool:  # noqa: N802
        return False

    @Property(str)
    def Identity(self) -> str:  # noqa: N802
        return self.parent().identity

    @Property(str)
    def DesktopEntry(self) -> str:  # noqa: N802
        return platform.APP_ID

    @Property("QStringList")
    def SupportedUriSchemes(self) -> list[str]:  # noqa: N802
        return []

    @Property("QStringList")
    def SupportedMimeTypes(self) -> list[str]:  # noqa: N802
        return []


@ClassInfo({"D-Bus Interface": PLAYER_IFACE})
class PlayerAdaptor(QDBusAbstractAdaptor):
    """The transport: what is playing, and the buttons that change it."""

    #: One of the phone's own actions: play, pause, playpause, next, previous.
    commanded = Signal(str)

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)

    # -- the buttons ---------------------------------------------------------

    @Slot()
    def Play(self) -> None:  # noqa: N802
        self.commanded.emit("play")

    @Slot()
    def Pause(self) -> None:  # noqa: N802
        self.commanded.emit("pause")

    @Slot()
    def PlayPause(self) -> None:  # noqa: N802
        self.commanded.emit("playpause")

    @Slot()
    def Stop(self) -> None:  # noqa: N802
        self.commanded.emit("stop")

    @Slot()
    def Next(self) -> None:  # noqa: N802
        self.commanded.emit("next")

    @Slot()
    def Previous(self) -> None:  # noqa: N802
        self.commanded.emit("previous")

    @Slot("qlonglong")
    def Seek(self, _offset: int) -> None:  # noqa: N802
        log.debug("seek is not offered")

    @Slot(str, "qlonglong")
    def SetPosition(self, _track: str, _position: int) -> None:  # noqa: N802
        log.debug("setting position is not offered")

    @Slot(str)
    def OpenUri(self, _uri: str) -> None:  # noqa: N802
        log.debug("opening a URI is not offered")

    # -- what it is playing --------------------------------------------------

    @Property(str)
    def PlaybackStatus(self) -> str:  # noqa: N802
        return self.parent().status

    @Property("QVariantMap")
    def Metadata(self) -> dict[str, Any]:  # noqa: N802
        return self.parent().metadata

    @Property(float)
    def Volume(self) -> float:  # noqa: N802
        # The phone's volume is the phone's business, and pretending otherwise
        # would give the desktop a slider that does nothing.
        return 1.0

    @Property(float)
    def Rate(self) -> float:  # noqa: N802
        return 1.0

    @Property(float)
    def MinimumRate(self) -> float:  # noqa: N802
        return 1.0

    @Property(float)
    def MaximumRate(self) -> float:  # noqa: N802
        return 1.0

    @Property(bool)
    def CanControl(self) -> bool:  # noqa: N802
        return self.parent().can_control

    @Property(bool)
    def CanPlay(self) -> bool:  # noqa: N802
        return self.parent().can_control

    @Property(bool)
    def CanPause(self) -> bool:  # noqa: N802
        return self.parent().can_control

    @Property(bool)
    def CanGoNext(self) -> bool:  # noqa: N802
        return self.parent().can_control

    @Property(bool)
    def CanGoPrevious(self) -> bool:  # noqa: N802
        return self.parent().can_control

    @Property(bool)
    def CanSeek(self) -> bool:  # noqa: N802
        return False

    @Property(bool)
    def Shuffle(self) -> bool:  # noqa: N802
        return False

    @Property(str)
    def LoopStatus(self) -> str:  # noqa: N802
        return "None"


class MprisServer(QObject):
    """The phone, as a player this desktop can see and control."""

    #: The desktop asked for the window; the main window connects to this.
    raiseRequested = Signal()
    #: A button was pressed: the phone's own action name.
    commanded = Signal(str)

    def __init__(self, identity: str = "Phone", parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.identity = identity
        self.status = "Stopped"
        self.metadata: dict[str, Any] = {}
        self.can_control = False

        self._registered = False
        self._bus = session()
        self._root = RootAdaptor(self)
        self._player = PlayerAdaptor(self)
        self._root.raiseRequested.connect(self.raiseRequested)
        self._player.commanded.connect(self.commanded)

    # -- the bus -------------------------------------------------------------

    @property
    def published(self) -> bool:
        return self._registered

    def publish(self) -> bool:
        """Take the bus name and export the object. False if it could not."""
        if self._registered:
            return True
        if not HAVE_QTDBUS or not self._bus.isConnected():
            log.info("no session bus, so the phone cannot be published as a player")
            return False

        options = (
            QDBusConnection.RegisterOption.ExportAllSlots
            | QDBusConnection.RegisterOption.ExportAllProperties
            | QDBusConnection.RegisterOption.ExportAdaptors
        )
        if not self._bus.registerObject(PATH, self, options):
            log.warning("could not export %s", PATH)
            return False
        if not self._bus.registerService(SERVICE):
            log.warning("could not take the bus name %s", SERVICE)
            self._bus.unregisterObject(PATH)
            return False

        self._registered = True
        log.info("published the phone as an MPRIS player")
        return True

    def withdraw(self) -> None:
        if not self._registered:
            return
        self._bus.unregisterService(SERVICE)
        self._bus.unregisterObject(PATH)
        self._registered = False

    # -- what it says --------------------------------------------------------

    def update(self, media: dict[str, Any]) -> None:
        """Take the companion app's now-playing and republish it."""
        title = str(media.get("title", "") or "")
        playing = bool(media.get("playing"))
        app = str(media.get("app", "") or "")

        self.can_control = bool(media.get("canControl", True)) and bool(title)
        self.status = "Playing" if playing else ("Paused" if title else "Stopped")
        self.metadata = self._metadata(media)
        self.identity = f"{app} on the phone" if app else "Phone"

        self._announce()

    def _metadata(self, media: dict[str, Any]) -> dict[str, Any]:
        """The MPRIS shape of a track.

        Deliberately without mpris:length: it is a 64-bit integer, PySide6
        marshals every Python int as a 32-bit one, and a wrong type is worse
        than a missing optional field -- the applet simply shows no progress
        bar, which is honest, because there is no seeking here either.
        """
        title = str(media.get("title", "") or "")
        if not title:
            return {}
        artist = str(media.get("artist", "") or "")
        album = str(media.get("album", "") or "")

        # Plain values, not QDBusVariant: the map is already a{sv}, so QtDBus
        # boxes each value itself. Wrapping them here produced a variant inside
        # a variant, which applets read as an empty field.
        data: dict[str, Any] = {
            # The specification wants an object path here, and a plain string
            # would be sent as one more string.
            "mpris:trackid": QDBusObjectPath(TRACK_PATH),
            "xesam:title": title,
        }
        if artist:
            # An array of strings, which is how every other player sends it.
            data["xesam:artist"] = [artist]
        if album:
            data["xesam:album"] = album
        return data

    def _announce(self) -> None:
        """Tell the desktop what changed, which is how applets stay current."""
        if not self._registered:
            return
        message = QDBusMessage.createSignal(PATH, PROPERTIES_IFACE, "PropertiesChanged")
        # Plain values again: the dictionary is a{sv} and QtDBus boxes them.
        message.setArguments([
            PLAYER_IFACE,
            {
                "PlaybackStatus": self.status,
                "Metadata": self.metadata,
                "CanPlay": self.can_control,
                "CanPause": self.can_control,
                "CanGoNext": self.can_control,
                "CanGoPrevious": self.can_control,
                "CanControl": self.can_control,
            },
            [],
        ])
        self._bus.send(message)


def available() -> bool:
    """Whether a player can be published on this desktop at all."""
    if not HAVE_QTDBUS or not platform.supported("mpris"):
        return False
    return bool(session().isConnected())
