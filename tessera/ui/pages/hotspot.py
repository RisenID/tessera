"""One-click phone hotspot."""

from __future__ import annotations

import logging

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from ...backends import companion, hotspot
from ...core.hub import Hub
from ...core.proc import submit
from ..theme import SPACE, Palette
from ..widgets import Card, Pill, Toast, heading

log = logging.getLogger(__name__)


class HotspotPage(QWidget):
    def __init__(self, hub: Hub, palette: Palette, parent: QWidget | None = None):
        super().__init__(parent)
        self.hub = hub
        self.palette_tokens = palette
        self._busy = False
        #: Where the phone says it can be reached, sent while both ends are
        #: still on the same network.
        self._addresses: list[str] = []
        #: The network last joined here, for a hotspot with no name in Settings.
        self._joining = ""
        self._joined_ssid = ""

        # Watching for a hotspot switched on by hand.
        self._waiting = QTimer(self)
        self._waiting.setInterval(2000)
        self._waiting.timeout.connect(self._poll_hotspot)
        self._waited = 0

        outer = QVBoxLayout(self)
        outer.setContentsMargins(SPACE["xl"], SPACE["xl"], SPACE["xl"], SPACE["xl"])
        outer.setSpacing(SPACE["lg"])
        outer.addWidget(heading("Hotspot", "Turn on your phone's hotspot and join it in one click"))

        card = Card(self)
        row = QHBoxLayout()
        title = QLabel("Connection")
        title.setObjectName("SectionTitle")
        row.addWidget(title)
        row.addStretch(1)
        self.state_pill = Pill("Not connected", "muted")
        self.state_pill.apply(palette)
        row.addWidget(self.state_pill)
        card.body().addLayout(row)

        self.ssid = QLineEdit(hub.config.hotspot.ssid)
        self.ssid.setPlaceholderText("Hotspot network name (leave blank to use the phone's own)")
        card.add(self.ssid)

        self.passphrase = QLineEdit(hub.config.hotspot.passphrase)
        self.passphrase.setPlaceholderText(
            "Password (at least 8 characters; 6 GHz requires WPA3)"
        )
        self.passphrase.setEchoMode(QLineEdit.EchoMode.Password)
        card.add(self.passphrase)

        self.band = QComboBox()
        card.add(self.band)

        self.band_hint = QLabel()
        self.band_hint.setObjectName("Muted")
        self.band_hint.setWordWrap(True)
        self.band_hint.setVisible(False)
        card.add(self.band_hint)
        self._populate_bands(hub.device_caps.get("hotspotBands", []))

        buttons = QHBoxLayout()
        self.connect_button = QPushButton("Turn on and connect")
        self.connect_button.setObjectName("Primary")
        self.connect_button.clicked.connect(self._connect)
        buttons.addWidget(self.connect_button)

        self.stop_button = QPushButton("Turn off")
        self.stop_button.clicked.connect(self._stop)
        buttons.addWidget(self.stop_button)
        buttons.addStretch(1)
        card.body().addLayout(buttons)

        self.band_note = QLabel()
        self.band_note.setObjectName("Muted")
        self.band_note.setWordWrap(True)
        self.band_note.setVisible(False)
        card.add(self.band_note)

        self.status = QLabel()
        self.status.setObjectName("Muted")
        self.status.setWordWrap(True)
        card.add(self.status)
        outer.addWidget(card)

        explain = Card(self)
        explain.add(self._explainer())
        outer.addWidget(explain)
        outer.addStretch(1)

        self.toast = Toast(self)
        hub.deviceCapsChanged.connect(
            lambda caps: self._populate_bands(caps.get("hotspotBands", []))
        )
        self._refresh()

    #: Bands, with the trade-off that decides which one someone wants.
    BAND_LABELS = {
        "2.4": "2.4 GHz — best range, slowest",
        "5": "5 GHz — faster, shorter range",
        "6": "6 GHz — fastest, needs Wi-Fi 6E on this computer (WPA3)",
    }

    def _populate_bands(self, bands: list) -> None:
        """Offer only the bands this phone's radio supports."""
        supported = [b for b in ("2.4", "5", "6") if b in bands] or ["2.4"]
        if "6" in supported or "5" in supported:
            self.band_hint.setText(
                "The band follows the phone's own Wi-Fi connection. Turn that "
                "off to hold 5 or 6 GHz."
            )
            self.band_hint.setVisible(True)
        saved = self.hub.config.hotspot.band

        self.band.blockSignals(True)
        self.band.clear()
        for value in supported:
            self.band.addItem(self.BAND_LABELS.get(value, f"{value} GHz"), value)
        index = next(
            (i for i in range(self.band.count()) if self.band.itemData(i) == saved), 0
        )
        self.band.setCurrentIndex(index)
        self.band.blockSignals(False)

    def _explainer(self) -> QLabel:
        label = QLabel(
            "With Shizuku running the phone takes the command directly. "
            "Without it, Tessera opens tethering settings and joins once the "
            "hotspot appears. See docs/DESIGN.md."
        )
        label.setObjectName("Muted")
        label.setWordWrap(True)
        return label

    def quick_toggle(self) -> None:
        """Start the hotspot and join it, or leave it if we are already on it."""
        if self._busy:
            self.status.setText("Already working on it...")
            return
        if self.hub.hotspot_joined:
            self._stop()
            return
        self._connect()

    def _save(self) -> None:
        cfg = self.hub.config.hotspot
        cfg.ssid = self.ssid.text().strip()
        cfg.passphrase = self.passphrase.text()
        cfg.band = self.band.currentData()
        self.hub.config.save()

    def _connect(self) -> None:
        if self._busy:
            return
        self._save()
        cfg = self.hub.config.hotspot

        if self.hub.companion.connected and self.hub.companion.supports("hotspot"):
            self._busy = True
            self.status.setText("Asking your phone to start its hotspot...")
            self.hub.companion.request(
                {
                    "t": "hotspot_start",
                    "ssid": cfg.ssid,
                    "passphrase": cfg.passphrase,
                    "band": cfg.band,
                },
                self._on_phone_started,
            )
            return

        if self.hub.companion.connected and self.hub.companion.supports("hotspot_wait"):
            self._wait_for_manual_hotspot()
            return

        if self.hub.companion.connected and self.hub.companion.supports("hotspot_panel"):
            self.hub.companion.send({"t": "hotspot_panel"})
            self.status.setText(
                "Shizuku is not running, so the tethering panel was opened on your "
                "phone. Tap the hotspot toggle, then press this button again."
            )
            return

        self._connect_over_adb()

    # -- a hotspot switched on by hand --------------------------------------

    #: How long to keep watching.
    WAIT_SECONDS = 150

    def _wait_for_manual_hotspot(self) -> None:
        """Open the tethering panel, then carry on once the hotspot appears."""
        if not self.hub.config.hotspot.ssid:
            self.status.setText(
                "Fill in the network name and password above first — without "
                "Shizuku the phone cannot tell this computer what they are."
            )
            return

        self._busy = True
        self._waited = 0
        # Ask before opening anything: the hotspot may be on already, in which
        # case throwing the user into Settings would be pure noise.
        self.status.setText("Checking whether the hotspot is already on...")
        self.hub.companion.request({"t": "hotspot_status"}, self._on_first_status)

    def _on_first_status(self, reply: dict) -> None:
        if reply.get("t") == "error":
            self._stop_waiting()
            self.status.setText(
                reply.get("message", "The phone did not answer about its hotspot.")
            )
            return
        if reply.get("tethering"):
            self.status.setText("Your phone's hotspot is already on. Joining...")
            self._proceed(reply)
            return
        self.hub.companion.send({"t": "hotspot_panel"})
        self.status.setText(
            "Tap the hotspot toggle on your phone. Joining follows "
            "automatically; Stop cancels."
        )
        self._waiting.start()

    def _poll_hotspot(self) -> None:
        self._waited += self._waiting.interval() // 1000
        if self._waited >= self.WAIT_SECONDS:
            self._stop_waiting()
            self.status.setText(
                "Gave up waiting for the hotspot. Turn it on and press Start again."
            )
            return
        if not self.hub.companion.connected:
            return
        self.hub.companion.request({"t": "hotspot_status"}, self._on_waiting_status)

    def _on_waiting_status(self, reply: dict) -> None:
        if not self._waiting.isActive() or not reply.get("tethering"):
            return
        self._stop_waiting()
        self.status.setText("The hotspot is up. Joining...")
        self._proceed(reply)

    def _stop_waiting(self) -> None:
        self._waiting.stop()
        self._busy = False

    def _proceed(self, reply: dict) -> None:
        """Join a hotspot the phone brought up on its own."""
        self._stop_waiting()
        self._remember_addresses(reply.get("addresses"))
        cfg = self.hub.config.hotspot
        self._join(
            str(reply.get("ssid") or cfg.ssid),
            str(reply.get("passphrase") or cfg.passphrase),
        )

    def _on_phone_started(self, reply: dict) -> None:
        self._busy = False
        self._remember_addresses(reply.get("addresses"))
        if reply.get("t") == "error":
            self.status.setText(reply.get("message", "The phone could not start its hotspot."))
            self.toast.show_message("Could not start the hotspot", self.palette_tokens, "danger")
            return

        # Samsung's One UI reconciles any band we ask for against its own
        # hotspot setting, so say plainly when the phone chose differently
        # rather than leaving a band selector that appears to do nothing.
        requested = str(reply.get("requestedBand", "") or "")
        actual = str(reply.get("band", "") or "")
        if requested and actual and requested != actual:
            frequency = reply.get("frequency", 0)
            self.band_note.setText(
                f"Your phone started the hotspot on {actual} GHz"
                f"{f' ({frequency} MHz)' if frequency else ''}, not the {requested} GHz "
                "you asked for. The band is fixed by the phone's own hotspot "
                "settings — change it in Settings › Connections › Mobile Hotspot › Band."
            )
            self.band_note.setVisible(True)
        else:
            self.band_note.setVisible(False)

        self._join(reply.get("ssid", ""), reply.get("passphrase", ""))

    def _connect_over_adb(self) -> None:
        serial = self.hub.serial
        if not serial:
            self.status.setText(
                "No phone connected. Pair the companion app, or attach your phone "
                "over adb, to control the hotspot from here."
            )
            return

        self._busy = True
        self.status.setText("Starting the hotspot over adb...")
        cfg = self.hub.config.hotspot

        def work() -> tuple:
            ap = hotspot.start_phone_hotspot(serial, cfg)
            # Read after starting, so the tether interface is in the list.
            return ap, hotspot.phone_addresses(serial)

        def started(result: tuple) -> None:
            ap, addresses = result
            self._addresses = list(addresses)
            self._join(ap.ssid, ap.passphrase)

        submit(work, on_done=started, on_error=self._fail)

    def _join(self, ssid: str, passphrase: str) -> None:
        if not ssid:
            self._fail("The hotspot started, but its network name is unknown.")
            return
        self._busy = True
        self._joining = ssid
        self.status.setText(f"Waiting for '{ssid}' to appear, then connecting...")
        timeout = float(self.hub.config.hotspot.scan_timeout)

        submit(
            hotspot.connect_wifi,
            ssid,
            passphrase,
            timeout,
            on_done=self._succeed,
            on_error=self._fail,
        )

    def _remember_addresses(self, addresses) -> None:
        """Keep the phone's own list of where it can be reached."""
        self._addresses = [
            str(entry.get("address", "")).strip()
            for entry in (addresses or [])
            if isinstance(entry, dict) and entry.get("address")
        ]
        if self._addresses:
            log.debug("phone reachable at %s", ", ".join(self._addresses))

    def _succeed(self, message: str) -> None:
        self._busy = False
        self._joined_ssid = self._joining
        self.status.setText(message)
        self.toast.show_message(message, self.palette_tokens, "success")
        self._refresh()
        self._find_phone_again()

    # -- picking the link back up ------------------------------------------

    def _find_phone_again(self) -> None:
        """Reconnect over the hotspot: probe the addresses the phone gave."""
        if not self._addresses:
            return
        port = self.hub.companion.phone.port or companion.DEFAULT_PORT
        addresses = list(self._addresses)
        self.status.setText("Joined. Finding your phone on the new network...")

        def done(address: str) -> None:
            if not address:
                # Not a failure of the hotspot: the network is up and working,
                # only the companion link has not come back. Say which.
                self.status.setText(
                    "Joined, but the companion app did not answer. It will "
                    "reconnect on its own."
                )
                return
            self.hub.companion.connect_to_phone(address, port)
            self.status.setText(f"Joined the hotspot and reconnected at {address}.")

        submit(
            hotspot.reachable_address, addresses, port,
            on_done=done, on_error=lambda _m: None,
        )

    def _fail(self, message: str) -> None:
        self._busy = False
        self.status.setText(message)
        self.toast.show_message("Hotspot failed", self.palette_tokens, "danger")

    def _stop(self) -> None:
        self._stop_waiting()
        if self.hub.companion.connected and self.hub.companion.supports("hotspot"):
            self.hub.companion.send({"t": "hotspot_stop"})
            self.status.setText("Asked your phone to switch the hotspot off.")
            # The link drops with the network, so the pill and the panel's tile
            # are refreshed from what NetworkManager says rather than from the
            # reply, which may never arrive.
            QTimer.singleShot(3000, self._refresh)
            return
        serial = self.hub.serial
        if not serial:
            self.status.setText("No phone connected.")
            return
        submit(
            hotspot.stop_phone_hotspot, serial,
            on_done=lambda _r: self.status.setText("Hotspot switched off."),
            on_error=self._fail,
        )

    def _refresh(self) -> None:
        submit(hotspot.active_ssid, on_done=self._show_ssid, on_error=lambda _m: None)

    def _show_ssid(self, ssid: str) -> None:
        if ssid:
            self.state_pill.set_state(ssid, "success")
        else:
            self.state_pill.set_state("Not connected", "muted")
        # Only the phone's own network counts as joined; any other Wi-Fi is
        # just Wi-Fi. The panel's tile reads this.
        wanted = self.hub.config.hotspot.ssid.strip() or self._joined_ssid
        self.hub.set_hotspot_joined(bool(ssid) and bool(wanted) and ssid == wanted)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.toast._reposition()
