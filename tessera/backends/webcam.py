"""Use the phone as a webcam."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from ..core.config import WebcamConfig
from ..core import packages
from ..core.proc import ManagedProcess, have, run

log = logging.getLogger(__name__)

MODULE = "v4l2loopback"
CARD_LABEL = "Tessera Camera"
V4L2_CLASS = Path("/sys/class/video4linux")


@dataclass(frozen=True)
class VideoDevice:
    path: str
    name: str
    virtual: bool = False

    @property
    def is_ours(self) -> bool:
        """Whether this is a loopback device Tessera can write to."""
        return self.virtual

    @property
    def label(self) -> str:
        return f"{self.name} ({self.path})"


class WebcamError(RuntimeError):
    pass


# -- environment checks ------------------------------------------------------


def scrcpy_available() -> bool:
    return have("scrcpy")


def scrcpy_version() -> tuple[int, int]:
    """scrcpy's (major, minor), or (0, 0) when it cannot be determined."""
    if not scrcpy_available():
        return (0, 0)
    result = run(["scrcpy", "--version"], timeout=10.0)
    match = re.search(r"(\d+)\.(\d+)", result.text)
    return (int(match.group(1)), int(match.group(2))) if match else (0, 0)


def ffmpeg_available() -> bool:
    return have("ffmpeg")


def module_loaded() -> bool:
    result = run(["lsmod"], timeout=5.0)
    return any(line.split()[:1] == [MODULE] for line in result.stdout.splitlines())


def module_installed() -> bool:
    """True when the v4l2loopback module exists for the running kernel."""
    return run(["modinfo", MODULE], timeout=10.0).ok


def video_devices() -> list[VideoDevice]:
    """Every /dev/videoN with its human-readable name."""
    found: list[VideoDevice] = []
    if not V4L2_CLASS.is_dir():
        return found
    for entry in sorted(V4L2_CLASS.iterdir(), key=lambda p: _index(p.name)):
        if not entry.name.startswith("video"):
            continue
        try:
            name = (entry / "name").read_text().strip()
        except OSError:
            name = entry.name
        # v4l2loopback nodes live under /sys/devices/virtual; a real capture
        # device resolves to its bus (pci, usb) instead.
        virtual = "/devices/virtual/" in str(entry.resolve())
        found.append(VideoDevice(f"/dev/{entry.name}", name, virtual))
    return found


def _index(name: str) -> int:
    match = re.search(r"(\d+)$", name)
    return int(match.group(1)) if match else 0


def loopback_devices() -> list[VideoDevice]:
    """Loopback devices Tessera can stream into, best match first."""
    devices = [d for d in video_devices() if d.is_ours]
    # If several exist, prefer the one this app labelled.
    devices.sort(key=lambda d: CARD_LABEL.lower() not in d.name.lower())
    return devices


def ensure_module(devices: int = 1) -> list[VideoDevice]:
    """Load v4l2loopback if needed and return the virtual devices."""
    existing = loopback_devices()
    if existing:
        return existing

    if not module_installed():
        raise WebcamError(
            "The v4l2loopback kernel module is not installed. "
            + packages.advice("v4l2loopback")
            + " It builds against the running kernel, so it also needs the "
            "kernel headers for that kernel."
        )

    if not have("pkexec"):
        raise WebcamError(
            "pkexec is unavailable, so the virtual camera cannot be loaded. "
            f"Run this manually: sudo modprobe {MODULE} exclusive_caps=1 "
            f'card_label="{CARD_LABEL}"'
        )

    log.info("loading %s", MODULE)
    result = run(
        [
            "pkexec", "modprobe", MODULE,
            f"devices={devices}",
            "exclusive_caps=1",          # make apps like Firefox/Chrome accept it
            f"card_label={CARD_LABEL}",
        ],
        timeout=60.0,
    )
    if not result.ok:
        # 126 is pkexec's "user dismissed or not authorised".
        if result.code == 126:
            raise WebcamError("Authentication was cancelled, so the camera was not started.")
        raise WebcamError(f"Could not load {MODULE}: {result.text}")

    created = loopback_devices()
    if not created:
        raise WebcamError(
            f"{MODULE} loaded but no virtual camera appeared. "
            "Try unloading it with: sudo modprobe -r v4l2loopback"
        )
    return created


def unload_module() -> None:
    """Remove the virtual camera. Best-effort; failure is not fatal."""
    if not module_loaded() or not have("pkexec"):
        return
    run(["pkexec", "modprobe", "-r", MODULE], timeout=30.0)


# -- camera enumeration ------------------------------------------------------


@dataclass(frozen=True)
class PhoneCamera:
    id: str
    facing: str
    sizes: str = ""

    @property
    def label(self) -> str:
        base = f"{self.facing.capitalize()} camera (id {self.id})"
        return f"{base} - {self.sizes}" if self.sizes else base


_CAMERA_LINE = re.compile(r"--camera-id=(\d+)\s+\((\w+)(?:,\s*)?([^)]*)\)")


def list_cameras(serial: str = "") -> list[PhoneCamera]:
    """Ask scrcpy which cameras the phone exposes."""
    if not scrcpy_available():
        return []
    argv = ["scrcpy", "--list-cameras"]
    if serial:
        argv += ["-s", serial]
    result = run(argv, timeout=30.0)
    cameras = []
    for match in _CAMERA_LINE.finditer(result.text):
        cameras.append(PhoneCamera(match.group(1), match.group(2), match.group(3).strip()))
    return cameras


