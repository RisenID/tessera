"""Client for the Tessera companion app."""

from __future__ import annotations

import base64
import json
import logging
import re
import secrets
import struct
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QByteArray, QObject, QTimer, Signal
from PySide6.QtNetwork import QAbstractSocket, QSslCertificate, QSslSocket

from ..core.proc import have, run, submit

log = logging.getLogger(__name__)

PROTOCOL_VERSION = 1
DEFAULT_PORT = 8765
SERVICE_TYPE = "_tessera._tcp"

TYPE_JSON = 1
TYPE_BINARY = 2

_HEADER = struct.Struct(">IB")
#: Refuse absurd frames rather than allocating whatever the peer claims.
MAX_FRAME = 32 * 1024 * 1024


class ProtocolError(RuntimeError):
    pass


# -- framing -----------------------------------------------------------------


def encode(kind: int, payload: bytes) -> bytes:
    if len(payload) + 1 > MAX_FRAME:
        raise ProtocolError("frame too large to send")
    return _HEADER.pack(len(payload) + 1, kind) + payload


def encode_json(message: dict[str, Any]) -> bytes:
    return encode(TYPE_JSON, json.dumps(message, separators=(",", ":")).encode("utf-8"))


def encode_binary(payload: bytes) -> bytes:
    return encode(TYPE_BINARY, payload)


class Decoder:
    """Incremental frame decoder for a byte stream."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, chunk: bytes) -> Iterator[tuple[int, bytes]]:
        """Add bytes and yield every complete frame they produced."""
        self._buffer += chunk
        while True:
            if len(self._buffer) < _HEADER.size:
                return
            length, kind = _HEADER.unpack_from(self._buffer, 0)
            if length < 1:
                raise ProtocolError("frame with no type byte")
            if length > MAX_FRAME:
                raise ProtocolError(f"frame of {length} bytes exceeds the limit")
            total = _HEADER.size + length - 1
            if len(self._buffer) < total:
                return
            payload = bytes(self._buffer[_HEADER.size : total])
            del self._buffer[:total]
            yield kind, payload


def decode_json(payload: bytes) -> dict[str, Any]:
    try:
        message = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ProtocolError(f"malformed JSON frame: {exc}") from None
    if not isinstance(message, dict):
        raise ProtocolError("JSON frame was not an object")
    return message


# -- discovery ---------------------------------------------------------------


@dataclass(frozen=True)
class Discovered:
    name: str
    host: str
    port: int
    device_id: str = ""
    model: str = ""

    @property
    def label(self) -> str:
        return f"{self.name or self.host} ({self.host}:{self.port})"


def discover(timeout: float = 4.0) -> list[Discovered]:
    """Find companion apps on the local network via mDNS."""
    if not have("avahi-browse"):
        return []
    result = run(
        ["avahi-browse", "-rptk", SERVICE_TYPE], timeout=max(timeout, 2.0) + 4.0
    )
    if not result.ok:
        log.debug("avahi-browse failed: %s", result.text)
        return []

    found: dict[str, Discovered] = {}
    for line in result.stdout.splitlines():
        # Resolved records look like:
        # =;wlp0s20f3;IPv4;Galaxy\032S25;_tessera._tcp;local;host;192.168.1.5;8765;"id=..."
        if not line.startswith("="):
            continue
        parts = line.split(";")
        if len(parts) < 9:
            continue
        name = parts[3].replace("\\032", " ").strip()
        host = parts[7].strip()
        try:
            port = int(parts[8])
        except ValueError:
            continue
        txt = " ".join(parts[9:])
        found[f"{host}:{port}"] = Discovered(
            name=name,
            host=host,
            port=port,
            device_id=_txt_value(txt, "id"),
            model=_txt_value(txt, "model"),
        )
    return list(found.values())


#: Interfaces a phone is never on. Container and virtual bridges carry their
#: own private ranges, and sweeping them finds nothing but time.
NEVER_THE_PHONE = ("docker", "br-", "virbr", "veth", "tun", "tap", "lo")


def local_networks() -> list[tuple[str, Any]]:
    """The IPv4 networks this computer is currently on."""
    import ipaddress

    result = run(["ip", "-4", "-o", "addr", "show"], timeout=8.0)
    if not result.ok:
        return []

    networks = []
    for line in result.stdout.splitlines():
        match = re.search(r"^\d+:\s*(\S+)\s+inet\s+(\d+\.\d+\.\d+\.\d+/\d+)", line.strip())
        if not match:
            continue
        name, cidr = match.group(1), match.group(2)
        if name.startswith("lo"):
            continue
        try:
            networks.append((name, ipaddress.ip_interface(cidr)))
        except ValueError:
            continue
    return networks


def on_a_local_network(host: str, networks: "list | None" = None) -> bool:
    """Whether *host* is an address this computer could reach directly."""
    import ipaddress

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(address in interface.network
               for _name, interface in (networks if networks is not None else local_networks()))


def sweep_for_companions(port: int, timeout: float = 0.4, workers: int = 64) -> list[str]:
    """Every address on this computer's own subnets listening on *port*."""
    import socket
    from concurrent.futures import ThreadPoolExecutor

    targets: list[str] = []
    for name, interface in local_networks():
        if name.startswith(NEVER_THE_PHONE):
            continue
        network = interface.network
        if network.num_addresses > 256:
            continue
        targets += [str(host) for host in network.hosts() if host != interface.ip]

    if not targets:
        return []

    def listening(address: str) -> str:
        try:
            with socket.create_connection((address, port), timeout=timeout):
                return address
        except OSError:
            return ""

    with ThreadPoolExecutor(max_workers=min(workers, len(targets))) as pool:
        found = [address for address in pool.map(listening, targets) if address]
    log.info("swept %d addresses, %d listening on %s", len(targets), len(found), port)
    return found


