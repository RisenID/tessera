"""The phone's clipboard over adb, for a phone without Shizuku.

Android lets no ordinary app read the clipboard in the background, but the
shell user may -- which is why the companion app's own clipboard route needs
Shizuku. adb *is* the shell user. So when the phone cannot share its clipboard
itself and adb is connected, this starts a small helper out of the installed
companion APK, the way scrcpy starts its server:

    adb shell CLASSPATH=<base.apk> app_process / dev.tessera.companion.shell.ClipboardHelper

The clipboard service tells the helper about each change, so nothing polls.
The helper speaks one JSON object per line on stdout and reads the same on
stdin; it exits when adb goes away, and this notices and says so.
"""

from __future__ import annotations

import json
import logging

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from ..core import platform

log = logging.getLogger(__name__)

PACKAGE = "dev.tessera.companion"
HELPER = "dev.tessera.companion.shell.ClipboardHelper"


def helper_command() -> str:
    """The one shell line that starts the helper from wherever the APK is installed."""
    # pm path prints "package:/data/app/.../base.apk"; the first line is the
    # base APK, which is where the classes are.
    return (
        f"CLASSPATH=$(pm path {PACKAGE} | head -n 1 | cut -d: -f2) "
        f"exec app_process / {HELPER}"
    )


def argv(serial: str) -> list[str]:
    adb = platform.find_tool("adb") or platform.tool("adb")
    # -T: no terminal. A pty would echo what we write and mangle line endings.
    return [adb, "-s", serial, "shell", "-T", helper_command()]


class AdbClipboard(QObject):
    """One helper process, restarted while it is wanted and adb is there."""

    changed = Signal(str)          # the phone's clipboard, when it changes
    runningChanged = Signal(bool)

    #: A helper that dies straight away (no companion app, adb dropped) is not
    #: restarted in a tight loop.
    RETRY_MS = 20_000

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._serial = ""
        self._process: QProcess | None = None
        self._buffer = b""
        self._ready = False
        self._retry = QTimer(self)
        self._retry.setSingleShot(True)
        self._retry.timeout.connect(self._spawn)

    @property
    def running(self) -> bool:
        return self._ready

    @property
    def serial(self) -> str:
        return self._serial

    def start(self, serial: str) -> None:
        if not serial:
            self.stop()
            return
        if serial == self._serial and (self._process is not None or self._retry.isActive()):
            return
        self.stop()
        self._serial = serial
        self._spawn()

    def stop(self) -> None:
        self._serial = ""
        self._retry.stop()
        process, self._process = self._process, None
        if process is not None:
            process.finished.disconnect()
            process.readyReadStandardOutput.disconnect()
            process.write(b'{"t":"quit"}\n')
            process.closeWriteChannel()
            if not process.waitForFinished(500):
                process.kill()
                process.waitForFinished(500)
            process.deleteLater()
        self._set_ready(False)

    def send(self, text: str) -> bool:
        return self._write({"t": "set", "text": text})

    def pull(self) -> bool:
        return self._write({"t": "get"})

    # -- the process ---------------------------------------------------------

    def _write(self, message: dict) -> bool:
        if not self._ready or self._process is None:
            return False
        line = json.dumps(message, ensure_ascii=False).encode("utf-8") + b"\n"
        return self._process.write(line) == len(line)

    def _spawn(self) -> None:
        if not self._serial or self._process is not None:
            return
        command = argv(self._serial)
        process = QProcess(self)
        process.readyReadStandardOutput.connect(self._on_output)
        process.finished.connect(self._on_finished)
        self._buffer = b""
        self._process = process
        log.debug("starting the clipboard helper: %s", " ".join(command))
        process.start(command[0], command[1:])

    def _on_output(self) -> None:
        if self._process is None:
            return
        self._buffer += bytes(self._process.readAllStandardOutput())
        *lines, self._buffer = self._buffer.split(b"\n")
        for line in lines:
            self.feed(line)

    def feed(self, line: bytes | str) -> None:
        """Handle one line from the helper. Public so it can be checked without a phone."""
        if isinstance(line, bytes):
            line = line.decode("utf-8", "replace")
        line = line.strip()
        if not line.startswith("{"):
            # adb's own complaints, or "Error: Could not find class" when the
            # companion app is missing or too old for this.
            if line:
                log.info("clipboard helper: %s", line)
            return
        try:
            message = json.loads(line)
        except ValueError:
            return
        kind = message.get("t")
        if kind == "ready":
            log.info("clipboard over adb (listening=%s)", message.get("listening"))
            self._set_ready(True)
        elif kind == "clip":
            self.changed.emit(str(message.get("text", "")))
        elif kind == "error":
            log.warning("clipboard helper: %s", message.get("message"))

    def _on_finished(self, code: int, _status) -> None:
        process, self._process = self._process, None
        if process is not None:
            stderr = bytes(process.readAllStandardError()).decode("utf-8", "replace").strip()
            if stderr:
                log.info("clipboard helper exited (%s): %s", code, stderr[-300:])
            process.deleteLater()
        self._set_ready(False)
        if self._serial:
            self._retry.start(self.RETRY_MS)

    def _set_ready(self, ready: bool) -> None:
        if ready != self._ready:
            self._ready = ready
            self.runningChanged.emit(ready)
