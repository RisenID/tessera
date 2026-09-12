"""QtDBus, where there is a QtDBus.

Three backends here speak D-Bus: KDE Connect, MPRIS and the desktop's Do Not
Disturb. All three are Linux desktop interfaces, and on Windows the module may
not even be in the Qt build. Importing it through here means those modules
still import -- and then quietly do nothing -- instead of taking the whole app
down on a platform that was never going to have a session bus.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

try:  # pragma: no cover - depends on the Qt build
    from PySide6.QtDBus import (
        QDBusConnection,
        QDBusMessage,
        QDBusServiceWatcher,
        QDBusVariant,
    )

    HAVE_QTDBUS = True
except ImportError:  # pragma: no cover - Windows, or a Qt built without it
    QDBusConnection = QDBusMessage = QDBusServiceWatcher = QDBusVariant = None
    HAVE_QTDBUS = False
    log.info("QtDBus is not available; D-Bus backends are switched off")


class _NoBus:
    """Stands in for a session bus that does not exist.

    Only the handful of calls the backends make on a bus they have not checked
    first. Everything answers "nothing here" rather than raising, because the
    callers already handle an unreachable service.
    """

    def isConnected(self) -> bool:  # noqa: N802 - Qt naming
        return False

    def interface(self):  # noqa: D102
        return None

    def call(self, *_args, **_kwargs):  # noqa: D102
        raise RuntimeError("no D-Bus session bus on this platform")

    def connect(self, *_args, **_kwargs) -> bool:  # noqa: D102
        return False

    def disconnect(self, *_args, **_kwargs) -> bool:  # noqa: D102
        return False


def session():
    """The session bus, or a stand-in that is never connected."""
    if not HAVE_QTDBUS:
        return _NoBus()
    return QDBusConnection.sessionBus()


def available() -> bool:
    """Whether there is a session bus to talk to."""
    return HAVE_QTDBUS and QDBusConnection.sessionBus().isConnected()
