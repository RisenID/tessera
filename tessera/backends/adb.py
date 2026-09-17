"""adb wrapper."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from ..core import packages
from ..core.proc import Result, have, run

log = logging.getLogger(__name__)

ADB = "adb"

#: `adb devices -l` lines look like:
#:   R5CWA0ABCDE  device product:x usb:1-2 model:SM_S931B device:x transport_id:3
_DEVICE_LINE = re.compile(r"^(?P<serial>\S+)\s+(?P<state>\S+)(?P<rest>.*)$")
_TAG = re.compile(r"(\w+):(\S+)")


class AdbError(RuntimeError):
    pass


class AdbUnavailable(AdbError):
    """adb is not installed."""


class NoDeviceError(AdbError):
    """No usable device is attached."""


@dataclass(frozen=True)
class AdbDevice:
    serial: str
    state: str
    model: str = ""
    product: str = ""
    transport_id: str = ""

    @property
    def wireless(self) -> bool:
        """True for `host:port` serials, i.e. connected over TCP rather than USB."""
        return ":" in self.serial and not self.serial.startswith("usb:")

    @property
    def usable(self) -> bool:
        return self.state == "device"

    @property
    def label(self) -> str:
        name = self.model.replace("_", " ") or self.serial
        return f"{name} ({'Wi-Fi' if self.wireless else 'USB'})"

    @property
    def status_text(self) -> str:
        return {
            "device": "ready",
            "unauthorized": "waiting for you to allow USB debugging on the phone",
            "offline": "offline - unplug and replug, or reconnect over Wi-Fi",
            "no": "no permission to access the USB device (check udev rules)",
        }.get(self.state, self.state)


def available() -> bool:
    return have(ADB)


def _adb(args: list[str], serial: str | None = None, timeout: float = 15.0) -> Result:
    if not available():
        raise AdbUnavailable("adb is not installed. " + packages.advice("adb"))
    argv = [ADB]
    if serial:
        argv += ["-s", serial]
    argv += args
    return run(argv, timeout=timeout)


def devices() -> list[AdbDevice]:
    """All attached devices, including unauthorized/offline ones."""
    if not available():
        return []
    result = _adb(["devices", "-l"], timeout=20.0)
    if not result.ok:
        log.warning("adb devices failed: %s", result.text)
        return []

    found: list[AdbDevice] = []
    for line in result.stdout.splitlines()[1:]:
        line = line.strip()
        if not line or line.startswith("*"):
            continue
        match = _DEVICE_LINE.match(line)
        if not match:
            continue
        tags = dict(_TAG.findall(match.group("rest")))
        found.append(
            AdbDevice(
                serial=match.group("serial"),
                state=match.group("state"),
                model=tags.get("model", ""),
                product=tags.get("product", ""),
                transport_id=tags.get("transport_id", ""),
            )
        )
    return found


def resolve_serial(preferred: str = "") -> str:
    """Pick which device to talk to."""
    attached = devices()
    if preferred:
        for dev in attached:
            if dev.serial == preferred:
                if not dev.usable:
                    raise NoDeviceError(f"{dev.serial} is {dev.status_text}")
                return dev.serial
        raise NoDeviceError(f"device {preferred} is not connected")

    usable = _deduplicate([d for d in attached if d.usable])
    if len(usable) == 1:
        return usable[0].serial
    if not usable:
        if attached:
            problem = attached[0]
            raise NoDeviceError(f"{problem.serial}: {problem.status_text}")
        raise NoDeviceError(
            "no phone connected over adb - plug in USB with USB debugging on, "
            "or pair over Wi-Fi in Settings"
        )
    raise NoDeviceError(
        "several devices are attached; pick one in Settings: "
        + ", ".join(d.serial for d in usable)
    )


def _deduplicate(devices: list[AdbDevice]) -> list[AdbDevice]:
    """Collapse entries that are the same physical phone."""
    grouped: dict[tuple[str, str, str], list[AdbDevice]] = {}
    for device in devices:
        key = (device.product, device.model, device.serial if not device.product else "")
        grouped.setdefault(key, []).append(device)

    chosen: list[AdbDevice] = []
    for entries in grouped.values():
        if len(entries) == 1:
            chosen.append(entries[0])
            continue
        # Prefer host:port over the mDNS service name.
        host_port = [d for d in entries if ":" in d.serial and "._tcp" not in d.serial]
        chosen.append(host_port[0] if host_port else entries[0])
    return chosen


def shell(serial: str, command: str, timeout: float = 15.0) -> str:
    """Run *command* in the device shell and return stdout."""
    result = _adb(["shell", command], serial=serial, timeout=timeout)
    if not result.ok:
        raise AdbError(result.text or f"adb shell failed ({result.code})")
    # Some ROMs write warnings to stderr while still succeeding.
    if result.stderr.strip():
        log.debug("adb shell stderr: %s", result.stderr.strip())
    return result.stdout.strip()


def try_shell(serial: str, command: str, timeout: float = 15.0) -> tuple[bool, str]:
    """Like :func:`shell` but returns success instead of raising."""
    try:
        return True, shell(serial, command, timeout=timeout)
    except AdbError as exc:
        return False, str(exc)


def getprop(serial: str, name: str) -> str:
    try:
        return shell(serial, f"getprop {name}", timeout=8.0)
    except AdbError:
        return ""


# -- wireless adb ------------------------------------------------------------


def wifi_ip(serial: str) -> str:
    """The phone's current IPv4 address on the network it routes through, or ''."""
    # Whatever interface carries the default route, by whatever name this ROM gives it.
    ok, out = try_shell(serial, "ip -4 route get 1.1.1.1")
    if ok:
        match = re.search(r"\bsrc (\d+\.\d+\.\d+\.\d+)", out)
        if match:
            return match.group(1)
    # No route (no internet): any address the phone has.
    ok, out = try_shell(serial, "ip -4 -o addr show scope global")
    if ok:
        match = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", out)
        if match:
            return match.group(1)
    return ""


