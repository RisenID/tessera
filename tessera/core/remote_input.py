"""Pointer and key events from the phone, delivered to this desktop."""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Signal

from . import platform

log = logging.getLogger(__name__)


class RemoteInput(QObject):
    """Turns the phone's remote-screen events into input here."""

    #: Something the user should know: the permission was refused, or is being asked for.
    message = Signal(str)

    def __init__(self, hub, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.hub = hub
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

    def _apply(self, event: dict) -> None:
        backend = self._backend
        kind = event.get("k")
        try:
            if kind == "move":
                backend.move(float(event.get("dx", 0)), float(event.get("dy", 0)))
            elif kind == "scroll":
                backend.scroll(float(event.get("dx", 0)), float(event.get("dy", 0)))
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