def default_gateways() -> list[str]:
    """IPv4 gateways of the active routes."""
    result = run(["ip", "-4", "route", "show", "default"], timeout=5.0)
    if not result.ok:
        return []
    gateways = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if "via" in parts:
            address = parts[parts.index("via") + 1]
            if address not in gateways:
                gateways.append(address)
    return gateways


def _txt_value(txt: str, key: str) -> str:
    for chunk in txt.replace('" "', '"\n"').splitlines():
        chunk = chunk.strip().strip('"')
        name, sep, value = chunk.partition("=")
        if sep and name == key:
            return value
    return ""


# -- client ------------------------------------------------------------------


@dataclass
class PairedPhone:
    """Everything needed to reconnect without pairing again."""

    host: str = ""
    port: int = DEFAULT_PORT
    token: str = ""
    fingerprint: str = ""     # SHA-256 of the phone's certificate, hex
    name: str = ""
    #: The model number, which is not the phone's name: the companion app
    #: sends both, and the sidebar shows the name with the model underneath.
    model: str = ""
    device_id: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.host and self.token and self.fingerprint)


class CompanionClient(QObject):
    """Persistent connection to the phone, with automatic reconnection."""

    connectedChanged = Signal(bool)
    capabilitiesChanged = Signal(list)
    pairingRequired = Signal()
    paired = Signal(object)                 # PairedPhone
    errorOccurred = Signal(str)
    statusChanged = Signal(str)

    notificationPosted = Signal(dict)
    notificationRemoved = Signal(str)
    dndChanged = Signal(str)
    clipboardChanged = Signal(str)
    #: The phone asks for this computer's clipboard; the argument is the request id.
    clipboardQueried = Signal(int)
    callChanged = Signal(dict)
    mediaChanged = Signal(dict)
    batteryChanged = Signal(int, bool)
    phoneStatusChanged = Signal(dict)       # battery, wifi, cell, ringer

    cameraStarted = Signal(dict)
    cameraFrame = Signal(bytes)
    cameraStopped = Signal()

    #: The phone's own audio, over this link rather than Bluetooth.
    phoneAudioStarted = Signal(dict)     # codec, rate, channels
    phoneAudioFrame = Signal(bytes)      # 20 ms of PCM
    #: File transfer, both directions. The JSON half of the conversation
    #: (offers, acceptances, completions, cancellations) and the chunks.
    fileEvent = Signal(dict)
    fileChunk = Signal(dict, bytes)
    #: The socket has written some of what it was holding.
    flushed = Signal()
    phoneAudioStopped = Signal()
    #: Android asks the user on the phone before capturing playback, and only
    #: an activity can ask -- so the phone says "I have put a notification up".
    phoneAudioConsent = Signal(str)

    #: Backoff schedule for reconnection, in seconds.
    RETRY_DELAYS = (2, 5, 10, 20, 30, 60)

    #: How often to prove the link is alive, and how long to
    #: wait for the reply.
    HEARTBEAT_MS = 15_000
    HEARTBEAT_GRACE = 2

    #: How long to wait for one address before moving to the next.
    CONNECT_TIMEOUT_MS = 6_000

    def __init__(
        self,
        phone: PairedPhone | None = None,
        parent: QObject | None = None,
        role: str = "",
    ) -> None:
        super().__init__(parent)
        self.phone = phone or PairedPhone()
        #: "" for the main link; "files" for the second connection that carries
        #: nothing but file transfers.
        self.role = role
        self._socket: QSslSocket | None = None
        self._decoder = Decoder()
        self._next_id = 1
        self._pending: dict[int, Callable[[dict[str, Any]], None]] = {}
        self._pending_binary: dict[str, Any] | None = None
        self._capabilities: list[str] = []
        self._authenticated = False
        self._pair_code = ""
        self._retries = 0
        self._want_connection = False

        self._retry_timer = QTimer(self)
        self._retry_timer.setSingleShot(True)
        self._retry_timer.timeout.connect(self._attempt)

        #: Addresses still to try for this reconnection round.
        self._candidates: list[tuple[str, int]] = []
        self._pending_host: tuple[str, int] | None = None
        #: Candidates that are a guess rather than the address
        #: we were paired on.
        self._speculative: set[tuple[str, int]] = set()
        #: How many times we have gone looking this session. The subnet sweep
        #: joins in from the second round.
        self._resolutions = 0
        self._resolving = False
        self._missed_beats = 0
        #: Topics to subscribe to. None means the default set.
        self.wanted_topics: list[str] | None = None

        self._heartbeat = QTimer(self)
        self._heartbeat.timeout.connect(self._send_heartbeat)

        self._connect_timer = QTimer(self)
        self._connect_timer.setSingleShot(True)
        self._connect_timer.timeout.connect(self._on_connect_timeout)

    # -- state ---------------------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._authenticated and self._socket is not None

    @property
    def address(self) -> tuple[str, int] | None:
        """Where the phone answered, while it is connected."""
        return self._pending_host if self._authenticated else None

    @property
    def capabilities(self) -> list[str]:
        return list(self._capabilities)

    def supports(self, capability: str) -> bool:
        return capability in self._capabilities

    # -- lifecycle -----------------------------------------------------------

    def connect_to_phone(self, host: str = "", port: int = 0, pair_code: str = "") -> None:
        """Open (or reopen) the connection, pairing first when asked to."""
        if host:
            self.phone.host = host
        if port:
            self.phone.port = port
        self._pair_code = pair_code
        self._want_connection = True
        self._retries = 0
        # An explicit connect starts a fresh search rather than continuing
        # through stale candidates from an earlier round.
        self._candidates = []
        self._attempt()

    def disconnect_from_phone(self) -> None:
        self._want_connection = False
        self._retry_timer.stop()
        self._teardown()

    def _attempt(self) -> None:
        """Try the next candidate address, resolving a fresh set if needed."""
        if not self._want_connection:
            return
        if self._resolving:
            return

        if not self._candidates:
            self._resolve_candidates()
            return

        host, port = self._candidates.pop(0)
        self._connect_to(host, port)

    def _resolve_candidates(self) -> None:
        """Work out where the phone might be, off the GUI thread."""
        self._resolving = True
        self._resolutions += 1
        saved_host, saved_port = self.phone.host, self.phone.port or DEFAULT_PORT
        device_id = self.phone.device_id
        # A sweep on the first try would delay a connection that was going to
        # work anyway; from the second it is exactly what is needed.
        sweep = self._resolutions > 1

        def work() -> tuple[list[tuple[str, int]], set[tuple[str, int]]]:
            networks = local_networks()
            near: list[tuple[str, int]] = []
            far: list[tuple[str, int]] = []
            speculative: set[tuple[str, int]] = set()

            def add(candidate: tuple[str, int], guess: bool) -> None:
                if candidate in near or candidate in far:
                    return
                (near if on_a_local_network(candidate[0], networks) else far).append(candidate)
                if guess:
                    speculative.add(candidate)

            if saved_host:
                add((saved_host, saved_port), guess=False)
            for entry in discover(timeout=3.0):
                # Only trust an announcement from the phone we paired with.
                if device_id and entry.device_id and entry.device_id != device_id:
                    continue
                add((entry.host, entry.port), guess=True)
            for gateway in default_gateways():
                add((gateway, saved_port), guess=True)

            if sweep or not near:
                for host in sweep_for_companions(saved_port):
                    add((host, saved_port), guess=True)

            return near + far, speculative

        def done(result: tuple) -> None:
            found, speculative = result
            self._resolving = False
            self._speculative = speculative
            self._candidates = list(found) or (
                [(saved_host, saved_port)] if saved_host else []
            )
            if self._candidates:
                self._attempt()
            else:
                self.statusChanged.emit("No phone found on this network.")
                self._schedule_retry()

        def failed(message: str) -> None:
            self._resolving = False
            self._speculative = set()
            log.debug("address resolution failed: %s", message)
            self._candidates = [(saved_host, saved_port)] if saved_host else []
            self._attempt() if self._candidates else self._schedule_retry()

        self.statusChanged.emit("Looking for your phone...")
        submit(work, on_done=done, on_error=failed)

    def _connect_to(self, host: str, port: int) -> None:
        self._teardown(silent=True)
        self._pending_host = (host, port)

        socket = QSslSocket(self)
        # The phone's certificate is self-signed by design, so the usual
        # chain checks cannot apply; the fingerprint pinned at pairing is the
        # trust anchor instead.
        socket.setPeerVerifyMode(QSslSocket.PeerVerifyMode.QueryPeer)
        socket.sslErrors.connect(self._on_ssl_errors)
        socket.encrypted.connect(self._on_encrypted)
        # Nagle's algorithm holds a small write back until the previous one is
        # acknowledged, and every chunk of a file is exactly that pattern: a
        # short JSON header followed by a large payload.
        socket.setSocketOption(QAbstractSocket.SocketOption.LowDelayOption, 1)
        socket.readyRead.connect(self._on_ready_read)
        # encryptedBytesWritten, not bytesWritten: on a TLS socket the plain
        # side reports everything as written the moment it is encrypted, so the
        # ordinary signal says nothing about what has reached the network.
        socket.encryptedBytesWritten.connect(lambda _n: self.flushed.emit())
        socket.disconnected.connect(self._on_disconnected)
        socket.errorOccurred.connect(self._on_socket_error)
        self._socket = socket
        self._decoder = Decoder()

        self.statusChanged.emit(f"Connecting to {host}...")
        self._connect_timer.start(self.CONNECT_TIMEOUT_MS)
        # Detect a peer that has silently gone away with the network.
        socket.setSocketOption(QAbstractSocket.SocketOption.KeepAliveOption, 1)
        socket.setSocketOption(QAbstractSocket.SocketOption.LowDelayOption, 1)
        socket.connectToHostEncrypted(host, port)

    def _teardown(self, silent: bool = False) -> None:
        was_connected = self._authenticated
        self._authenticated = False
        self._heartbeat.stop()
        self._connect_timer.stop()
        self._pending.clear()
        self._pending_binary = None
        if self._socket is not None:
            self._socket.blockSignals(True)
            self._socket.abort()
            self._socket.deleteLater()
            self._socket = None
        if was_connected and not silent:
            self.connectedChanged.emit(False)

    def _schedule_retry(self) -> None:
        if not self._want_connection:
            return
        # Another address left to try: attempt it straight away rather than
        # waiting out a backoff meant for a genuinely unreachable phone.
        if self._candidates:
            self._retry_timer.start(200)
            return
        delay = self.RETRY_DELAYS[min(self._retries, len(self.RETRY_DELAYS) - 1)]
        self._retries += 1
        self.statusChanged.emit(f"Reconnecting in {delay}s...")
        self._retry_timer.start(delay * 1000)

    # -- TLS -----------------------------------------------------------------

    def _on_ssl_errors(self, errors: list) -> None:
        socket = self._socket
        if socket is None:
            return
        certificate = socket.peerCertificate()
        if certificate.isNull():
            self.errorOccurred.emit("The phone presented no certificate.")
            return
        fingerprint = _fingerprint(certificate)

        if not self.phone.fingerprint:
            # First contact: trust on first use, then pin. The pairing code the
            # user reads off the phone is what authenticates this exchange.
            if self._pair_code:
                self.phone.fingerprint = fingerprint
                socket.ignoreSslErrors()
                return
            self.errorOccurred.emit("This phone is not paired yet.")
            self.pairingRequired.emit()
            return

        if fingerprint == self.phone.fingerprint:
            socket.ignoreSslErrors()
            return

        # Something else answering on the port is not news.
        if self._pending_host in self._speculative:
            log.info("%s is not the phone (certificate does not match)", self._pending_host[0])
            self._teardown(silent=True)
            self._schedule_retry()
            return

        self._want_connection = False
        self.errorOccurred.emit(
            "The phone's security certificate has changed, so the connection was "
            "refused.\n\nThis happens if you reinstalled the companion app - pair "
            "again to accept the new certificate. If you did not reinstall it, "
            "something on the network may be impersonating your phone."
        )

    def _on_connect_timeout(self) -> None:
        """Give up on this address and move to the next candidate."""
        if self._authenticated:
            return
        host = self._pending_host[0] if self._pending_host else "the phone"
        log.info("no answer from %s within %ss", host, self.CONNECT_TIMEOUT_MS / 1000)
        self._teardown(silent=True)
        self._schedule_retry()

    def _on_encrypted(self) -> None:
        self._connect_timer.stop()
        self._retries = 0
        self.statusChanged.emit("Connected, authenticating...")
        self._send({"t": "hello", "v": PROTOCOL_VERSION, "client": "tessera-linux"})

    # -- reading -------------------------------------------------------------

    def _on_ready_read(self) -> None:
        socket = self._socket
        if socket is None:
            return
        chunk = bytes(socket.readAll())
        try:
            for kind, payload in self._decoder.feed(chunk):
                if kind == TYPE_JSON:
                    self._handle_json(decode_json(payload))
                elif kind == TYPE_BINARY:
                    self._handle_binary(payload)
                else:
                    log.debug("ignoring unknown frame type %s", kind)
        except ProtocolError as exc:
            self.errorOccurred.emit(f"The phone sent something unreadable: {exc}")
            self._teardown()
            self._schedule_retry()

    def _handle_json(self, message: dict[str, Any]) -> None:
        kind = message.get("t", "")

        if message.get("binary"):
            # The next binary frame belongs to this header.
            self._pending_binary = message
            return

        handler = getattr(self, f"_recv_{kind}", None)
        if handler is not None:
            handler(message)
            return

        rid = message.get("rid")
        if isinstance(rid, int):
            callback = self._pending.pop(rid, None)
            if callback is not None:
                callback(message)
                return
        log.debug("unhandled message %r", kind)

    def _handle_binary(self, payload: bytes) -> None:
        header, self._pending_binary = self._pending_binary, None
        if header is None:
            log.debug("binary frame with no header; dropping")
            return
        if header.get("t") == "camera_frame":
            self.cameraFrame.emit(payload)
            return
        if header.get("t") == "audio_frame":
            self.phoneAudioFrame.emit(payload)
            return
        if header.get("t") == "file_chunk":
            self.fileChunk.emit(header, payload)
            return
        rid = header.get("rid")
        if isinstance(rid, int):
            callback = self._pending.pop(rid, None)
            if callback is not None:
                callback({**header, "data": payload})

    # -- protocol handlers ---------------------------------------------------

    def _recv_hello(self, message: dict[str, Any]) -> None:
        version = message.get("v", 0)
        if version != PROTOCOL_VERSION:
            self._want_connection = False
            self.errorOccurred.emit(
                f"The companion app speaks protocol v{version}, this app speaks "
                f"v{PROTOCOL_VERSION}. Update whichever is older."
            )
            self._teardown()
            return
        self.phone.name = message.get("name", "") or self.phone.name
        self.phone.model = message.get("model", "") or self.phone.model
        self.phone.device_id = message.get("id", "") or self.phone.device_id

        if self._pair_code:
            self._send({"t": "pair", "code": self._pair_code, "name": computer_name()})
        elif self.phone.token:
            self._send(self._auth_message())
        else:
            self.pairingRequired.emit()

    def _auth_message(self) -> dict[str, Any]:
        message: dict[str, Any] = {
            "t": "auth", "token": self.phone.token, "name": computer_name(),
        }
        if self.role:
            message["role"] = self.role
        return message

    def _recv_pair_ok(self, message: dict[str, Any]) -> None:
        self.phone.token = message.get("token", "")
        self._pair_code = ""
        self.paired.emit(self.phone)
        self._send(self._auth_message())

    def _recv_pair_fail(self, message: dict[str, Any]) -> None:
        self._pair_code = ""
        self._want_connection = False
        self.errorOccurred.emit(
            message.get("message", "") or "That pairing code was not accepted."
        )
        self._teardown()

    def _recv_auth_ok(self, message: dict[str, Any]) -> None:
        self._authenticated = True
        self._capabilities = list(message.get("caps", []))

        # Remember whichever address worked; after moving onto the phone's
        # hotspot that is a different one from the address we were paired on.
        if self._pending_host is not None:
            host, port = self._pending_host
            if (host, port) != (self.phone.host, self.phone.port):
                log.info("phone is now at %s:%s", host, port)
                self.phone.host, self.phone.port = host, port
                self.paired.emit(self.phone)
        self._candidates = []
        self._speculative = set()
        self._resolutions = 0
        self._missed_beats = 0
        self._heartbeat.start(self.HEARTBEAT_MS)
        self.statusChanged.emit(f"Connected to {self.phone.name or self.phone.host}")
        self.connectedChanged.emit(True)
        self.capabilitiesChanged.emit(self._capabilities)
        # The file connection subscribes to nothing: every event it carried
        # would arrive twice.
        if not self.role:
            self._send({"t": "sub", "topics": self._subscription_topics()})

    def _subscription_topics(self) -> list[str]:
        """Ask only for the events the user wants."""
        if self.wanted_topics is None:
            return ["notifications", "dnd", "battery"]
        return list(self.wanted_topics)

    def _recv_auth_fail(self, message: dict[str, Any]) -> None:
        self.phone.token = ""
        self._want_connection = False
        self.errorOccurred.emit(
            message.get("message", "") or "The phone rejected the saved pairing."
        )
        self.pairingRequired.emit()
        self._teardown()

    def _recv_notification(self, message: dict[str, Any]) -> None:
        self.notificationPosted.emit(message)

    def _recv_notification_removed(self, message: dict[str, Any]) -> None:
        self.notificationRemoved.emit(str(message.get("id", "")))

    def _recv_clipboard(self, message: dict[str, Any]) -> None:
        self.clipboardChanged.emit(str(message.get("text", "")))

    def _recv_clipboard_query(self, message: dict[str, Any]) -> None:
        self.clipboardQueried.emit(int(message.get("req", 0)))

    def _recv_media(self, message: dict[str, Any]) -> None:
        self.mediaChanged.emit(message)

    def _recv_call(self, message: dict[str, Any]) -> None:
        self.callChanged.emit(message)

    def _recv_dnd(self, message: dict[str, Any]) -> None:
        self.dndChanged.emit(str(message.get("mode", "off")))

    def _recv_battery(self, message: dict[str, Any]) -> None:
        self.batteryChanged.emit(int(message.get("level", 0)), bool(message.get("charging")))

    def _recv_status(self, message: dict[str, Any]) -> None:
        """Battery, signal and ringer state in one frame."""
        self.phoneStatusChanged.emit(message)
        battery = message.get("battery")
        if isinstance(battery, dict) and int(battery.get("level", -1)) >= 0:
            self.batteryChanged.emit(
                int(battery["level"]), bool(battery.get("charging"))
            )

    def _recv_camera_started(self, message: dict[str, Any]) -> None:
        self.cameraStarted.emit(message)

    def _recv_camera_stopped(self, _message: dict[str, Any]) -> None:
        self.cameraStopped.emit()

    def _recv_caps(self, message: dict[str, Any]) -> None:
        """The phone's list changed mid-session."""
        self._capabilities = list(message.get("caps", []))
        self.capabilitiesChanged.emit(self._capabilities)

    def _recv_audio_started(self, message: dict[str, Any]) -> None:
        self.phoneAudioStarted.emit(message)

    def _recv_audio_stopped(self, _message: dict[str, Any]) -> None:
        self.phoneAudioStopped.emit()

    def _recv_audio_consent(self, message: dict[str, Any]) -> None:
        self.phoneAudioConsent.emit(message.get("message", ""))

    # File transfer.
    def _recv_file_offer(self, message: dict[str, Any]) -> None:
        self.fileEvent.emit(message)

    _recv_file_accept = _recv_file_offer
    _recv_file_reject = _recv_file_offer
    _recv_file_done = _recv_file_offer
    _recv_file_saved = _recv_file_offer
    _recv_file_cancel = _recv_file_offer

    def _recv_error(self, message: dict[str, Any]) -> None:
        rid = message.get("rid")
        if isinstance(rid, int) and rid in self._pending:
            self._pending.pop(rid)({"t": "error", **message})
            return
        self.errorOccurred.emit(message.get("message", "The phone reported an error."))

    # -- writing -------------------------------------------------------------

    def _send(self, message: dict[str, Any]) -> None:
        socket = self._socket
        if socket is None or socket.state() != QAbstractSocket.SocketState.ConnectedState:
            raise ProtocolError("not connected to the phone")
        socket.write(QByteArray(encode_json(message)))

    def send_binary(self, header: dict[str, Any], payload: bytes) -> None:
        """Send a JSON header and the bytes it describes, adjacently."""
        socket = self._socket
        if socket is None or socket.state() != QAbstractSocket.SocketState.ConnectedState:
            raise ProtocolError("not connected to the phone")
        message = {**header, "binary": True, "length": len(payload)}
        # One write, so the header and payload stay adjacent.
        socket.write(QByteArray(encode_json(message) + encode_binary(payload)))

    @property
    def pending_bytes(self) -> int:
        """Bytes handed to the socket that have not reached the network yet."""
        socket = self._socket
        if socket is None:
            return 0
        pending = int(socket.encryptedBytesToWrite())
        return pending or int(socket.bytesToWrite())

    def send(self, message: dict[str, Any]) -> None:
        """Fire-and-forget a message, ignoring it if we are offline."""
        try:
            self._send(message)
        except ProtocolError:
            log.debug("dropping %s: not connected", message.get("t"))

    def request(
        self,
        message: dict[str, Any],
        on_reply: Callable[[dict[str, Any]], None],
    ) -> None:
        """Send *message* and deliver its reply to *on_reply*."""
        request_id = self._next_id
        self._next_id += 1
        self._pending[request_id] = on_reply
        try:
            self._send({**message, "req": request_id})
        except ProtocolError as exc:
            self._pending.pop(request_id, None)
            on_reply({"t": "error", "message": str(exc)})

    # -- liveness ------------------------------------------------------------

    def _send_heartbeat(self) -> None:
        """Prove the link still works, and reconnect when it does not."""
        if not self._authenticated:
            self._heartbeat.stop()
            return

        self._missed_beats += 1
        if self._missed_beats > self.HEARTBEAT_GRACE:
            log.info("no reply to %s heartbeats; reconnecting", self._missed_beats)
            self.statusChanged.emit("Connection lost; reconnecting...")
            self._heartbeat.stop()
            self._teardown()
            self._candidates = []       # the address may have changed
            self._retries = 0
            self._schedule_retry()
            return

        self.request({"t": "ping"}, lambda _reply: self._on_heartbeat_reply())

    def _on_heartbeat_reply(self) -> None:
        self._missed_beats = 0

    # -- socket events -------------------------------------------------------

    def _on_disconnected(self) -> None:
        # Mark the link down before announcing it, so listeners see it as down.
        was_connected = self._authenticated
        self._authenticated = False
        self._heartbeat.stop()
        if was_connected:
            self.connectedChanged.emit(False)
        self.statusChanged.emit("Disconnected")
        self._schedule_retry()

    def _on_socket_error(self, error: QAbstractSocket.SocketError) -> None:
        socket = self._socket
        if socket is None:
            return
        if error == QAbstractSocket.SocketError.RemoteHostClosedError:
            return  # handled by _on_disconnected
        self.statusChanged.emit(socket.errorString())
        self._schedule_retry()


def _fingerprint(certificate: QSslCertificate) -> str:
    from PySide6.QtCore import QCryptographicHash

    digest = certificate.digest(QCryptographicHash.Algorithm.Sha256)
    return bytes(digest).hex()


def computer_name() -> str:
    """This computer's name, for the phone's list of paired computers."""
    try:
        with open("/etc/machine-info", encoding="utf-8") as info:
            for line in info:
                if line.startswith("PRETTY_HOSTNAME="):
                    pretty = line.split("=", 1)[1].strip().strip('"')
                    if pretty:
                        return pretty
    except OSError:
        pass
    import socket
    return socket.gethostname().split(".")[0] or "Computer"


def generate_token() -> str:
    return secrets.token_hex(32)


def b64decode(value: str) -> bytes:
    try:
        return base64.b64decode(value or "", validate=True)
    except (ValueError, TypeError):
        return b""
