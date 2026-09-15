"""Playing the phone's audio on this computer, over the companion link."""

from __future__ import annotations

import logging
import os
from array import array

from PySide6.QtCore import QObject, Signal

from ..core.config import PhoneAudioConfig

log = logging.getLogger(__name__)

try:  # pragma: no cover - depends on the Qt build
    from PySide6.QtMultimedia import (
        QAudio,
        QAudioFormat,
        QAudioSink,
        QMediaDevices,
    )

    HAVE_QTMULTIMEDIA = True
except ImportError:  # pragma: no cover - a Qt built without multimedia
    QAudio = QAudioFormat = QAudioSink = QMediaDevices = None
    HAVE_QTMULTIMEDIA = False
    log.info("QtMultimedia is not available; the phone's audio cannot be played")


#: What the phone sends, and the only format this understands. The header names
#: it on every stream so a mismatch is caught rather than played as noise.
CODEC = "pcm_s16le"
RATE = 48_000
CHANNELS = 2
BYTES_PER_FRAME = 2 * CHANNELS        # 16-bit stereo

#: Never let the backlog grow past this: a late burst is worth catching up on,
#: a second of it is just latency the user cannot get rid of.
MAX_BACKLOG_MS = 400

#: How many samples to look at when measuring the level. Every eighth is
#: plenty for a meter and keeps the arithmetic off the hot path.
LEVEL_STRIDE = 8


_backend_ready = False


def _start_backend() -> None:
    """Load Qt's media backend without its VA-API probe.

    Only audio is used, and the probe can hang for minutes on NVIDIA drivers.
    """
    global _backend_ready
    if _backend_ready or not HAVE_QTMULTIMEDIA:
        return
    _backend_ready = True
    if os.environ.get("LIBVA_DRIVERS_PATH"):
        QMediaDevices.defaultAudioOutput()
        return
    os.environ["LIBVA_DRIVERS_PATH"] = os.devnull
    try:
        QMediaDevices.defaultAudioOutput()
    finally:
        del os.environ["LIBVA_DRIVERS_PATH"]


def available() -> bool:
    """Whether this computer can play audio at all."""
    if not HAVE_QTMULTIMEDIA:
        return False
    _start_backend()
    return QMediaDevices.defaultAudioOutput() is not None


def _bytes_for(milliseconds: int) -> int:
    return int(RATE * BYTES_PER_FRAME * milliseconds / 1000)


