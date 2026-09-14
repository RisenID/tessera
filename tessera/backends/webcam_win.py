"""The phone's camera as a Windows webcam (Windows 11).

ffmpeg decodes the phone's H.264 to NV12, tessera-camera.exe puts up the
virtual camera, and TesseraCamera.dll, loaded by Frame Server, serves it.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, Signal

from ..core import packages, platform
from ..core.config import WebcamConfig
from ..core.proc import have, tool_path
from .webcam import WebcamError

log = logging.getLogger(__name__)

CAMERA_NAME = "Tessera Camera"
CLSID = "{7D3A4C1E-5B2F-4E8A-9C61-2F0B8D4E7A19}"
SOURCE_DLL = "TesseraCamera.dll"
HELPER_EXE = "tessera-camera.exe"
#: MFCreateVirtualCamera arrived in Windows 11.
MIN_BUILD = 22000
ERROR_CANCELLED = 1223


def native_dir() -> Path:
    """Where the camera component is: beside the frozen app, or build\\native."""
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        return Path(bundled)
    return Path(__file__).resolve().parents[2] / "build" / "native"


def windows_build() -> int:
    if platform.REAL != "windows":
        return 0
    return sys.getwindowsversion().build                    # type: ignore[attr-defined]


def installed_source() -> str:
    """The DLL registered for our CLSID, or ''."""
    try:
        import winreg
    except ImportError:
        return ""
    path = rf"SOFTWARE\Classes\CLSID\{CLSID}\InprocServer32"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0,
                            winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            return str(winreg.QueryValueEx(key, "")[0])
    except OSError:
        return ""


def target_for(source: Path) -> Path:
    """Named by content, so an update never overwrites a DLL Frame Server holds."""
    digest = hashlib.sha256(source.read_bytes()).hexdigest()[:12]
    base = os.environ.get("ProgramData") or r"C:\ProgramData"
    return Path(base) / "Tessera" / f"TesseraCamera-{digest}.dll"


def registered(source: Path) -> bool:
    current = installed_source()
    return bool(current) and Path(current) == target_for(source) and Path(current).is_file()


def registration_script(source: Path, target: Path) -> str:
    """PowerShell, run as administrator: copy where Frame Server can read it, register."""
    def quoted(value: object) -> str:
        return "'" + str(value).replace("'", "''") + "'"

    return "; ".join([
        "$ErrorActionPreference = 'Stop'",
        f"New-Item -ItemType Directory -Force -Path {quoted(target.parent)} | Out-Null",
        f"Copy-Item -LiteralPath {quoted(source)} -Destination {quoted(target)} -Force",
        # Frame Server runs as Local Service.
        f"icacls {quoted(target)} /grant '*S-1-5-19:(RX)' | Out-Null",
        f"$p = Start-Process -FilePath regsvr32.exe -ArgumentList {quoted(f'/s \"{target}\"')} "
        "-Wait -PassThru",
        "exit $p.ExitCode",
    ])


def _elevate(script: str) -> int:
    """Run *script* in an elevated PowerShell. Its exit code, or ERROR_CANCELLED."""
    import ctypes
    from ctypes import wintypes

    class ShellExecuteInfo(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong), ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR), ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR),
            ("hkeyClass", wintypes.HKEY), ("dwHotKey", wintypes.DWORD),
            ("hIconOrMonitor", wintypes.HANDLE), ("hProcess", wintypes.HANDLE),
        ]

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    info = ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x00000040 | 0x00000100            # NOCLOSEPROCESS | NOASYNC
    info.lpVerb = "runas"
    info.lpFile = "powershell.exe"
    info.lpParameters = (
        f"-NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand {encoded}"
    )
    info.nShow = 0
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        return ctypes.get_last_error()
    kernel32.WaitForSingleObject(info.hProcess, 0xFFFFFFFF)
    code = wintypes.DWORD()
    kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
    kernel32.CloseHandle(info.hProcess)
    return code.value


def register(source: Path) -> None:
    """Register the camera, once, with the user's approval."""
    code = _elevate(registration_script(source, target_for(source)))
    if code == ERROR_CANCELLED:
        raise WebcamError("Administrator approval was declined, so the camera was not set up.")
    if code != 0 or not registered(source):
        raise WebcamError(f"Setting up the camera failed (exit {code}).")


