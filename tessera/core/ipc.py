"""The running app's command socket: what `tessera <command>` and the applet talk to."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable

from PySide6.QtCore import QObject
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from . import platform

log = logging.getLogger(__name__)

#: A handler takes the request and a function to answer it with, so a command
#: that waits on the phone can answer later.
Handler = Callable[[dict, Callable[[dict], None]], None]


def socket_name() -> str:
    """Where the app listens: a socket path on Linux, a pipe name on Windows."""
    if platform.REAL == "windows":
        return f"tessera-{os.environ.get('USERNAME', 'user')}"
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        return os.path.join(runtime, "tessera.sock")
    return f"/tmp/tessera-{os.getuid()}.sock"


class IpcServer(QObject):
    """One JSON line in, one JSON line out, per connection."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._server = QLocalServer(self)
        self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self._server.newConnection.connect(self._accept)
        self._handlers: dict[str, Handler] = {}
        self._buffers: dict[int, bytearray] = {}

    def handle(self, command: str, handler: Handler) -> None:
        self._handlers[command] = handler

    def listen(self) -> bool:
        name = socket_name()
        # A socket left by a crash would refuse the new listener.
        QLocalServer.removeServer(name)
        if not self._server.listen(name):
            log.warning("command socket: %s", self._server.errorString())
            return False
        log.info("listening for commands on %s", name)
        return True

    def close(self) -> None:
        self._server.close()
        if platform.REAL != "windows":
            QLocalServer.removeServer(socket_name())

    def _accept(self) -> None:
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            socket.readyRead.connect(lambda s=socket: self._read(s))
            socket.disconnected.connect(lambda s=socket: self._forget(s))

    def _forget(self, socket: QLocalSocket) -> None:
        self._buffers.pop(id(socket), None)
        socket.deleteLater()

    def _read(self, socket: QLocalSocket) -> None:
        buffer = self._buffers.setdefault(id(socket), bytearray())
        buffer += bytes(socket.readAll())
        if b"\n" not in buffer or len(buffer) > 1_000_000:
            if len(buffer) > 1_000_000:
                socket.abort()
            return
        line, _, _rest = bytes(buffer).partition(b"\n")
        self._buffers.pop(id(socket), None)
        try:
            request = json.loads(line.decode("utf-8"))
            if not isinstance(request, dict):
                raise ValueError("not an object")
        except ValueError as exc:
            self._answer(socket, {"ok": False, "message": f"unreadable request: {exc}"})
            return
        self._dispatch(socket, request)

    def _dispatch(self, socket: QLocalSocket, request: dict) -> None:
        command = str(request.get("cmd", ""))
        handler = self._handlers.get(command)
        if handler is None:
            self._answer(socket, {"ok": False, "message": f"unknown command {command!r}"})
            return
        answered = False

        def reply(answer: dict) -> None:
            nonlocal answered
            if answered:
                return
            answered = True
            self._answer(socket, answer)

        try:
            handler(request, reply)
        except Exception as exc:  # noqa: BLE001 - a handler's bug must not take the app down
            log.exception("command %s failed", command)
            reply({"ok": False, "message": str(exc)})

    @staticmethod
    def _answer(socket: QLocalSocket, answer: dict) -> None:
        if socket.state() != QLocalSocket.LocalSocketState.ConnectedState:
            return
        socket.write(json.dumps(answer).encode("utf-8") + b"\n")
        socket.flush()
        socket.disconnectFromServer()


def call(request: dict, timeout_ms: int = 15_000) -> dict:
    """Send *request* to the running app and wait for its answer."""
    socket = QLocalSocket()
    socket.connectToServer(socket_name())
    if not socket.waitForConnected(1_500):
        return {"ok": False, "offline": True, "message": "Tessera is not running."}
    socket.write(json.dumps(request).encode("utf-8") + b"\n")
    socket.flush()
    buffer = bytearray()
    while b"\n" not in buffer:
        if not socket.waitForReadyRead(timeout_ms):
            if socket.state() != QLocalSocket.LocalSocketState.ConnectedState and buffer:
                break
            return {"ok": False, "message": "Tessera did not answer in time."}
        buffer += bytes(socket.readAll())
    line, _, _rest = bytes(buffer).partition(b"\n")
    try:
        answer = json.loads(line.decode("utf-8"))
    except ValueError:
        return {"ok": False, "message": "Tessera sent an unreadable answer."}
    return answer if isinstance(answer, dict) else {"ok": False, "message": "bad answer"}


def running() -> bool:
    """Whether another copy of the app holds the socket."""
    return bool(call({"cmd": "ping"}, timeout_ms=2_000).get("ok"))