class PhoneAudio(QObject):
    """Plays the PCM the companion app sends."""

    started = Signal()
    stopped = Signal()
    failed = Signal(str)
    #: Peak level of the last frame, 0.0 to 1.0, for a meter in the interface.
    levelChanged = Signal(float)

    def __init__(self, config: PhoneAudioConfig, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._config = config
        self._sink: QAudioSink | None = None
        self._device = None                 # the QIODevice the sink hands back
        self._pending = bytearray()
        self._priming = True
        self._dropped = 0
        self._played = 0

    # -- state ---------------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._sink is not None

    @property
    def played_bytes(self) -> int:
        """How much audio has reached the sound card, for the interface."""
        return self._played

    @property
    def dropped_bytes(self) -> int:
        """How much was thrown away to keep the delay down."""
        return self._dropped

    # -- the stream ----------------------------------------------------------

    def open(self, header: dict) -> None:
        """Start playing, using the format the phone declared."""
        if not HAVE_QTMULTIMEDIA:
            self.failed.emit(
                "This build of Qt has no audio output, so the phone's audio "
                "cannot be played here."
            )
            return

        codec = str(header.get("codec", CODEC))
        rate = int(header.get("rate", RATE) or RATE)
        channels = int(header.get("channels", CHANNELS) or CHANNELS)
        if codec != CODEC:
            self.failed.emit(
                f"The phone is sending {codec}, which this version cannot play. "
                "Update whichever of the two is older."
            )
            return

        self.close()

        _start_backend()
        output = self._output_device()
        if output is None:
            self.failed.emit("This computer has no audio output to play through.")
            return

        audio_format = QAudioFormat()
        audio_format.setSampleRate(rate)
        audio_format.setChannelCount(channels)
        audio_format.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        if not output.isFormatSupported(audio_format):
            # Qt resamples in the backend, so this is a warning rather than a
            # refusal: the nearest supported format is still played correctly.
            log.info(
                "%s does not claim 48 kHz stereo 16-bit; letting Qt convert",
                output.description(),
            )

        sink = QAudioSink(output, audio_format, self)
        # Two jitter buffers deep, so the sink has something to chew on even
        # when a frame is late.
        sink.setBufferSize(max(_bytes_for(self._config.buffer_ms) * 2, _bytes_for(80)))
        sink.setVolume(max(0, min(100, self._config.volume)) / 100)
        sink.stateChanged.connect(self._on_state)

        device = sink.start()
        if device is None:
            self.failed.emit("The audio output would not start.")
            return

        self._sink = sink
        self._device = device
        self._pending.clear()
        self._priming = True
        self._dropped = 0
        self._played = 0
        log.info(
            "playing the phone's audio: %s %s Hz, %s channels, %s ms buffer",
            codec, rate, channels, self._config.buffer_ms,
        )
        self.started.emit()

    def feed(self, payload: bytes) -> None:
        """Take one frame from the phone."""
        if self._sink is None or self._device is None:
            return
        self._pending += payload
        self._emit_level(payload)

        # Fill the buffer before playing anything, once.
        if self._priming:
            if len(self._pending) < _bytes_for(self._config.buffer_ms):
                return
            self._priming = False

        self._trim()
        self._drain()

    def _drain(self) -> None:
        """Hand the sink as much as it will take."""
        device = self._device
        if device is None or not self._pending:
            return
        free = self._sink.bytesFree() if self._sink is not None else 0
        if free <= 0:
            return
        chunk = bytes(self._pending[:free])
        written = device.write(chunk)
        if written <= 0:
            return
        del self._pending[:written]
        self._played += written

    def _trim(self) -> None:
        """Drop the oldest audio when the backlog has grown too far."""
        cap = _bytes_for(MAX_BACKLOG_MS)
        if len(self._pending) <= cap:
            return
        excess = len(self._pending) - cap
        # Cut on a whole frame so the two channels do not swap over.
        excess -= excess % BYTES_PER_FRAME
        if excess <= 0:
            return
        del self._pending[:excess]
        self._dropped += excess
        log.debug("dropped %s bytes to keep the delay down", excess)

    def close(self) -> None:
        """Stop playing and let the output go."""
        sink, self._sink = self._sink, None
        self._device = None
        self._pending.clear()
        self._priming = True
        if sink is None:
            return
        try:
            sink.stateChanged.disconnect(self._on_state)
        except (RuntimeError, TypeError):
            pass
        sink.stop()
        sink.deleteLater()
        self.stopped.emit()

    # -- settings ------------------------------------------------------------

    def set_volume(self, percent: int) -> None:
        self._config.volume = max(0, min(100, int(percent)))
        if self._sink is not None:
            self._sink.setVolume(self._config.volume / 100)

    def _output_device(self):
        """The configured output, or the system default."""
        wanted = (self._config.device or "").strip()
        if wanted:
            for device in QMediaDevices.audioOutputs():
                if device.description() == wanted:
                    return device
            log.info("audio output %r is gone; using the default", wanted)
        default = QMediaDevices.defaultAudioOutput()
        if default is None or default.isNull():
            return None
        return default

    # -- diagnostics ---------------------------------------------------------

    def _on_state(self, state) -> None:
        if QAudio is None:
            return
        if state == QAudio.State.StoppedState and self._sink is not None:
            error = self._sink.error()
            if error not in (QAudio.Error.NoError, QAudio.Error.UnderrunError):
                self.failed.emit(f"The audio output stopped ({error.name}).")
        elif state == QAudio.State.IdleState:
            # Underrun: the network fell behind. Prime again rather than
            # dribbling single frames into a sink that keeps starving.
            self._priming = True

    def _emit_level(self, payload: bytes) -> None:
        """Peak level of *payload*, for a meter."""
        if len(payload) < 2:
            return
        samples = array("h")
        samples.frombytes(payload[: len(payload) - len(payload) % 2])
        peak = 0
        for index in range(0, len(samples), LEVEL_STRIDE):
            value = samples[index]
            if value < 0:
                value = -value
            if value > peak:
                peak = value
        self.levelChanged.emit(peak / 32768)
