"""The phone's microphone as a microphone on this computer."""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject

from ..core import packages
from ..core.config import PhoneAudioConfig
from ..core.proc import have, run
from .phone_audio import USE_PROCESS, PhoneAudio

log = logging.getLogger(__name__)

#: A null sink the phone's audio is played into, and a source remapped from
#: its monitor: that is what applications list as a microphone. Both
#: PulseAudio and pipewire-pulse know these modules.
SINK = "tessera_mic_sink"
SOURCE = "tessera_mic"
DESCRIPTION = "Phone microphone (Tessera)"


def available() -> bool:
    """Whether a virtual microphone can be made here."""
    if USE_PROCESS:
        return have("pactl") and (have("pw-cat") or have("pacat"))
    from . import phone_audio

    return phone_audio.available()


class PhoneMic(PhoneAudio):
    """Plays the phone's microphone into a virtual source, or a chosen output."""

    def __init__(self, config: PhoneAudioConfig, parent: QObject | None = None) -> None:
        super().__init__(config, parent)
        self._modules: list[str] = []

    def target(self) -> str:
        return SINK if USE_PROCESS else ""

    def stream_name(self) -> str:
        return "Phone microphone"

    def open(self, header: dict) -> None:
        if USE_PROCESS:
            problem = self._create_source()
            if problem:
                self.failed.emit(problem)
                return
        super().open(header)
        if USE_PROCESS and not self.running:
            self._destroy_source()

    def close(self) -> None:
        super().close()
        self._destroy_source()

    # -- the virtual microphone ----------------------------------------------

    def _create_source(self) -> str:
        """Make the source applications will see. Empty when it worked."""
        if not have("pactl"):
            return "pactl is needed to make a virtual microphone. " + packages.advice("pulseaudio-utils")
        self._destroy_source()
        # Quoted twice: pactl joins its arguments into one string and the
        # module parses that, so the inner quotes are what keep the spaces.
        steps = (
            ["pactl", "load-module", "module-null-sink", f"sink_name={SINK}",
             f"sink_properties=\"device.description='{DESCRIPTION} feed'\""],
            ["pactl", "load-module", "module-remap-source", f"master={SINK}.monitor",
             f"source_name={SOURCE}", "channels=1",
             f"source_properties=\"device.description='{DESCRIPTION}'\""],
        )
        for argv in steps:
            result = run(argv, timeout=10.0)
            if not result.ok:
                self._destroy_source()
                return f"Could not create the virtual microphone: {result.text.strip()[:160]}"
            self._modules.append(result.stdout.strip())
        log.info("virtual microphone %s is up", SOURCE)
        return ""

    def _destroy_source(self) -> None:
        modules, self._modules = self._modules, []
        for module in reversed(modules):
            if module.isdigit():
                run(["pactl", "unload-module", module], timeout=10.0)
