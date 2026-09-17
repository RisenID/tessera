"""One-click phone hotspot."""

from __future__ import annotations

import logging
import re
import shlex
import time
from dataclasses import dataclass

from ..core import platform
from ..core.config import HotspotConfig
from ..core.proc import have, run
from . import adb, wifi_win

log = logging.getLogger(__name__)

NMCLI = "nmcli"


class HotspotError(RuntimeError):
    pass


@dataclass(frozen=True)
class ApConfig:
    ssid: str
    passphrase: str = ""

    @property
    def valid(self) -> bool:
        return bool(self.ssid)


# -- phone side --------------------------------------------------------------


def read_ap_config(serial: str) -> ApConfig:
    """Best-effort read of the phone's saved hotspot SSID and passphrase."""
    ok, out = adb.try_shell(serial, "cmd wifi get-softap-config", timeout=10.0)
    if ok and out:
        ssid = _first(r"SSID\s*[:=]\s*\"?([^\"\n,]+)", out)
        passphrase = _first(r"(?:passphrase|preSharedKey)\s*[:=]\s*\"?([^\"\n,]+)", out)
        if ssid:
            return ApConfig(ssid.strip(), (passphrase or "").strip())

    ok, out = adb.try_shell(serial, "dumpsys wifi | grep -i -m5 softap", timeout=15.0)
    if ok and out:
        ssid = _first(r"SSID\s*[:=]\s*\"?([^\"\n,]+)", out)
        if ssid:
            return ApConfig(ssid.strip(), "")
    return ApConfig("", "")


def _first(pattern: str, text: str) -> str:
    match = re.search(pattern, text, re.IGNORECASE)
    return match.group(1) if match else ""


def tethering_state(serial: str) -> bool | None:
    """True/False if the hotspot state can be read, None when it cannot."""
    ok, out = adb.try_shell(serial, "cmd wifi is-softap-enabled", timeout=10.0)
    if ok and out:
        lowered = out.lower()
        if "enabled" in lowered or "true" in lowered:
            return "not" not in lowered and "disabled" not in lowered
        if "disabled" in lowered or "false" in lowered:
            return False

    ok, out = adb.try_shell(
        serial, "dumpsys wifi | grep -i -m3 'ap state\\|softap state'", timeout=15.0
    )
    if ok and out:
        upper = out.upper()
        if "AP_STATE_ENABLED" in upper or "WIFI_AP_STATE_ENABLED" in upper:
            return True
        if "AP_STATE_DISABLED" in upper or "WIFI_AP_STATE_DISABLED" in upper:
            return False
    return None


def start_phone_hotspot(serial: str, config: HotspotConfig) -> ApConfig:
    """Turn the phone's Wi-Fi hotspot on and report the network to join."""
    band = "-b 5" if config.band == "5" else "-b 2"
    attempts: list[tuple[str, str]] = []

    if config.ssid and len(config.passphrase) >= 8:
        # `-w` waits for the AP to finish coming up rather than returning early.
        attempts.append(
            (
                "start-softap with your settings",
                f"cmd -w wifi start-softap {shlex.quote(config.ssid)} wpa2 "
                f"{shlex.quote(config.passphrase)} {band}",
            )
        )
    attempts.append(("start the saved hotspot", "cmd -w wifi start-softap"))
    attempts.append(("legacy tethering command", "svc wifi enable-softap"))

    problems = []
    for label, command in attempts:
        ok, out = adb.try_shell(serial, command, timeout=45.0)
        lowered = out.lower()
        if ok and not any(
            bad in lowered
            for bad in ("error", "exception", "failed", "unknown command", "permission deni")
        ):
            log.info("hotspot started (%s)", label)
            break
        problems.append(f"{label}: {out.strip() or 'no output'}")
    else:
        raise HotspotError(
            "Could not turn the hotspot on from the computer. Samsung's One UI "
            "often blocks this.\n\nUse USB tethering instead (change the method "
            "in Settings), or switch the hotspot on from the phone.\n\nTried:\n- "
            + "\n- ".join(problems)
        )

    if config.ssid and config.passphrase:
        return ApConfig(config.ssid, config.passphrase)
    detected = read_ap_config(serial)
    if not detected.valid:
        raise HotspotError(
            "The hotspot is on, but the phone would not tell us its network name. "
            "Enter the SSID and password in Settings so the computer can connect "
            "automatically."
        )
    return detected


