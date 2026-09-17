"""Locking this computer when the phone walks away."""

from __future__ import annotations

import logging
import weakref
from time import monotonic

from PySide6.QtCore import QObject, QTimer, Signal

from . import platform
from .config import PresenceConfig
from .proc import submit

log = logging.getLogger(__name__)

#: How often the signal is read.
POLL_MS = 5_000
#: A reading older than this no longer counts as the phone being heard.
FRESH_SECONDS = 20.0


class Presence(QObject):
    """near, far or gone, from the phone's beacon; locks once on leaving."""

    #: state ("off", "waiting", "near", "far", "gone"), and the last RSSI (0 when none).
    changed = Signal(str, int)

    @property
    def hub(self):
        hub = self._hub()
        if hub is None:
            raise RuntimeError("the hub is gone")
        return hub

    def __init__(self, hub, config: PresenceConfig, parent: QObject | None = None) -> None:
        super().__init__(parent)
        # Weak: the hub owns this object, and a cycle would keep a dropped hub alive.
        self._hub = weakref.ref(hub)
        self.config = config
        self.state = "off"
        self.rssi = 0
        self.message = ""
        self._watcher = None
        self._reading = False
        self.fresh_seconds = FRESH_SECONDS
        self._last_heard = 0.0
        self._last_near = 0.0
        #: Set once the phone has been near since the last lock, so a phone
        #: that is simply away when the app starts does not lock anything.
        self._armed = False
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._poll)
        hub.companion.connectedChanged.connect(lambda _on: self.apply())
        hub.companion.capabilitiesChanged.connect(lambda _c: self.apply())

    # -- switching on and off --------------------------------------------------

    def apply(self) -> None:
        """Start or stop watching, as the settings and the phone allow."""
        wanted = self.hub.config.features.presence and self.hub.companion.connected
        if not wanted:
            self.stop()
            return
        if self._watcher is not None:
            return
        if not self.hub.companion.supports("beacon"):
            self._set("off", 0, "The phone has not allowed the presence beacon; grant it in the phone app.")
            return
        self._set("waiting", 0, "Asking the phone for its beacon...")
        self.hub.ask({"t": "beacon_start"}, self._beacon_started, needs="beacon")

    def _beacon_started(self, reply: dict) -> None:
        if reply.get("t") == "error":
            self._set("off", 0, str(reply.get("message") or "The phone would not advertise."))
            return
        uuid = str(reply.get("uuid", ""))
        tag = str(reply.get("tag", ""))
        if platform.REAL == "windows":
            from ..backends.presence_win import WinrtWatcher as Watcher
        else:
            from ..backends.presence_linux import BluezWatcher as Watcher
        watcher = Watcher(uuid, tag)

        def started(problem: object) -> None:
            if problem:
                self._watcher = None
                self._set("off", 0, str(problem))
                return
            self._watcher = watcher
            self._last_heard = 0.0
            self._armed = False
            self._set("waiting", 0, "Listening for the phone...")
            self._timer.start()

        self._watcher = watcher
        submit(watcher.start, on_done=started, on_error=started)

    def stop(self) -> None:
        self._timer.stop()
        watcher, self._watcher = self._watcher, None
        if watcher is not None:
            submit(watcher.stop, on_error=lambda m: log.debug("presence stop: %s", m))
            if self.hub.companion.connected:
                self.hub.companion.send({"t": "beacon_stop"})
        if self.state != "off":
            self._set("off", 0, "")

    # -- readings --------------------------------------------------------------

    def _poll(self) -> None:
        watcher = self._watcher
        if watcher is None or self._reading:
            return
        self._reading = True
        submit(watcher.rssi, on_done=self._on_reading,
               on_error=lambda m: self._on_reading(None))

    def _on_reading(self, rssi: object) -> None:
        self._reading = False
        if self._watcher is None:
            return
        now = monotonic()
        if isinstance(rssi, int):
            self.rssi = rssi
            self._last_heard = now
            if rssi >= self.config.threshold_dbm:
                self._last_near = now
                self._armed = True
                self._set("near", rssi, "")
                return
        heard = now - self._last_heard < self.fresh_seconds
        away_for = now - self._last_near
        if away_for < self.config.away_seconds and self._last_near:
            # Briefly weak: not yet away.
            self._set("near", self.rssi if heard else 0, "")
            return
        state = "far" if heard else "gone"
        self._set(state, self.rssi if heard else 0, "")
        if self._armed and self.config.lock:
            self._armed = False
            self._lock(state)

    def _lock(self, state: str) -> None:
        from ..backends import lockscreen

        log.info("presence: the phone is %s; locking", state)
        self.hub.statusChanged.emit("Your phone left; locking the screen")
        submit(lockscreen.lock, on_done=lambda problem: problem and log.warning("presence lock: %s", problem),
               on_error=lambda m: log.warning("presence lock: %s", m))

    def _set(self, state: str, rssi: int, message: str) -> None:
        changed = state != self.state or rssi != self.rssi or message != self.message
        self.state, self.rssi, self.message = state, rssi, message
        if changed:
            self.changed.emit(state, rssi)

    def describe(self) -> str:
        if self.state == "off":
            return self.message or "Off."
        if self.state == "waiting":
            return self.message or "Listening for the phone..."
        strength = f" ({self.rssi} dBm)" if self.rssi else ""
        return {"near": "The phone is nearby", "far": "The phone is far",
                "gone": "The phone is out of range"}.get(self.state, self.state) + strength + "."