def enable_tcpip(serial: str, port: int = 5555) -> None:
    """Switch a USB-attached device to listening for adb over TCP."""
    result = _adb(["tcpip", str(port)], serial=serial, timeout=20.0)
    if not result.ok:
        raise AdbError(result.text or "could not enable adb over TCP")


def connect(host_port: str) -> str:
    """Connect to a device at `host:port`, returning adb's message."""
    result = _adb(["connect", host_port], timeout=25.0)
    text = result.text
    # adb exits 0 even when it failed to connect, so inspect the message.
    if not result.ok or "unable to connect" in text.lower() or "failed" in text.lower():
        raise AdbError(text or f"could not connect to {host_port}")
    return text


#: One line per service: "name  _adb-tls-connect._tcp  192.168.1.5:37589".
_MDNS_ROW = re.compile(r"^(\S+)\s+(\S+)\s+(\S+:\d+)\s*$")

#: What a phone advertises once wireless debugging is on. The -pairing- one is
#: for the pairing step, which needs a code and is not something to guess at.
MDNS_CONNECT = "_adb-tls-connect._tcp"


def mdns_targets() -> list[str]:
    """Phones advertising wireless debugging on this network, as host:port."""
    result = _adb(["mdns", "services"], timeout=12.0)
    if not result.ok:
        return []
    found = []
    for line in result.stdout.splitlines():
        match = _MDNS_ROW.match(line.strip())
        if match and match.group(2) == MDNS_CONNECT:
            found.append(match.group(3))
    return found


def pair(host_port: str, code: str) -> str:
    """Pair with Android 11+ wireless debugging using a 6-digit code."""
    result = run([ADB, "pair", host_port], timeout=40.0, stdin=f"{code}\n")
    text = result.text
    if not result.ok or "failed" in text.lower():
        raise AdbError(text or f"pairing with {host_port} failed")
    return text


def disconnect(host_port: str = "") -> None:
    _adb(["disconnect", host_port] if host_port else ["disconnect"], timeout=10.0)


# -- content provider queries ------------------------------------------------

#: `content query` prints one "Row: N key=value, key=value" line per record.
_ROW = re.compile(r"^Row:\s*\d+\s+(.*)$")


def parse_content_rows(output: str) -> list[dict[str, str]]:
    """Parse `adb shell content query` output into dictionaries."""
    rows: list[dict[str, str]] = []
    for line in output.splitlines():
        match = _ROW.match(line.strip())
        if not match:
            continue
        body = match.group(1)
        # Split only where a new `key=` actually begins.
        parts = re.split(r",\s+(?=[A-Za-z_][A-Za-z0-9_]*=)", body)
        row: dict[str, str] = {}
        for part in parts:
            key, sep, value = part.partition("=")
            if sep:
                row[key.strip()] = value.strip()
        if row:
            rows.append(row)
    return rows


def content_query(
    serial: str,
    uri: str,
    projection: list[str] | None = None,
    sort: str = "",
    where: str = "",
    limit: int = 0,
    timeout: float = 30.0,
) -> list[dict[str, str]]:
    """Query an Android content provider."""
    def build(with_sort: bool) -> str:
        parts = [f"content query --uri {uri}"]
        if projection:
            parts.append("--projection " + ":".join(projection))
        if where:
            parts.append(f'--where "{where}"')
        if sort and with_sort:
            order = f"{sort} LIMIT {limit}" if limit else sort
            parts.append(f'--sort "{order}"')
        return " ".join(parts)

    ok, out = try_shell(serial, build(True), timeout=timeout)
    if not ok or "error" in out.lower()[:200]:
        ok, out = try_shell(serial, build(False), timeout=timeout)
        if not ok:
            raise AdbError(out)
    if "no result found" in out.lower():
        return []
    rows = parse_content_rows(out)
    return rows[:limit] if limit else rows