# -- the running stream ------------------------------------------------------


class Webcam(QObject):
    """Supervises the scrcpy process feeding the virtual camera."""

    started = Signal(str)      # /dev/videoN
    stopped = Signal()
    output = Signal(str)
    failed = Signal(str)

    def __init__(self, config: WebcamConfig, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._config = config
        self._device = ""
        self._proc = ManagedProcess(self)
        self._proc.output.connect(self.output)
        self._proc.failed.connect(self._on_failed)
        self._proc.stopped.connect(self._on_stopped)

    @property
    def running(self) -> bool:
        return self._proc.running

    @property
    def device(self) -> str:
        return self._device

    def log_tail(self, lines: int = 200) -> str:
        return self._proc.log_tail(lines)

    def build_command(self, serial: str, device: str) -> list[str]:
        """Assemble the scrcpy invocation for the current settings."""
        cfg = self._config
        version = scrcpy_version()
        argv = ["scrcpy", f"--v4l2-sink={device}"]
        if serial:
            argv += ["-s", serial]
        if not cfg.audio:
            argv.append("--no-audio")

        if cfg.source == "camera":
            argv += [
                "--video-source=camera",
                f"--camera-facing={cfg.facing}",
                f"--camera-size={cfg.size}",
                f"--camera-fps={cfg.fps}",
            ]
        else:
            # Screen mirroring: cap the resolution so encoding keeps up.
            width = cfg.size.split("x")[0]
            argv += [f"--max-size={width}", f"--max-fps={cfg.fps}"]
        # We sink to v4l2, so scrcpy should not also decode for a window it
        # will never show. The flag was renamed in scrcpy 2.0.
        argv.append("--no-playback" if version >= (2, 0) else "--no-display")
        return argv

    def start(self, serial: str = "") -> None:
        if self.running:
            raise WebcamError("The virtual camera is already running.")
        if not scrcpy_available():
            raise WebcamError(
                "scrcpy is not installed. " + packages.advice("scrcpy")
            )
        if self._config.source == "camera" and scrcpy_version() < (2, 2):
            raise WebcamError(
                "Using the phone's camera needs scrcpy 2.2 or newer; "
                f"you have {'.'.join(map(str, scrcpy_version()))}. "
                "Switch the source to Screen, or update scrcpy."
            )

        device = self._config.device.strip()
        if device:
            if not Path(device).exists():
                raise WebcamError(f"{device} does not exist. Pick another device in Settings.")
        else:
            device = ensure_module()[0].path

        self._device = device
        argv = self.build_command(serial, device)
        log.info("starting virtual camera on %s", device)
        if not self._proc.start(argv):
            self._device = ""
            return
        self.started.emit(device)

    def stop(self) -> None:
        self._proc.stop()

    def _on_stopped(self, code: int) -> None:
        self._device = ""
        if code not in (0, 15, -15):  # 15/-15 is our own SIGTERM
            self.failed.emit(
                f"The camera stream stopped unexpectedly (exit {code}).\n"
                f"{self._proc.log_tail(8)}"
            )
        self.stopped.emit()

    def _on_failed(self, message: str) -> None:
        self._device = ""
        self.failed.emit(message)


class CompanionCamera(QObject):
    """Feeds the companion app's H.264 stream into a virtual camera."""

    started = Signal(str)
    stopped = Signal()
    output = Signal(str)        # ffmpeg's own diagnostics, line by line
    failed = Signal(str)

    def __init__(self, config: WebcamConfig, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._config = config
        self._proc = ManagedProcess(self)
        self._device = ""
        self._proc.failed.connect(self.failed)
        self._proc.output.connect(self.output)
        self._proc.stopped.connect(self._on_stopped)
        self._stopping = False

    @property
    def running(self) -> bool:
        return self._proc.running

    @property
    def device(self) -> str:
        return self._device

    def build_command(self, device: str) -> list[str]:
        """ffmpeg reading raw H.264 on stdin, writing to the loopback device."""
        return [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            # Low-latency decode: do not buffer ahead of the live stream.
            "-fflags", "nobuffer",
            "-flags", "low_delay",
            "-f", "h264",
            "-i", "pipe:0",
            "-vf", "format=yuv420p",
            "-f", "v4l2",
            device,
        ]

    def setup_needed(self):
        """A blocking step (pkexec modprobe) to run first, or None."""
        if self._config.device.strip() or loopback_devices():
            return None
        return ensure_module

    def start(self) -> str:
        """The device, or "" when ffmpeg would not start (failed says why)."""
        if self.running:
            raise WebcamError("The virtual camera is already running.")
        if not have("ffmpeg"):
            raise WebcamError("ffmpeg is not installed. " + packages.advice("ffmpeg"))

        device = self._config.device.strip() or ensure_module()[0].path
        self._device = device
        if not self._proc.start(self.build_command(device)):
            self._device = ""
            return ""
        self.started.emit(device)
        return device

    def feed(self, payload: bytes) -> None:
        """Push one encoded frame to ffmpeg."""
        self._proc.write(payload)

    def stop(self) -> None:
        self._stopping = True
        self._proc.stop()
        self._device = ""

    def _on_stopped(self, code: int) -> None:
        self._device = ""
        # A stop we asked for is not a failure, however the child chose to die.
        if not self._stopping and code not in (0, 15, -15, 255):
            self.failed.emit(
                f"The virtual camera stopped (exit {code}).\n{self._proc.log_tail(6)}"
            )
        self._stopping = False
        self.stopped.emit()
