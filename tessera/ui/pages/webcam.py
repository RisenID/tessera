"""Use the phone as a webcam.

Every choice is enumerated from the phone rather than hardcoded.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...backends import webcam
from ...core.hub import Hub
from ...core import packages
from ..theme import SPACE, Palette
from ..widgets import Card, Pill, Toast, heading

#: Shown before the phone has reported anything, and when running over adb.
FALLBACK_SIZES = ["1920x1080", "1280x720", "640x480"]
FALLBACK_FPS = [30, 24, 15]

#: The resolutions worth offering, tallest first. A modern phone reports twenty
#: or more output sizes -- sensor crops, odd aspect ratios, thumbnail formats --
#: and listing them all makes the menu unusable. Only these standard heights are
#: shown, and only when the phone actually supports them.
STANDARD_HEIGHTS: tuple[tuple[int, str], ...] = (
    (2160, "2160p · 4K UHD"),
    (1440, "1440p · QHD"),
    (1080, "1080p · Full HD"),
    (720, "720p · HD"),
    (480, "480p"),
    (360, "360p"),
)

#: Widescreen, used to pick between several widths offered at the same height.
TARGET_ASPECT = 16 / 9

#: Frame rates worth offering. A camera's auto-exposure ranges include odd upper
#: bounds (26, 27, 53 on this phone) that exist for exposure control rather than
#: as sensible capture rates; offering them just clutters the menu.
COMMON_FPS: tuple[int, ...] = (240, 120, 90, 60, 50, 30, 25, 24, 15)


def _standard_sizes(sizes: list[dict]) -> list[dict]:
    """Reduce the phone's full size list to the standard resolutions.

    A phone typically offers several widths at each height (1920x1080 alongside
    2400x1080 and 1440x1080, say). The one closest to 16:9 is the one people
    mean by "1080p", so that is what gets offered.
    """
    by_height: dict[int, list[dict]] = {}
    for entry in sizes:
        width, height = entry.get("w", 0), entry.get("h", 0)
        if width and height:
            by_height.setdefault(height, []).append(entry)

    chosen: list[dict] = []
    for height, label in STANDARD_HEIGHTS:
        candidates = by_height.get(height)
        if not candidates:
            continue
        best = min(candidates, key=lambda e: abs(e["w"] / e["h"] - TARGET_ASPECT))
        chosen.append(
            {
                "label": f"{best['w']}x{best['h']}",
                "text": f"{label}  ·  {best['w']}x{best['h']}  ·  up to {best.get('maxFps', 30)}fps",
                "maxFps": best.get("maxFps", 30),
            }
        )
    return chosen


class WebcamPage(QWidget):
    def __init__(self, hub: Hub, palette: Palette, parent: QWidget | None = None):
        super().__init__(parent)
        self.hub = hub
        self.palette_tokens = palette
        self._cameras: list[dict] = []
        self._loading = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(SPACE["xl"], SPACE["xl"], SPACE["xl"], SPACE["xl"])
        outer.setSpacing(SPACE["lg"])
        outer.addWidget(
            heading("Webcam", "Your phone's camera, as an ordinary webcam for any Linux app")
        )

        card = Card(self)
        row = QHBoxLayout()
        title = QLabel("Virtual camera")
        title.setObjectName("SectionTitle")
        row.addWidget(title)
        row.addStretch(1)
        self.state_pill = Pill("Stopped", "muted")
        self.state_pill.apply(palette)
        row.addWidget(self.state_pill)
        card.body().addLayout(row)

        self.source = QComboBox()
        self.source.currentIndexChanged.connect(self._on_source_changed)
        card.add(self._labelled("Source", self.source))

        self.size = QComboBox()
        self.size.currentIndexChanged.connect(self._on_size_changed)
        card.add(self._labelled("Resolution", self.size))

        self.fps = QComboBox()
        card.add(self._labelled("Frame rate", self.fps))

        buttons = QHBoxLayout()
        self.start_button = QPushButton("Start camera")
        self.start_button.setObjectName("Primary")
        self.start_button.clicked.connect(self._toggle)
        buttons.addWidget(self.start_button)

        refresh = QPushButton("Re-read phone")
        refresh.setObjectName("Ghost")
        refresh.clicked.connect(hub.refresh_device_caps)
        buttons.addWidget(refresh)
        buttons.addStretch(1)
        card.body().addLayout(buttons)

        self.status = QLabel()
        self.status.setObjectName("Muted")
        self.status.setWordWrap(True)
        card.add(self.status)
        outer.addWidget(card)

        info = Card(self)
        note = QLabel(
            "Appears as an ordinary camera in Firefox, Chrome, OBS and Zoom. "
            "Without the companion app only the fallback resolutions work."
        )
        note.setObjectName("Muted")
        note.setWordWrap(True)
        info.add(note)
        outer.addWidget(info)
        outer.addStretch(1)

        self.toast = Toast(self)
        hub.cameraStarted.connect(self._on_started)
        hub.cameraStopped.connect(self._on_stopped)
        hub.cameraFailed.connect(self._on_failed)
        hub.deviceCapsChanged.connect(self._on_caps)

        self._populate(hub.device_caps.get("cameras", []))
        self._check_environment()

    @staticmethod
    def _labelled(text: str, widget: QWidget) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        label = QLabel(text)
        label.setMinimumWidth(150)
        layout.addWidget(label)
        layout.addWidget(widget, 1)
        return container

    # -- populating from the phone -------------------------------------------

    def _on_caps(self, caps: dict) -> None:
        self._populate(caps.get("cameras", []))

    def _populate(self, cameras: list) -> None:
        """Rebuild the source list, restoring the saved selection if it still exists."""
        self._loading = True
        cfg = self.hub.config.webcam
        self._cameras = cameras

        self.source.clear()
        seen_facings: dict[str, int] = {}
        for camera in cameras:
            facing = camera.get("facing", "back")
            seen_facings[facing] = seen_facings.get(facing, 0) + 1
            # "Back camera", then "Back camera 2" for the extra lenses.
            ordinal = seen_facings[facing]
            name = f"{facing.capitalize()} camera"
            if ordinal > 1:
                name = f"{name} {ordinal}"
            best = _standard_sizes(camera.get("sizes", []))
            if best:
                name += f"  ·  up to {best[0]['label']}"
            self.source.addItem(name, camera)

        self.source.addItem("Phone screen", {"screen": True})

        # Restore selection: exact camera id first, then facing.
        index = -1
        if cfg.source == "screen":
            index = self.source.count() - 1
        elif cfg.camera_id:
            index = next(
                (i for i in range(self.source.count())
                 if (self.source.itemData(i) or {}).get("id") == cfg.camera_id),
                -1,
            )
        if index < 0:
            index = next(
                (i for i in range(self.source.count())
                 if (self.source.itemData(i) or {}).get("facing") == cfg.facing),
                0,
            )
        self.source.setCurrentIndex(max(index, 0))

        self._loading = False
        self._rebuild_sizes()

    def _current_camera(self) -> dict | None:
        data = self.source.currentData()
        if not isinstance(data, dict) or data.get("screen"):
            return None
        return data

    def _rebuild_sizes(self) -> None:
        """Resolutions for the selected camera, largest first."""
        self._loading = True
        cfg = self.hub.config.webcam
        camera = self._current_camera()

        self.size.clear()
        if camera is None:
            # Screen mirroring, or no phone: offer the safe fixed list.
            for value in FALLBACK_SIZES:
                self.size.addItem(value, {"label": value})
        else:
            for entry in _standard_sizes(camera.get("sizes", [])):
                self.size.addItem(entry["text"], entry)
            if self.size.count() == 0:
                for value in FALLBACK_SIZES:
                    self.size.addItem(value, {"label": value})

        index = next(
            (i for i in range(self.size.count())
             if (self.size.itemData(i) or {}).get("label") == cfg.size),
            0,
        )
        self.size.setCurrentIndex(max(index, 0))
        self._loading = False
        self._rebuild_fps()

    def _rebuild_fps(self) -> None:
        """Frame rates the phone will honour at the chosen resolution.

        The phone reports the rates its auto-exposure can target and a ceiling
        per resolution; only the rates at or below that ceiling are offered, so
        4K does not list 60fps it cannot deliver.
        """
        self._loading = True
        cfg = self.hub.config.webcam
        camera = self._current_camera()
        size_data = self.size.currentData() or {}
        ceiling = int(size_data.get("maxFps", 0) or 0)

        rates: list[int] = []
        if camera is not None:
            reported = {int(v) for v in camera.get("fps", []) if int(v) > 0}
            allowed = {r for r in reported if not ceiling or r <= ceiling}
            # Standard rates the phone actually reports.
            rates = [r for r in COMMON_FPS if r in allowed]
            if not rates:
                # Nothing standard on offer; fall back to whatever it reported,
                # rather than showing an empty menu.
                rates = sorted(allowed, reverse=True)
            if not rates and ceiling:
                rates = [r for r in COMMON_FPS if r <= ceiling]
        if not rates:
            rates = list(FALLBACK_FPS)

        self.fps.clear()
        for rate in rates:
            self.fps.addItem(f"{rate} fps", rate)

        index = next(
            (i for i in range(self.fps.count()) if self.fps.itemData(i) == cfg.fps),
            -1,
        )
        if index < 0:
            # Prefer 30 when the saved rate is unavailable at this resolution.
            index = next(
                (i for i in range(self.fps.count()) if self.fps.itemData(i) == 30), 0
            )
        self.fps.setCurrentIndex(max(index, 0))
        self._loading = False

    def _on_source_changed(self) -> None:
        if not self._loading:
            self._rebuild_sizes()

    def _on_size_changed(self) -> None:
        if not self._loading:
            self._rebuild_fps()

    # -- running -------------------------------------------------------------

    def _save(self) -> None:
        cfg = self.hub.config.webcam
        camera = self._current_camera()
        if camera is None:
            cfg.source = "screen"
        else:
            cfg.source = "camera"
            cfg.facing = camera.get("facing", "back")
            cfg.camera_id = str(camera.get("id", ""))
        cfg.size = (self.size.currentData() or {}).get("label", cfg.size)
        cfg.fps = int(self.fps.currentData() or cfg.fps)
        self.hub.config.save()

    def _toggle(self) -> None:
        if self.hub.camera_running:
            self.hub.stop_camera()
            return
        self._save()
        try:
            self.hub.start_camera()
        except webcam.WebcamError as exc:
            self.status.setText(str(exc))
            self.toast.show_message("Could not start the camera", self.palette_tokens, "danger")

    def _on_started(self, device: str) -> None:
        self.state_pill.set_state("Live", "success")
        self.start_button.setText("Stop camera")
        self.status.setText(f"Streaming to {device}. Pick “{webcam.CARD_LABEL}” in your app.")

    def _on_stopped(self) -> None:
        self.state_pill.set_state("Stopped", "muted")
        self.start_button.setText("Start camera")
        self.status.setText("")

    def _on_failed(self, message: str) -> None:
        self.status.setText(message)
        self.toast.show_message("Camera error", self.palette_tokens, "danger")

    def _check_environment(self) -> None:
        problems: list[str] = []
        #: What to install, named for packages.advice rather than for the user.
        missing: list[str] = []
        if not webcam.module_installed():
            problems.append("the v4l2loopback kernel module is missing")
            missing.append("v4l2loopback")
        if self.hub.camera_uses_companion:
            if not webcam.ffmpeg_available():
                problems.append("ffmpeg is not installed")
                missing.append("ffmpeg")
        elif not webcam.scrcpy_available():
            problems.append("scrcpy is not installed (needed without the companion app)")
            missing.append("scrcpy")

        if problems:
            self.status.setText(
                ", and ".join(problems).capitalize() + ". " + packages.advice(*missing)
            )
        else:
            route = "the companion app" if self.hub.camera_uses_companion else "scrcpy over adb"
            self.status.setText(f"Ready. Video will come through {route}.")

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.toast._reposition()
