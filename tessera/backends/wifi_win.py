"""Joining a Wi-Fi network on Windows, through netsh."""

from __future__ import annotations

import logging
import tempfile
import time
from pathlib import Path
from xml.sax.saxutils import escape

from ..core.proc import have, run

log = logging.getLogger(__name__)

NETSH = "netsh"

#: A WPA2-PSK profile, the one shape an Android hotspot needs.
_PROFILE = """<?xml version="1.0"?>
<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">
  <name>{ssid}</name>
  <SSIDConfig>
    <SSID>
      <name>{ssid}</name>
    </SSID>
  </SSIDConfig>
  <connectionType>ESS</connectionType>
  <connectionMode>manual</connectionMode>
  <MSM>
    <security>
      <authEncryption>
        <authentication>{authentication}</authentication>
        <encryption>{encryption}</encryption>
        <useOneX>false</useOneX>
      </authEncryption>
      {key}
    </security>
  </MSM>
</WLANProfile>
"""

_KEY = """<sharedKey>
        <keyType>passPhrase</keyType>
        <protected>false</protected>
        <keyMaterial>{passphrase}</keyMaterial>
      </sharedKey>"""


def available() -> bool:
    return have(NETSH)


def _wlan(*args: str, timeout: float = 20.0):
    return run([NETSH, "wlan", *args], timeout=timeout)


def active_ssid() -> str:
    """SSID of the network currently joined, or ''."""
    result = _wlan("show", "interfaces")
    if not result.ok:
        return ""
    connected = False
    for raw in result.stdout.splitlines():
        line = raw.strip()
        key, _, value = line.partition(":")
        key, value = key.strip().lower(), value.strip()
        if key == "state":
            connected = value.lower() == "connected"
        elif key == "ssid" and connected and value:
            return value
    return ""


def radio_on() -> bool:
    """Whether any wireless adapter is up."""
    result = _wlan("show", "interfaces")
    if not result.ok:
        return False
    lowered = result.stdout.lower()
    if "radio status" in lowered and "off" in lowered.split("radio status", 1)[1][:40]:
        return False
    return "state" in lowered


def profiles() -> list[str]:
    """Every saved network profile, by name."""
    result = _wlan("show", "profiles")
    if not result.ok:
        return []
    names = []
    for raw in result.stdout.splitlines():
        if ":" not in raw or "profile" not in raw.lower():
            continue
        name = raw.split(":", 1)[1].strip()
        if name and not name.lower().startswith("all user"):
            names.append(name)
    return names


def has_profile(ssid: str) -> bool:
    return ssid in profiles()


def scan_for(ssid: str, timeout: float = 30.0) -> bool:
    """Wait for *ssid* to appear in a scan."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = _wlan("show", "networks", "mode=bssid")
        if result.ok and _mentions(result.stdout, ssid):
            return True
        time.sleep(2.0)
    return False


def _mentions(output: str, ssid: str) -> bool:
    for raw in output.splitlines():
        line = raw.strip()
        if not line.lower().startswith("ssid"):
            continue
        _, _, value = line.partition(":")
        if value.strip() == ssid:
            return True
    return False


def add_profile(ssid: str, passphrase: str) -> bool:
    """Teach Windows the network. True when the profile was accepted."""
    body = _PROFILE.format(
        ssid=escape(ssid),
        authentication="WPA2PSK" if passphrase else "open",
        encryption="AES" if passphrase else "none",
        key=_KEY.format(passphrase=escape(passphrase)) if passphrase else "",
    )
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".xml", encoding="utf-8", delete=False
    )
    try:
        handle.write(body)
        handle.close()
        result = _wlan("add", "profile", f"filename={handle.name}", "user=current")
        if not result.ok:
            log.warning("netsh refused the profile: %s", result.text)
        return result.ok
    finally:
        # The passphrase was in that file. Do not leave it lying about.
        Path(handle.name).unlink(missing_ok=True)


def connect(ssid: str, passphrase: str, scan_timeout: float = 30.0) -> str:
    """Join *ssid*, adding a profile first when Windows has none."""
    if not available():
        raise RuntimeError("netsh is not available, so Wi-Fi cannot be joined.")
    if active_ssid() == ssid:
        return f"Already connected to {ssid}."
    if not scan_for(ssid, scan_timeout):
        raise RuntimeError(
            f"'{ssid}' did not appear in a Wi-Fi scan within {scan_timeout:g}s.\n"
            "Check that the hotspot is on and that the network name matches exactly."
        )
    if not has_profile(ssid) and not add_profile(ssid, passphrase):
        raise RuntimeError(
            f"Windows would not accept a profile for {ssid}. Join it once from "
            "the taskbar and Tessera can use it from then on."
        )

    result = _wlan("connect", f"name={ssid}", f"ssid={ssid}", timeout=60.0)
    if not result.ok:
        raise RuntimeError(f"Could not connect to {ssid}: {result.text}")

    # netsh returns as soon as the request is queued, so the connection is not
    # up yet. Wait for it rather than reporting success it has not earned.
    deadline = time.monotonic() + 25.0
    while time.monotonic() < deadline:
        if active_ssid() == ssid:
            return f"Connected to {ssid}."
        time.sleep(1.0)
    raise RuntimeError(
        f"Windows accepted the request but did not join {ssid} within 25s."
    )


def disconnect(ssid: str = "") -> None:
    """Leave the current network. Windows disconnects the interface, not an SSID."""
    if ssid and active_ssid() != ssid:
        return
    _wlan("disconnect")


def addresses() -> list[str]:
    """Every IPv4 address this computer holds, for finding the phone again."""
    result = run([NETSH, "interface", "ipv4", "show", "addresses"], timeout=20.0)
    found: list[str] = []
    for raw in result.stdout.splitlines():
        line = raw.strip()
        if not line.lower().startswith("ip address"):
            continue
        _, _, value = line.partition(":")
        value = value.strip()
        if value and value != "0.0.0.0":
            found.append(value)
    return found