class WindowsCamera(QObject):
    """ffmpeg piped into tessera-camera.exe; the same face as CompanionCamera."""

    started = Signal(str)
    stopped = Signal()
    output = Signal(str)
    failed = Signal(str)

    def __init__(self, config: WebcamConfig, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._config = config
        self._decoder: QProcess | None = None
        self._presenter: QProcess | None = None
        self._stopping = False
        self._log: list[str] = []

    @property
    def running(self) -> bool:
        return (self._presenter is not None
                and self._presenter.state() != QProcess.ProcessState.NotRunning)

    @property
    def device(self) -> str:
        return CAMERA_NAME if self.running else ""

    def size(self) -> tuple[int, int]:
        width, _, height = self._config.size.partition("x")
        return int(width) // 2 * 2, int(height) // 2 * 2

    def commands(self) -> tuple[list[str], list[str]]:
        width, height = self.size()
        decoder = [
            tool_path("ffmpeg"), "-hide_banner", "-loglevel", "error",
            "-fflags", "nobuffer", "-flags", "low_delay",
            "-f", "h264", "-i", "pipe:0",
            "-vf", f"scale={width}:{height}", "-pix_fmt", "nv12",
            "-f", "rawvideo", "pipe:1",
        ]
        presenter = [str(native_dir() / HELPER_EXE), str(width), str(height), CAMERA_NAME]
        return decoder, presenter

    def check(self) -> None:
        """Raise WebcamError naming whatever stops the camera starting."""
        if windows_build() < MIN_BUILD:
            raise WebcamError("The webcam needs Windows 11.")
        if not have("ffmpeg"):
            raise WebcamError("ffmpeg is not installed. " + packages.advice("ffmpeg"))
        if not (native_dir() / SOURCE_DLL).is_file() or not (native_dir() / HELPER_EXE).is_file():
            raise WebcamError("This build of Tessera has no camera component.")

    def start(self) -> str:
        if self.running:
            raise WebcamError("The virtual camera is already running.")
        self.check()
        source = native_dir() / SOURCE_DLL
        if not registered(source):
            register(source)

        decoder_argv, presenter_argv = self.commands()
        self._stopping = False
        self._log = []
        decoder, presenter = QProcess(self), QProcess(self)
        decoder.setStandardOutputProcess(presenter)
        decoder.readyReadStandardError.connect(lambda: self._read(decoder))
        presenter.readyReadStandardError.connect(lambda: self._read(presenter))
        presenter.finished.connect(self._on_finished)
        presenter.start(presenter_argv[0], presenter_argv[1:])
        decoder.start(decoder_argv[0], decoder_argv[1:])
        if not presenter.waitForStarted(5000) or not decoder.waitForStarted(5000):
            reason = presenter.errorString() or decoder.errorString()
            for process in (decoder, presenter):
                process.kill()
            raise WebcamError(f"Could not start the camera: {reason}")
        self._decoder, self._presenter = decoder, presenter
        log.info("virtual camera %s at %dx%d", CAMERA_NAME, *self.size())
        self.started.emit(CAMERA_NAME)
        return CAMERA_NAME

    def feed(self, payload: bytes) -> None:
        if self._decoder is not None and self._decoder.state() == QProcess.ProcessState.Running:
            self._decoder.write(payload)

    def stop(self) -> None:
        self._stopping = True
        decoder, presenter = self._decoder, self._presenter
        if decoder is not None:
            # Closing ffmpeg's input ends it, which ends the camera's input.
            decoder.closeWriteChannel()
            if not decoder.waitForFinished(3000):
                decoder.kill()
        if presenter is not None and not presenter.waitForFinished(3000):
            presenter.kill()

    def _read(self, process: QProcess) -> None:
        text = bytes(process.readAllStandardError()).decode("utf-8", "replace")
        for line in text.splitlines():
            if line.strip():
                self._log.append(line)
                self.output.emit(line)

    def _on_finished(self, code: int, _status) -> None:
        if self._decoder is not None and self._decoder.state() != QProcess.ProcessState.NotRunning:
            self._decoder.kill()
        self._decoder = self._presenter = None
        if not self._stopping and code != 0:
            tail = "\n".join(self._log[-6:])
            self.failed.emit(f"The virtual camera stopped (exit {code}).\n{tail}".strip())
        self._stopping = False
        self.stopped.emit()
