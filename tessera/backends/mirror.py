"""Screen mirroring and per-app windows."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal

from ..core import packages
from ..core.proc import ManagedProcess, have, run

log = logging.getLogger(__name__)

#: Virtual displays (--new-display / --start-app) arrived in scrcpy 3.0.
MIN_VIRTUAL_DISPLAY = (3, 0)
#: Resizable virtual displays (--flex-display) arrived in scrcpy 4.0.
MIN_FLEX_DISPLAY = (4, 0)


class MirrorError(RuntimeError):
    pass


def available() -> bool:
    return have("scrcpy")


def version() -> tuple[int, int]:
    if not available():
        return (0, 0)
    match = re.search(r"(\d+)\.(\d+)", run(["scrcpy", "--version"], timeout=10.0).text)
    return (int(match.group(1)), int(match.group(2))) if match else (0, 0)


@dataclass(frozen=True)
class MirrorOptions:
    """Tunables shared by both window kinds."""

    max_size: int = 1600          # long edge, in pixels; 0 means native
    fps: int = 60
    bitrate: str = "8M"
    audio: bool = True
    stay_awake: bool = True
    turn_screen_off: bool = False  # mirror without lighting up the phone
    borderless: bool = False

    def base_args(self) -> list[str]:
        argv: list[str] = []
        if self.max_size:
            argv.append(f"--max-size={self.max_size}")
        argv += [f"--max-fps={self.fps}", f"--video-bit-rate={self.bitrate}"]
        if not self.audio:
            argv.append("--no-audio")
        if self.stay_awake:
            argv.append("--stay-awake")
        if self.turn_screen_off:
            argv.append("--turn-screen-off")
        if self.borderless:
            argv.append("--window-borderless")
        return argv


def mirror_command(serial: str, options: MirrorOptions, title: str = "Phone") -> list[str]:
    """Full-screen mirror with touch and keyboard control."""
    if not available():
        raise MirrorError("scrcpy is not installed. " + packages.advice("scrcpy"))
    argv = ["scrcpy", *options.base_args(), f"--window-title={title}"]
    if serial:
        argv += ["-s", serial]
    return argv


def app_command(
    serial: str,
    package: str,
    options: MirrorOptions,
    size: str = "1280x800",
    dpi: int = 0,
    title: str = "",
    resizable: bool = True,
) -> list[str]:
    """One app on its own virtual display."""
    current = version()
    if current < MIN_VIRTUAL_DISPLAY:
        raise MirrorError(
            "Opening a single app in its own window needs scrcpy 3.0 or newer; "
            f"you have {current[0]}.{current[1]}. Use full screen mirroring, or "
            "update scrcpy."
        )

    display = size if not dpi else f"{size}/{dpi}"
    argv = [
        "scrcpy",
        *options.base_args(),
        f"--new-display={display}",
        f"--start-app={package}",
        f"--window-title={title or package}",
    ]
    # A flexible display resizes with the window instead of letterboxing.
    if resizable and current >= MIN_FLEX_DISPLAY:
        argv.append("--flex-display")
    if serial:
        argv += ["-s", serial]
    return argv


class MirrorSession(QObject):
    """One scrcpy window: either the phone's screen or a single app."""

    started = Signal()
    stopped = Signal(int)
    failed = Signal(str)

    def __init__(self, label: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.label = label
        self._proc = ManagedProcess(self)
        self._proc.stopped.connect(self._on_stopped)
        self._proc.failed.connect(self.failed)

    @property
    def running(self) -> bool:
        return self._proc.running

    def log_tail(self, lines: int = 40) -> str:
        return self._proc.log_tail(lines)

    def start(self, argv: list[str]) -> None:
        log.info("mirror: %s", " ".join(argv))
        if self._proc.start(argv):
            self.started.emit()

    def stop(self) -> None:
        self._proc.stop()

    def _on_stopped(self, code: int) -> None:
        # Closing the scrcpy window is the normal way to end a session, and it
        # exits non-zero on some builds; only surface a genuine startup failure.
        if code not in (0, 1, 15, -15):
            self.failed.emit(
                f"{self.label} closed unexpectedly (exit {code}).\n{self._proc.log_tail(6)}"
            )
        self.stopped.emit(code)


class MirrorManager(QObject):
    """Tracks every open mirror window so they can be closed together."""

    changed = Signal()
    failed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._sessions: dict[str, MirrorSession] = {}

    @property
    def sessions(self) -> dict[str, MirrorSession]:
        return dict(self._sessions)

    def is_running(self, key: str) -> bool:
        session = self._sessions.get(key)
        return session is not None and session.running

    def open(self, key: str, label: str, argv: list[str]) -> None:
        if self.is_running(key):
            raise MirrorError(f"{label} is already open.")

        session = MirrorSession(label, self)
        session.failed.connect(self.failed)
        session.stopped.connect(lambda _code, k=key: self._forget(k))
        self._sessions[key] = session
        session.start(argv)
        if not session.running:
            # failed has already said why.
            self._sessions.pop(key, None)
            session.deleteLater()
        self.changed.emit()

    def close(self, key: str) -> None:
        session = self._sessions.get(key)
        if session is not None:
            session.stop()

    def close_all(self) -> None:
        for session in list(self._sessions.values()):
            session.stop()

    def _forget(self, key: str) -> None:
        self._sessions.pop(key, None)
        self.changed.emit()
