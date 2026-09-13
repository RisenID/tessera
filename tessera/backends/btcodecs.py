"""Which Bluetooth codec the phone's music arrives in."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from ..core.proc import have, run

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
    """The codecs to advertise, minus any this computer cannot decode."""
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


def session_managed_by_systemd() -> bool:
    """Whether the session manager can be restarted with systemctl --user."""
    return have("systemctl") and Path("/run/systemd/system").is_dir()


def reload_session() -> bool:
    """Restart WirePlumber so the new codec list is advertised."""
    if not session_managed_by_systemd():
        log.info("no systemd user session; the codec list applies at next start")
        return False
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
