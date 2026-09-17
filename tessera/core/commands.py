"""What the command socket can be asked to do. See cli.py for the other end."""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Callable

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication

from .hub import Hub
from .ipc import IpcServer

log = logging.getLogger(__name__)

Reply = Callable[[dict], None]

_URL = re.compile(r"^(https?|mailto|tel|geo|sms):\S+$", re.IGNORECASE)


def looks_like_url(text: str) -> bool:
    return bool(_URL.match(text.strip()))


def open_here(url: str) -> bool:
    """Open *url* with whatever this desktop uses for it."""
    parsed = QUrl(url.strip())
    if not parsed.isValid() or not looks_like_url(url):
        return False
    return bool(QDesktopServices.openUrl(parsed))


class Commands:
    """Turns socket requests into hub calls, answering when the phone has."""

    def __init__(self, hub: Hub, window=None) -> None:
        self.hub = hub
        self.window = window

    def register(self, server: IpcServer) -> None:
        for name in ("ping", "status", "notify", "sms", "send", "open", "copy", "ring",
                     "type", "photo", "mic", "timer", "alarm", "wifi", "show", "lock"):
            server.handle(name, getattr(self, f"_{name}"))

    # -- the phone as it is now ----------------------------------------------

    def _ping(self, _request: dict, reply: Reply) -> None:
        reply({"ok": True})

    def _status(self, _request: dict, reply: Reply) -> None:
        hub = self.hub
        status = hub.phone_status
        answer = {
            "ok": True,
            "connected": hub.connected,
            "source": hub.source,
            "phone": hub.phone_name,
            "battery": status.get("battery") or {},
            "wifi": status.get("wifi") or {},
            "cell": status.get("cell") or {},
            "ringer": hub.ringer,
            "volume": status.get("volume"),
            "dnd": hub.phone_dnd,
            "media": {k: hub.media.get(k) for k in ("title", "artist", "app", "playing")
                      if k in hub.media},
            "notifications": len(hub.notifications),
            "presence": getattr(hub, "presence_state", ""),
            "mic": hub.phone_mic.running if hasattr(hub, "phone_mic") else False,
        }
        codes = hub.recent_codes(1)
        if codes:
            match, note = codes[0]
            answer["code"] = match.code
            answer["code_from"] = note.app
        reply(answer)

    # -- things the phone does -----------------------------------------------

    def _phone(self, message: dict, reply: Reply, done: str, needs: str = "") -> None:
        """Send *message* and answer with the phone's verdict."""
        def answered(result: dict) -> None:
            if result.get("t") == "error":
                reply({"ok": False, "message": result.get("message") or "The phone refused."})
            else:
                reply({"ok": True, "message": done, **{k: v for k, v in result.items()
                                                       if k not in ("t", "rid")}})
        self.hub.ask(message, answered, needs=needs)

    def _notify(self, request: dict, reply: Reply) -> None:
        text = str(request.get("text", "")).strip()
        if not text:
            reply({"ok": False, "message": "Nothing to say."})
            return
        self._phone({"t": "notify", "title": str(request.get("title", "")), "text": text},
                    reply, "Shown on the phone.", needs="notify")

    def _sms(self, request: dict, reply: Reply) -> None:
        number, text = str(request.get("number", "")).strip(), str(request.get("text", "")).strip()
        if not number or not text:
            reply({"ok": False, "message": "A number and a message are needed."})
            return
        self._phone({"t": "sms_send", "address": number, "text": text}, reply, "Sent.",
                    needs="sms_send")

    def _send(self, request: dict, reply: Reply) -> None:
        files = [str(path) for path in request.get("files", []) if os.path.isfile(str(path))]
        if not files:
            reply({"ok": False, "message": "No such file."})
            return
        if not self.hub.connected:
            reply({"ok": False, "message": "The companion app is not connected."})
            return
        self.hub.send_files(files)
        reply({"ok": True, "message": f"Sending {len(files)} file{'s' if len(files) != 1 else ''}."})

    def _open(self, request: dict, reply: Reply) -> None:
        url = str(request.get("url", "")).strip()
        if not url:
            url = QGuiApplication.clipboard().text().strip()
            if not looks_like_url(url):
                reply({"ok": False, "message": "No link given, and none on the clipboard."})
                return
        if not looks_like_url(url):
            reply({"ok": False, "message": f"{url} is not a link the phone can open."})
            return
        self.hub.open_on_phone(url, lambda ok, message: reply({"ok": ok, "message": message}))

    def _copy(self, request: dict, reply: Reply) -> None:
        QGuiApplication.clipboard().setText(str(request.get("text", "")))
        reply({"ok": True, "message": "Copied."})

    def _ring(self, _request: dict, reply: Reply) -> None:
        self.hub.ring_phone(lambda message: reply({"ok": "Ringing" in message, "message": message}))

    def _type(self, request: dict, reply: Reply) -> None:
        text = str(request.get("text", ""))
        if not text and not request.get("enter"):
            reply({"ok": False, "message": "Nothing to type."})
            return
        self.hub.type_on_phone(text, bool(request.get("enter")),
                               lambda ok, message: reply({"ok": ok, "message": message}))

    def _photo(self, request: dict, reply: Reply) -> None:
        def done(ok: bool, message: str, path: str = "") -> None:
            reply({"ok": ok, "message": message, "path": path})

        self.hub.take_photo(
            facing=str(request.get("facing", "back")),
            path=str(request.get("path", "")),
            to_clipboard=bool(request.get("clipboard")),
            on_done=done,
        )

    def _mic(self, request: dict, reply: Reply) -> None:
        if request.get("on"):
            self.hub.start_phone_mic(lambda ok, message: reply({"ok": ok, "message": message}))
        else:
            self.hub.stop_phone_mic()
            reply({"ok": True, "message": "Stopped."})

    def _timer(self, request: dict, reply: Reply) -> None:
        seconds = int(request.get("seconds", 0) or 0)
        if seconds <= 0:
            reply({"ok": False, "message": "A timer needs a length."})
            return
        self._phone({"t": "timer_set", "seconds": seconds, "label": str(request.get("label", ""))},
                    reply, "Timer started on the phone.", needs="alarms")

    def _alarm(self, request: dict, reply: Reply) -> None:
        match = re.fullmatch(r"(\d{1,2}):(\d{2})", str(request.get("time", "")).strip())
        if not match or int(match.group(1)) > 23 or int(match.group(2)) > 59:
            reply({"ok": False, "message": "Give the time as HH:MM."})
            return
        self._phone({"t": "alarm_set", "hour": int(match.group(1)), "minute": int(match.group(2)),
                     "label": str(request.get("label", ""))},
                    reply, "Alarm set on the phone.", needs="alarms")

    def _wifi(self, request: dict, reply: Reply) -> None:
        self.hub.share_wifi(str(request.get("ssid", "")),
                            lambda ok, message: reply({"ok": ok, "message": message}))

    def _show(self, _request: dict, reply: Reply) -> None:
        self.hub.raiseRequested.emit()
        reply({"ok": True})

    def _lock(self, _request: dict, reply: Reply) -> None:
        from ..backends import lockscreen

        problem = lockscreen.lock()
        reply({"ok": problem is None, "message": problem or "Locked."})
