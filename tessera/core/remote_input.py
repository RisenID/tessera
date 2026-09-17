"""Pointer and key events from the phone, delivered to this desktop."""

from __future__ import annotations

import logging
import weakref

from PySide6.QtCore import QObject, Signal

from . import platform

log = logging.getLogger(__name__)

#: Millimetres of finger travel that cross this screen at base gain. The phone
#: accelerates fast moves up to about 2.8x, so a flick across a typical 65 mm
#: trackpad still spans the whole screen.
SPAN_MM = 160.0


class RemoteInput(QObject):
    """Turns the phone's remote-screen events into input here."""

    #: Something the user should know: the permission was refused, or is being asked for.
    message = Signal(str)

    @property
    def hub(self):
        hub = self._hub()
        if hub is None:
            raise RuntimeError("the hub is gone")
        return hub

    def __init__(self, hub, parent: QObject | None = None) -> None:
        super().__init__(parent)
        # Weak: the hub owns this object, and a cycle would keep a dropped hub alive.
        self._hub = weakref.ref(hub)
        self._backend = None
        self._queued: list[dict] = []
        self._asked = False
        hub.companion.inputEvent.connect(self._on_event)

    def backend(self):
        if self._backend is not None:
            return self._backend
        if platform.REAL == "windows":
            from ..backends.input_win import WindowsInput

            self._backend = WindowsInput()
        else:
            from ..backends.input_linux import PortalInput

            portal = PortalInput(self.hub.config.remote_input.restore_token, self)
            portal.on_token = self._save_token
            self._backend = portal
        return self._backend

    def _save_token(self, token: str) -> None:
        self.hub.config.remote_input.restore_token = token
        self.hub.config.save()

    def _on_event(self, event: dict) -> None:
        if not self.hub.config.features.remote_input:
            return
        backend = self.backend()
        if getattr(backend, "running", True):
            self._apply(event)
            return
        # Opening the portal session asks the desktop once; events wait for it.
        self._queued.append(event)
        del self._queued[:-200]
        if not self._asked:
            self._asked = True
            self.message.emit("Allow Tessera to control the pointer and keyboard when the desktop asks.")
            backend.start(self._ready)

    def _ready(self, ok: bool, problem: str) -> None:
        self._asked = False
        queued, self._queued = self._queued, []
        if not ok:
            self.message.emit(problem)
            return
        for event in queued:
            self._apply(event)

    def _scale(self) -> float:
        """Pixels here per millimetre of finger travel on the phone.

        Sized to this screen rather than to any particular phone or monitor:
        SPAN_MM of finger crosses the screen at base gain, whatever its size.
        """
        from PySide6.QtGui import QGuiApplication

        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return 4.0
        width = screen.size().width()
        if platform.REAL == "windows":
            # SendInput moves in physical pixels; the portal in logical ones.
            width *= screen.devicePixelRatio()
        return width / SPAN_MM

    def _apply(self, event: dict) -> None:
        backend = self._backend
        kind = event.get("k")
        try:
            if kind == "move":
                scale = self._scale()
                backend.move(float(event.get("dx", 0)) * scale, float(event.get("dy", 0)) * scale)
            elif kind == "scroll":
                scale = self._scale()
                backend.scroll(float(event.get("dx", 0)) * scale, float(event.get("dy", 0)) * scale)
            elif kind == "click":
                backend.click(str(event.get("b", "left")))
            elif kind == "button":
                backend.button(str(event.get("b", "left")), bool(event.get("down")))
            elif kind == "key":
                backend.key(str(event.get("name", "")))
            elif kind == "text":
                backend.text(str(event.get("text", "")))
        except Exception as exc:  # noqa: BLE001 - one bad event must not stop the stream
            log.debug("remote input %s failed: %s", kind, exc)

    def stop(self) -> None:
        if self._backend is not None:
            self._backend.stop()
