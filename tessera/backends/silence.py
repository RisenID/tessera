"""Silencing desktop notifications, on whichever desktop this is.

Plasma implements Inhibit/UnInhibit/Inhibited on org.freedesktop.Notifications
and Tessera uses it where it exists: it is reversible, it hands back a cookie,
and it leaves an inhibition the user set themselves alone. But it is a Plasma
extension rather than part of the notification specification, and nobody else
implements it. GNOME, Cinnamon, XFCE and the standalone daemons each have their
own switch, so on those desktops Do Not Disturb sync used to appear to work --
the service is registered, so the check passed -- and then quietly do nothing.

Each backend here is chosen by whether its *read* succeeds. That matters more
than it looks: it means an entry for a desktop nobody has tested is either
correct or skipped, never selected and then silently ineffective.

Deliberately absent: mako. Its modes mechanism can be told to add a
do-not-disturb mode whether or not the user's config defines one, so the read
would succeed and the write would do nothing -- exactly the failure this
module exists to avoid.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from ..core.proc import have, run

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
    #: XDG_CURRENT_DESKTOP. Empty means any -- which is right only for a
    #: backend that talks to the notification daemon itself.
    desktops: tuple[str, ...] = ()

    @property
    def program(self) -> str:
        return self.read[0]

    def running_desktop(self) -> bool:
        """Whether this is the desktop in front of the user.

        A readable setting is not proof that anything acts on it. gsettings
        and GNOME's notification schema are installed on this KDE machine as a
        dependency of other software, so the GNOME read succeeds here and
        setting show-banners=false silences precisely nothing. The session has
        to be asked as well.
        """
        if not self.desktops:
            return True
        current = ":".join(
            os.environ.get(name, "")
            for name in ("XDG_CURRENT_DESKTOP", "XDG_SESSION_DESKTOP", "DESKTOP_SESSION")
        ).lower()
        return any(token in current.split(":") for token in self.desktops)

    def available(self) -> bool:
        """Whether this desktop's switch is here, readable, and listened to."""
        return (
            self.running_desktop()
            and have(self.program)
            and run(list(self.read), timeout=6.0).ok
        )

    def silenced(self) -> bool:
        result = run(list(self.read), timeout=6.0)
        if not result.ok:
            return False
        value = result.stdout.strip().strip("'\"").lower() in ("true", "1", "yes")
        return (not value) if self.inverted else value

    def set(self, silenced: bool) -> None:
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


def detect() -> Silencer | None:
    """The first backend whose read works on this desktop."""
    for backend in BACKENDS:
        if backend.available():
            log.info("desktop notifications can be silenced through %s", backend.desktop)
            return backend
    return None
