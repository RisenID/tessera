"""Silencing desktop notifications, on whichever desktop this is."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from ..core.proc import have, run

from ..core import platform

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Silencer:
    """One desktop's way of turning notifications off."""

    #: What to call it when explaining what Tessera is driving.
    desktop: str
    read: tuple[str, ...]
    on: tuple[str, ...]
    off: tuple[str, ...]
    #: True when the setting means "show notifications" rather than "silence".
    inverted: bool = False
    #: Desktops this setting is honoured by, matched against
    #: XDG_CURRENT_DESKTOP.
    desktops: tuple[str, ...] = ()
    #: Set for a backend that is not a command at all; see OWN_POPUPS.
    key: str = ""

    @property
    def program(self) -> str:
        return self.read[0] if self.read else ""

    def running_desktop(self) -> bool:
        """Whether this is the desktop in front of the user."""
        if not self.desktops:
            return True
        current = ":".join(
            os.environ.get(name, "")
            for name in ("XDG_CURRENT_DESKTOP", "XDG_SESSION_DESKTOP", "DESKTOP_SESSION")
        ).lower()
        return any(token in current.split(":") for token in self.desktops)

    def available(self) -> bool:
        """Whether this desktop's switch is here, readable, and listened to."""
        if self.key == "tessera":
            return True
        return (
            self.running_desktop()
            and have(self.program)
            and run(list(self.read), timeout=6.0).ok
        )

    def silenced(self) -> bool:
        if self.key == "tessera":
            return own_popups_silenced()
        result = run(list(self.read), timeout=6.0)
        if not result.ok:
            return False
        value = result.stdout.strip().strip("'\"").lower() in ("true", "1", "yes")
        return (not value) if self.inverted else value

    def set(self, silenced: bool) -> None:
        if self.key == "tessera":
            silence_own_popups(silenced)
            return
        argv = list(self.on if silenced else self.off)
        result = run(argv, timeout=8.0)
        if not result.ok:
            raise RuntimeError(result.text or f"{self.program} refused the change")


BACKENDS: tuple[Silencer, ...] = (
    # dunst, on tiling setups and anywhere the desktop has no daemon of its
    # own. Its own notion of paused is exactly this one.
    Silencer(
        "dunst",
        read=("dunstctl", "is-paused"),
        on=("dunstctl", "set-paused", "true"),
        off=("dunstctl", "set-paused", "false"),
    ),
    # GNOME. show-banners is the switch its own Do Not Disturb toggle flips.
    Silencer(
        "GNOME",
        read=("gsettings", "get", "org.gnome.desktop.notifications", "show-banners"),
        on=("gsettings", "set", "org.gnome.desktop.notifications", "show-banners", "false"),
        off=("gsettings", "set", "org.gnome.desktop.notifications", "show-banners", "true"),
        inverted=True,
        desktops=("gnome", "gnome-classic", "gnome-flashback", "gnome-xorg", "unity"),
    ),
    Silencer(
        "Cinnamon",
        read=("gsettings", "get", "org.cinnamon.desktop.notifications",
              "display-notifications"),
        on=("gsettings", "set", "org.cinnamon.desktop.notifications",
            "display-notifications", "false"),
        off=("gsettings", "set", "org.cinnamon.desktop.notifications",
             "display-notifications", "true"),
        inverted=True,
        desktops=("cinnamon", "x-cinnamon"),
    ),
    Silencer(
        "XFCE",
        read=("xfconf-query", "-c", "xfce4-notifyd", "-p", "/do-not-disturb"),
        on=("xfconf-query", "-c", "xfce4-notifyd", "-p", "/do-not-disturb",
            "-n", "-t", "bool", "-s", "true"),
        off=("xfconf-query", "-c", "xfce4-notifyd", "-p", "/do-not-disturb",
             "-n", "-t", "bool", "-s", "false"),
        desktops=("xfce",),
    ),
)


#: Tessera's own popups, which is all a platform with no scriptable switch
#: lets anyone silence.
OWN_POPUPS = Silencer(
    desktop="Tessera's own popups", read=(), on=(), off=(), key="tessera",
)

_own_popups_silenced = False


def own_popups_silenced() -> bool:
    return _own_popups_silenced


def silence_own_popups(quiet: bool) -> bool:
    global _own_popups_silenced
    _own_popups_silenced = bool(quiet)
    return True


def detect() -> Silencer | None:
    """The first backend whose read works on this desktop."""
    if not platform.IS_LINUX:
        return OWN_POPUPS
    for backend in BACKENDS:
        if backend.available():
            log.info("desktop notifications can be silenced through %s", backend.desktop)
            return backend
    return None
