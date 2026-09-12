"""Which Bluetooth codec the phone's music arrives in.

Receiving audio is not the mirror image of sending it. A codec needs an
encoder to send and a decoder to receive, and the two are shipped separately:

    Endpoint registered: /MediaEndpoint/A2DPSource/ldac      <- can be sent
    Endpoint registered: /MediaEndpoint/A2DPSink/aptx_hd     <- can be received
    Endpoint registered: /MediaEndpoint/A2DPSink/aptx        <- can be received
    Endpoint registered: /MediaEndpoint/A2DPSink/aac         <- can be received
    Endpoint registered: /MediaEndpoint/A2DPSink/sbc         <- can be received

LDAC is the odd one out as distributions ship it. Sony released the encoder
(libldacBT_enc) and no decoder, so stock PipeWire can drive LDAC headphones
and cannot accept LDAC from a phone -- and LDAC is the only codec a Galaxy
actually offers above aptX. Of the rest, aptX HD looks best on paper at 576
kbit/s, but a Galaxy S25 does not offer it: asked for aptX HD alone it
negotiates plain SBC, which is worse than the aptX it would otherwise have
used. That is the whole reason the default offers everything and lets the
phone choose, and why the Audio page reports the codec that was negotiated
rather than the one that was asked for.

scripts/build-ldac-decoder.sh closes the gap. PipeWire's LDAC plugin already
contains a complete decode path, compiled out for want of a library providing
ldacBT_decode(); the script supplies one from libldacdec and rebuilds that one
plugin. When it has been run, A2DPSink/ldac appears and LDAC becomes the best
option by a wide margin -- 909 kbit/s at up to 96 kHz. Everything here reads
that state rather than assuming it, so the app behaves correctly on a machine
where the script was never run.

SBC is always offered whatever the preference. It is the only codec A2DP
requires every device to implement, so dropping it risks a phone that connects
and then has nothing to speak.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from ..core.proc import run

log = logging.getLogger(__name__)

#: Where scripts/build-ldac-decoder.sh puts its work. Nothing here writes to
#: it; the app only needs to know whether it happened.
DECODER_PREFIX = Path(
    os.environ.get("TESSERA_LDAC_PREFIX", str(Path.home() / ".local/lib64/tessera"))
)
DECODER_PLUGIN = DECODER_PREFIX / "spa-0.2" / "bluez5" / "libspa-codec-bluez5-ldac.so"


def ldac_receivable() -> bool:
    """Whether this computer can decode LDAC sent to it."""
    return DECODER_PLUGIN.is_file() and (DECODER_PREFIX / "libldacBT_dec.so.2").is_file()


#: Codec sets by preference, in the order they are offered to the phone.
#: "auto" offers everything and lets the phone choose, which is what its own
#: Developer options setting then decides.
CHOICES: dict[str, tuple[str, ...]] = {
    "auto": ("ldac", "aptx_hd", "aptx", "aac", "sbc_xq", "sbc"),
    "ldac": ("ldac", "sbc"),
    "aptx_hd": ("aptx_hd", "sbc"),
    "aptx": ("aptx", "sbc"),
    "aac": ("aac", "sbc"),
    "sbc_xq": ("sbc_xq", "sbc"),
}

LABELS: dict[str, str] = {
    "auto": "Best the phone offers (recommended)",
    "ldac": "LDAC only",
    "aptx_hd": "aptX HD only",
    "aptx": "aptX only",
    "aac": "AAC only",
    "sbc_xq": "SBC XQ only",
}

#: Rough bit rates, for saying what a choice is worth rather than just naming
#: it. SBC's figure is the usual 328 kbit/s, not the XQ variant's.
BITRATES: dict[str, int] = {
    "LDAC": 909,
    "aptX HD": 576,
    "SBC XQ": 452,
    "aptX": 352,
    "SBC": 328,
    "AAC": 256,
}

#: Written where a user drop-in overrides the file the package installs.
CONFIG = Path(
    os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
) / "wireplumber" / "wireplumber.conf.d" / "51-tessera-bluez.conf"

TEMPLATE = """# Tessera: let this computer receive audio from a phone.
#
# Written by Tessera from the Bluetooth audio quality setting. Edits are
# overwritten when that setting is saved.
#
# The roles that matter here are the reverse of driving headphones:
#
#   a2dp_source  the remote device is the source, we are the sink  (music)
#   hfp_hf       we are the hands-free unit, the phone is the gateway (calls)
#
monitor.bluez.properties = {{
  bluez5.roles = [ a2dp_sink a2dp_source bap_sink bap_source hfp_hf hfp_ag hsp_hs hsp_ag ]
  bluez5.codecs = [ {codecs} ]
  bluez5.enable-sbc-xq = true
  bluez5.hfphsp-backend = native
}}
"""


#: Best first. Only codecs that get an A2DP endpoint of their own appear here;
#: SBC XQ shares SBC's, so it is a modifier on SBC rather than a choice.
QUALITY_ORDER: tuple[str, ...] = ("ldac", "aptx_hd", "aptx", "aac", "sbc")


def best_shared(phone_codecs: "tuple[str, ...] | list[str]") -> str:
    """The best codec both the phone and this computer can manage."""
    for codec in QUALITY_ORDER:
        if codec == "ldac" and not ldac_receivable():
            continue
        if codec in phone_codecs:
            return codec
    return ""


def codecs_for(choice: str, phone_codecs: "tuple[str, ...] | list[str]" = ()) -> tuple[str, ...]:
    """The codecs to advertise, minus any this computer cannot decode.

    Offering LDAC without the decoder installed is not merely useless: the
    phone would negotiate it and then send audio nothing here can turn back
    into sound.

    "Best the phone offers" narrows the list to one codec rather than handing
    over everything, because a phone offered everything does not pick the best
    one -- it picks whatever its own ranking prefers, and an S25 given LDAC,
    aptX and the rest settles on aptX every time. Given LDAC and SBC it takes
    LDAC. So the choice has to be made on this side, and it can be made safely
    only once the phone has said what it supports: narrowing blind is how
    asking for aptX HD ended in plain SBC. Until then the wide list stands.
    """
    if choice == "auto" and phone_codecs:
        best = best_shared(phone_codecs)
        # SBC is the floor either way, so there is nothing to narrow to.
        if best and best != "sbc":
            return (best, "sbc_xq", "sbc")

    codecs = CHOICES.get(choice, CHOICES["auto"])
    if not ldac_receivable():
        codecs = tuple(c for c in codecs if c != "ldac")
    return codecs or ("sbc",)


def write_preference(choice: str, phone_codecs: "tuple[str, ...] | list[str]" = ()) -> bool:
    """Offer the phone the chosen codecs. True when the file changed."""
    wanted = TEMPLATE.format(codecs=" ".join(codecs_for(choice, phone_codecs)))
    try:
        current = CONFIG.read_text()
    except OSError:
        current = ""
    if current == wanted:
        return False
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(wanted)
    return True


def reload_session() -> bool:
    """Restart WirePlumber so the new codec list is advertised.

    The codecs are advertised to BlueZ once, when the session manager starts,
    so a change only takes effect after this. Audio on this computer stops for
    about a second; anything playing resumes on its own.
    """
    result = run(["systemctl", "--user", "restart", "wireplumber"], timeout=25.0)
    if not result.ok:
        log.warning("could not restart wireplumber: %s", result.text)
    return result.ok


#: Names as PipeWire reports them, mapped to something worth reading.
CODEC_NAMES = {
    "aptx_hd": "aptX HD",
    "aptx": "aptX",
    "aac": "AAC",
    "sbc_xq": "SBC XQ",
    "sbc": "SBC",
    "ldac": "LDAC",
    "opus_05": "Opus",
}