def stop_phone_hotspot(serial: str) -> None:
    for command in ("cmd -w wifi stop-softap", "svc wifi disable-softap"):
        ok, out = adb.try_shell(serial, command, timeout=30.0)
        if ok and "error" not in out.lower() and "unknown command" not in out.lower():
            return
    raise HotspotError("Could not turn the hotspot off; do it from the phone.")


def set_usb_tethering(serial: str, enabled: bool) -> None:
    """Toggle USB tethering, the fallback when Wi-Fi tethering is blocked."""
    if not enabled:
        adb.try_shell(serial, "svc usb setFunctions", timeout=20.0)
        return

    problems = []
    for function in ("ncm,adb", "rndis,adb", "ncm", "rndis"):
        ok, out = adb.try_shell(serial, f"svc usb setFunctions {function}", timeout=25.0)
        if ok and "error" not in out.lower():
            log.info("USB tethering enabled via %s", function)
            return
        problems.append(f"{function}: {out.strip() or 'no output'}")
    raise HotspotError(
        "Could not start USB tethering.\nTried:\n- " + "\n- ".join(problems)
    )


# -- laptop side -------------------------------------------------------------


def nm_available() -> bool:
    """Whether this computer can join a network from here at all."""
    if platform.IS_WINDOWS:
        return wifi_win.available()
    return have(NMCLI)


def wifi_radio_on() -> bool:
    result = run([NMCLI, "radio", "wifi"], timeout=10.0)
    return result.ok and "enabled" in result.stdout.lower()


def enable_wifi_radio() -> None:
    run([NMCLI, "radio", "wifi", "on"], timeout=15.0)


def active_ssid() -> str:
    """SSID of the Wi-Fi network currently joined, or ''."""
    if platform.IS_WINDOWS:
        return wifi_win.active_ssid()
    result = run([NMCLI, "-t", "-f", "ACTIVE,SSID", "device", "wifi"], timeout=15.0)
    if not result.ok:
        return ""
    for line in result.stdout.splitlines():
        active, _, ssid = line.partition(":")
        if active == "yes":
            return ssid
    return ""


def scan_for(ssid: str, timeout: float = 30.0) -> bool:
    """Rescan until *ssid* is visible or *timeout* elapses."""
    if platform.IS_WINDOWS:
        return wifi_win.scan_for(ssid, timeout)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run([NMCLI, "device", "wifi", "rescan"], timeout=20.0)
        result = run([NMCLI, "-t", "-f", "SSID", "device", "wifi", "list"], timeout=20.0)
        if result.ok and any(line.strip() == ssid for line in result.stdout.splitlines()):
            return True
        time.sleep(2.0)
    return False


def has_saved_connection(ssid: str) -> bool:
    if platform.IS_WINDOWS:
        return wifi_win.has_profile(ssid)
    result = run([NMCLI, "-t", "-f", "NAME", "connection", "show"], timeout=15.0)
    return result.ok and any(line.strip() == ssid for line in result.stdout.splitlines())


def connect_wifi(ssid: str, passphrase: str, scan_timeout: float = 30.0) -> str:
    """Join *ssid*, reusing a saved profile when one exists."""
    if platform.IS_WINDOWS:
        try:
            return wifi_win.connect(ssid, passphrase, scan_timeout)
        except RuntimeError as exc:
            raise HotspotError(str(exc)) from exc
    if not nm_available():
        raise HotspotError("NetworkManager (nmcli) is not available.")
    if not wifi_radio_on():
        enable_wifi_radio()

    if active_ssid() == ssid:
        return f"Already connected to {ssid}."

    if not scan_for(ssid, scan_timeout):
        raise HotspotError(
            f"'{ssid}' did not appear in a Wi-Fi scan within {scan_timeout:g}s.\n"
            "Check that the hotspot is on and that the network name matches exactly."
        )

    if has_saved_connection(ssid):
        result = run([NMCLI, "connection", "up", ssid], timeout=60.0)
        if result.ok:
            return f"Connected to {ssid}."
        log.debug("saved profile failed (%s); retrying with the password", result.text)

    argv = [NMCLI, "device", "wifi", "connect", ssid]
    if passphrase:
        argv += ["password", passphrase]
    result = run(argv, timeout=90.0)
    if not result.ok:
        raise HotspotError(f"Could not connect to {ssid}: {result.text}")
    return f"Connected to {ssid}."


