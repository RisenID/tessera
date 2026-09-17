"""This computer's saved Wi-Fi networks, for handing one to the phone."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from ..core import platform
from ..core.proc import have, run

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Network:
    name: str                  # the connection's name here
    ssid: str = ""
    security: str = ""         # open | wpa2 | wpa3 | wep | enterprise
    password: str = ""
    active: bool = False

    @property
    def shareable(self) -> bool:
        return bool(self.ssid) and self.security in ("open", "wpa2", "wpa3")


def available() -> bool:
    return have("netsh") if platform.IS_WINDOWS else have("nmcli")


def networks() -> list[Network]:
    """Saved networks, the active one first. No passwords yet."""
    if platform.IS_WINDOWS:
        return _windows_networks()
    result = run(["nmcli", "-t", "-f", "NAME,TYPE,ACTIVE", "connection", "show"], timeout=10.0)
    if not result.ok:
        return []
    found = []
    for line in result.stdout.splitlines():
        parts = line.split(":")
        if len(parts) < 3 or parts[1] != "802-11-wireless":
            continue
        found.append(Network(name=parts[0], active=parts[2] == "yes"))
    return sorted(found, key=lambda n: (not n.active, n.name.lower()))


def credentials(name: str) -> Network:
    """The named network with its password. Raises RuntimeError when it cannot be read."""
    if platform.IS_WINDOWS:
        return _windows_credentials(name)
    result = run([
        "nmcli", "-s", "-t", "-f",
        "802-11-wireless.ssid,802-11-wireless-security.key-mgmt,802-11-wireless-security.psk,"
        "802-11-wireless-security.wep-key0",
        "connection", "show", name,
    ], timeout=15.0)
    if not result.ok:
        raise RuntimeError(f"NetworkManager would not show {name}: {result.text.strip()[:160]}")
    fields = {}
    for line in result.stdout.splitlines():
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    management = fields.get("802-11-wireless-security.key-mgmt", "")
    security = {
        "": "open", "none": "wep", "wpa-psk": "wpa2", "sae": "wpa3",
    }.get(management, "enterprise")
    password = fields.get("802-11-wireless-security.psk", "") or fields.get(
        "802-11-wireless-security.wep-key0", "")
    if security in ("wpa2", "wpa3") and not password:
        raise RuntimeError(f"The password for {name} is not readable by this user.")
    return Network(name=name, ssid=fields.get("802-11-wireless.ssid", "") or name,
                   security=security, password=password)


# -- Windows: netsh --------------------------------------------------------------


def _windows_networks() -> list[Network]:
    from . import wifi_win

    active = wifi_win.active_ssid()
    return sorted(
        (Network(name=name, ssid=name, active=name == active) for name in wifi_win.profiles()),
        key=lambda n: (not n.active, n.name.lower()),
    )


def _windows_credentials(name: str) -> Network:
    result = run(["netsh", "wlan", "show", "profile", f"name={name}", "key=clear"],
                 timeout=15.0, encoding="oem" if platform.REAL == "windows" else "utf-8")
    if not result.ok:
        raise RuntimeError(f"netsh would not show {name}: {result.text.strip()[:160]}")
    return parse_netsh_profile(name, result.stdout)


def parse_netsh_profile(name: str, output: str) -> Network:
    """netsh's profile listing, in whatever language, by the shape of its lines."""
    ssid = name
    authentication = ""
    password = ""
    for raw in output.splitlines():
        if " : " not in raw:
            continue
        label, _, value = raw.partition(" : ")
        label, value = label.strip().lower(), value.strip()
        if label.startswith("ssid") and value.startswith('"'):
            ssid = value.strip('"')
        elif "auth" in label and not authentication:
            authentication = value.lower()
        elif label in ("key content", "schlüsselinhalt", "contenu de la clé") or label.startswith("key content"):
            password = value
    lowered = authentication
    if "wpa3" in lowered:
        security = "wpa3"
    elif "wpa" in lowered:
        security = "wpa2"
    elif "wep" in lowered:
        security = "wep"
    elif lowered in ("open", "") and not password:
        security = "open"
    else:
        security = "enterprise" if "802.1x" in lowered or "enterprise" in lowered else "wpa2"
    if security in ("wpa2", "wpa3") and not password:
        raise RuntimeError(f"Windows would not show the password for {name}.")
    return Network(name=name, ssid=ssid, security=security, password=password)


def describe(network: Network) -> str:
    """A line for the interface."""
    kind = {"open": "open", "wpa2": "WPA2", "wpa3": "WPA3", "wep": "WEP",
            "enterprise": "enterprise"}.get(network.security, network.security)
    return re.sub(r"\s+", " ", f"{network.ssid or network.name} ({kind})").strip()