def disconnect_wifi(ssid: str) -> None:
    if platform.IS_WINDOWS:
        wifi_win.disconnect(ssid)
        return
    if ssid and has_saved_connection(ssid):
        run([NMCLI, "connection", "down", ssid], timeout=30.0)


# -- finding the phone again -------------------------------------------------


def phone_addresses(serial: str) -> list[str]:
    """The phone's own addresses, read over adb."""
    ok, out = adb.try_shell(serial, "ip -4 -o addr show", timeout=10.0)
    if not ok or not out:
        return []

    tether, other = [], []
    for line in out.splitlines():
        # 23: ap0    inet 192.168.43.1/24 brd ... scope global ap0
        match = re.search(r"^\d+:\s*(\S+)\s+inet\s+(\d+\.\d+\.\d+\.\d+)", line.strip())
        if not match:
            continue
        name, address = match.group(1), match.group(2)
        if name.startswith("lo"):
            continue
        (tether if _looks_like_tether(name) else other).append(address)
    return list(dict.fromkeys(tether + other))


def _looks_like_tether(name: str) -> bool:
    return name.startswith(("ap", "swlan", "wlan1", "rndis", "usb", "bt-pan"))


def reachable_address(addresses: "list[str]", port: int, timeout: float = 1.5,
                      attempts: int = 8, gap: float = 1.0) -> str:
    """The first of *addresses* that answers on *port*."""
    import socket
    from concurrent.futures import ThreadPoolExecutor

    candidates = [a for a in dict.fromkeys(addresses) if a]
    if not candidates:
        return ""

    def answers(address: str) -> str:
        try:
            with socket.create_connection((address, port), timeout=timeout):
                return address
        except OSError:
            return ""

    for attempt in range(attempts):
        with ThreadPoolExecutor(max_workers=min(8, len(candidates))) as pool:
            for found in pool.map(answers, candidates):
                if found:
                    log.info("phone answered on %s:%s", found, port)
                    return found
        if attempt + 1 < attempts:
            time.sleep(gap)
    log.info("none of %s answered on port %s", ", ".join(candidates), port)
    return ""


def usb_tether_interface_up(timeout: float = 25.0) -> str:
    """Wait for a USB tethering interface to get an address; return its name."""
    if platform.IS_WINDOWS:
        # Windows names the RNDIS adapter itself and brings it up without being
        # asked; there is nothing to wait for beyond an address appearing that
        # was not there before. (Not a fixed subnet: ROMs choose their own.)
        before = set(wifi_win.addresses())
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            fresh = [a for a in wifi_win.addresses()
                     if a not in before and not a.startswith("169.254.")]
            if fresh:
                return "USB tethering"
            time.sleep(1.5)
        raise HotspotError(
            "USB tethering was switched on, but Windows did not get an address "
            "from the phone. Check that the cable carries data."
        )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = run(
            [NMCLI, "-t", "-f", "DEVICE,TYPE,STATE", "device", "status"], timeout=15.0
        )
        for line in result.stdout.splitlines():
            parts = line.split(":")
            if len(parts) >= 3 and parts[1] == "ethernet" and parts[2] == "connected":
                if parts[0].startswith(("enp", "usb", "ncm", "rndis", "enx")):
                    return parts[0]
        time.sleep(1.5)
    raise HotspotError(
        "USB tethering was switched on, but no network interface came up. "
        "Check that the USB cable supports data, and that tethering is allowed."
    )
