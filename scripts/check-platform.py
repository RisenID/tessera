#!/usr/bin/env python3
"""Exercise the Windows and Linux paths from wherever this is run.

The Windows target cannot be tested on the machine it was written on, so
everything about it that *can* be checked without Windows is checked here:
where files go, which features are offered, what the commands look like, what
the registry would be given, and that nothing Linux-only is reached.

`TESSERA_PLATFORM` is what makes that possible -- it moves the app's idea of
the platform without pretending the kernel changed. Anything that genuinely
needs Windows (netsh, winreg, a real toast) is stubbed and the call recorded.

    python3 scripts/check-platform.py

Exits non-zero on the first disagreement, listing them all.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FAILURES: list[str] = []


def check(label: str, got: object, want: object) -> None:
    ok = got == want
    if not ok:
        FAILURES.append(label)
    print(f"{'ok  ' if ok else 'FAIL'} {label}: {got!r}" + ("" if ok else f" != {want!r}"))


def contains(label: str, haystack: object, needle: object) -> None:
    ok = needle in haystack           # type: ignore[operator]
    if not ok:
        FAILURES.append(label)
    print(f"{'ok  ' if ok else 'FAIL'} {label}: {needle!r} in {haystack!r}"
          if not ok else f"ok   {label}")


def as_platform(name: str, **environment: str):
    """Reload the app's modules as if it were running on *name*."""
    os.environ["TESSERA_PLATFORM"] = name
    os.environ.update(environment)
    modules = [
        "tessera.core.platform", "tessera.core.proc", "tessera.core.config",
        "tessera.core.packages", "tessera.core.autostart",
        "tessera.backends.dbus", "tessera.backends.silence",
    ]
    loaded = {}
    for module in modules:
        loaded[module.rsplit(".", 1)[-1]] = importlib.reload(
            importlib.import_module(module)
        )
    return loaded


def windows_checks() -> None:
    home = Path(tempfile.mkdtemp())
    loaded = as_platform(
        "windows",
        APPDATA=str(home / "Roaming"),
        LOCALAPPDATA=str(home / "Local"),
    )
    platform = loaded["platform"]
    print("\n== Windows ==")
    check("platform name", platform.NAME, "windows")
    check("the kernel is not faked", platform.REAL, "linux"
          if sys.platform.startswith("linux") else platform.REAL)
    check("config lands in APPDATA",
          platform.config_dir(), home / "Roaming" / "Tessera")
    check("state lands in LOCALAPPDATA",
          platform.state_dir(), home / "Local" / "Tessera")
    check("tools get .exe", platform.tool("adb"), "adb.exe")
    check("an .exe is not doubled", platform.tool("scrcpy.exe"), "scrcpy.exe")
    check("no console flag on this kernel", platform.no_window_flags(), 0)

    print("-- what Windows cannot do --")
    for feature in ("bluetooth_audio", "webcam", "kdeconnect", "mpris"):
        check(f"{feature} refused", platform.supported(feature), False)
        contains(f"{feature} explained", platform.reason(feature), " ")
    for feature in ("notifications", "messages", "photos", "calls", "clipboard",
                    "screen", "apps", "hotspot", "dnd_sync", "otp"):
        check(f"{feature} offered", platform.supported(feature), True)

    print("-- the config file --")
    config = loaded["config"]
    fresh = config.Config()
    check("a panel width to start from", fresh.panel.width > 0, True)
    fresh.save()
    written = Path(config.Config.path())
    check("written where Windows keeps settings",
          written.parent, home / "Roaming" / "Tessera")
    check("and readable again", json.loads(written.read_text())["panel"]["width"],
          fresh.panel.width)

    print("-- installing the tools --")
    packages = loaded["packages"]
    manager = packages.detect()
    check("a manager is named", bool(manager), True)
    contains("scrcpy has a package", packages.install_command("scrcpy"), "scrcpy")
    check("no sudo in the advice", "sudo" in packages.install_command("scrcpy"), False)
    check("no pkexec in the argv", "pkexec" in packages.install_argv("scrcpy"), False)

    print("-- autostart --")
    autostart = loaded["autostart"]
    check("the Run key, not a desktop file", autostart.RUN_KEY.endswith("Run"), True)
    written_values: dict[str, str] = {}

    class FakeKey:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    class FakeWinreg:
        HKEY_CURRENT_USER = 1
        KEY_SET_VALUE = 2
        REG_SZ = 1

        @staticmethod
        def CreateKeyEx(_root, path, _reserved, _access):
            written_values["path"] = path
            return FakeKey()

        @staticmethod
        def OpenKey(_root, path):
            if "value" not in written_values:
                raise FileNotFoundError(path)
            return FakeKey()

        @staticmethod
        def SetValueEx(_key, name, _reserved, _kind, value):
            written_values["name"] = name
            written_values["value"] = value

        @staticmethod
        def QueryValueEx(_key, name):
            return written_values.get("value", ""), 1

        @staticmethod
        def DeleteValue(_key, _name):
            written_values.pop("value", None)

    autostart._registry = lambda: FakeWinreg      # the only Windows-only import
    check("off to begin with", autostart.enabled(), False)
    check("turning it on succeeds", autostart.set_enabled(True), True)
    check("under the Run key", written_values["path"], autostart.RUN_KEY)
    check("named for the app", written_values["name"], "Tessera")
    check("the value is a quoted path",
          written_values["value"].startswith('"'), True)
    contains("naming this app", written_values["value"].lower(), "tessera")
    check("and reads back as on", autostart.enabled(), True)
    check("turning it off succeeds", autostart.set_enabled(False), True)
    check("and reads back as off", autostart.enabled(), False)

    print("-- joining the phone's hotspot --")
    wifi = importlib.reload(importlib.import_module("tessera.backends.wifi_win"))
    calls: list[list[str]] = []

    def fake_run(argv, timeout=15.0, stdin=None):
        calls.append(list(argv))
        text = ""
        if argv[:3] == ["netsh", "wlan", "show"] and argv[3] == "interfaces":
            text = "    Name  : Wi-Fi\n    State : connected\n    SSID  : Phone AP\n"
        elif argv[:4] == ["netsh", "wlan", "show", "profiles"]:
            text = "    All User Profile     : Phone AP\n"
        elif argv[:4] == ["netsh", "wlan", "show", "networks"]:
            text = "SSID 1 : Phone AP\n    Signal : 90%\n"
        return type("Result", (), {"ok": True, "stdout": text, "stderr": "",
                                   "text": text, "code": 0})()

    wifi.run = fake_run
    wifi.have = lambda _program: True          # netsh, which is not here
    check("reads the joined network", wifi.active_ssid(), "Phone AP")
    check("lists profiles", wifi.profiles(), ["Phone AP"])
    check("sees the network in a scan", wifi.scan_for("Phone AP", timeout=1), True)
    check("already-joined is not a reconnect",
          wifi.connect("Phone AP", "secret"), "Already connected to Phone AP.")

    print("-- a first-time join writes a profile, then deletes it --")
    leftovers: list[Path] = []
    original_ssid = wifi.active_ssid
    wifi.active_ssid = lambda: ""
    wifi.has_profile = lambda _ssid: False
    profile_bodies: list[str] = []

    def capture(argv, timeout=15.0, stdin=None):
        for part in argv:
            if str(part).startswith("filename="):
                path = Path(str(part).split("=", 1)[1])
                profile_bodies.append(path.read_text("utf-8"))
                leftovers.append(path)
        return fake_run(argv, timeout, stdin)

    wifi.run = capture
    wifi.add_profile("Phone AP", "s3cret&<>")
    check("a profile was handed to netsh", len(profile_bodies), 1)
    contains("with the network name", profile_bodies[0], "<name>Phone AP</name>")
    contains("and the passphrase, escaped", profile_bodies[0], "s3cret&amp;&lt;&gt;")
    contains("as WPA2", profile_bodies[0], "WPA2PSK")
    check("and the file is gone", [p for p in leftovers if p.exists()], [])
    wifi.active_ssid = original_ssid

    print("-- the desktop's Do Not Disturb --")
    silence = loaded["silence"]
    backend = silence.detect()
    check("something to silence", bool(backend), True)
    check("which is our own popups", backend.key, "tessera")
    backend.set(True)
    check("and it takes", backend.silenced(), True)
    backend.set(False)
    check("and releases", backend.silenced(), False)


def linux_checks() -> None:
    print("\n== Linux ==")
    home = Path(tempfile.mkdtemp())
    loaded = as_platform(
        "linux",
        XDG_CONFIG_HOME=str(home / "config"),
        XDG_STATE_HOME=str(home / "state"),
    )
    platform = loaded["platform"]
    check("platform name", platform.NAME, "linux")
    check("config keeps the directory it always had",
          platform.config_dir(), home / "config" / "tessera")
    check("state too", platform.state_dir(), home / "state" / "tessera")
    check("tools are not suffixed", platform.tool("adb"), "adb")
    check("nothing is refused", platform.UNSUPPORTED["linux"], {})
    autostart = loaded["autostart"]
    contains("autostart is a desktop file", str(autostart.path()), ".desktop")
    check("written under XDG autostart",
          autostart.path().parent, home / "config" / "autostart")
    check("on succeeds", autostart.set_enabled(True), True)
    check("and reads back", autostart.enabled(), True)
    contains("with an Exec line", autostart.path().read_text(), "Exec=")
    check("off succeeds", autostart.set_enabled(False), True)
    check("and reads back", autostart.enabled(), False)


def interface_checks() -> None:
    """The window itself, built as if on Windows."""
    print("\n== The interface on Windows ==")
    os.environ["TESSERA_PLATFORM"] = "windows"
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    home = Path(tempfile.mkdtemp())
    os.environ["APPDATA"] = str(home / "Roaming")
    os.environ["LOCALAPPDATA"] = str(home / "Local")

    # A fresh interpreter state: the UI modules read the platform at import.
    for name in [n for n in list(sys.modules) if n.startswith("tessera")]:
        del sys.modules[name]

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    from tessera.core import hub as hub_module

    hub_module.Hub._apply_codec_preference = lambda self: None
    hub_module.Hub._watch_bluetooth = lambda self: None

    from tessera.core.config import Config
    from tessera.ui.main_window import IMPOSSIBLE, PAGES, MainWindow
    from tessera.ui.pages.unavailable import UnavailablePage
    from tessera.ui.theme import detect_palette, stylesheet

    palette = detect_palette(app)
    app.setStyleSheet(stylesheet(palette))
    hub = hub_module.Hub(Config.load())
    window = MainWindow(hub, palette)
    window.resize(1280, 800)
    window.show()
    for _ in range(4):
        app.processEvents()

    # Audio stays: the page leads with the route over the companion link,
    # which needs no Bluetooth and works here. Only its Bluetooth half goes.
    check("Webcam is not offered", sorted(IMPOSSIBLE), ["Webcam"])
    check("but Audio is, for the link route", "Audio" not in IMPOSSIBLE, True)
    check("no dead tabs",
          [window.tabs.tabText(i) for i in range(window.tabs.count())
           if window.tabs.isTabVisible(i) and window.tabs.tabData(i) in IMPOSSIBLE],
          [])
    check("nor menu entries",
          [a.text() for a in window.more_menu.actions() if a.text() in IMPOSSIBLE],
          [])
    for name in IMPOSSIBLE:
        index = [n for n, *_ in PAGES].index(name)
        check(f"{name} is a placeholder, not the real page",
              isinstance(window.stack.widget(index), UnavailablePage), True)
    check("the audio settings card is hidden",
          window.settings_page.audio_card.isHidden(), True)
    check("the Bluetooth audio switch cannot be turned on",
          window.settings_page.feature_boxes["bluetooth_audio"].isEnabled(), False)
    check("the Bluetooth cards are hidden on the Audio page",
          [c.isVisibleTo(window.audio_page) for c in window.audio_page._bluetooth_cards],
          [False, False, False])
    check("but the link card is shown",
          window.audio_page.link_card.isVisibleTo(window.audio_page), True)
    # The audio tile is not on by default, so choosing it is what proves the
    # platform no longer vetoes it: on Windows it used to be struck out.
    hub.config.panel.tiles.append("audio")
    window.panel.apply_tiles()
    check("the sidebar will take an audio switch on Windows",
          window.panel.tiles["audio"].property("feature_off"), False)
    check("nor a webcam one",
          window.panel.tiles["camera"].property("feature_off"), True)
    check("but the clipboard one is there",
          window.panel.tiles["clipboard"].property("feature_off"), False)

    print("-- icons, where there is no icon theme --")
    from PySide6.QtGui import QIcon
    from tessera.ui import glyphs
    from tessera.ui.widgets import themed_icon

    QIcon.setThemeName("does-not-exist")
    QIcon.setFallbackThemeName("does-not-exist")
    missing = []
    for names, _glyph, _tip, _checkable, _feature in __import__(
        "tessera.ui.panel", fromlist=["TILES"]
    ).TILES.values():
        if themed_icon(*names).isNull():
            missing.append(names[0])
    for name in [icon for _n, icon, *_ in PAGES]:
        if themed_icon(name).isNull():
            missing.append(name)
    for name in ("battery-020", "battery-100-charging", "network-wireless-60",
                 "network-mobile-80-5g", "network-bluetooth-activated",
                 "view-refresh", "media-playback-start", "media-skip-forward",
                 "media-skip-backward", "audio-volume-muted", "smartphone"):
        if themed_icon(name).isNull():
            missing.append(name)
    check("every icon the app asks for can be drawn", missing, [])
    check("and they are drawn by us, not the theme",
          glyphs.available("smartphone"), True)

    print("-- the desktop popups --")
    from tessera.core.models import Notification

    shown: list[tuple[str, str]] = []
    window.popups.tray.showMessage = lambda title, body, *_a: shown.append((title, body))
    window.popups.tray.isVisible = lambda: True
    note = Notification(id="1", app="WhatsApp", title="Aai", text="Dinner?")
    hub._add(note)
    for _ in range(3):
        app.processEvents()
    check("a notification pops up", shown, [("WhatsApp: Aai", "Dinner?")])

    shown.clear()
    hub.config.notification_popups = False
    hub._add(Notification(id="2", app="WhatsApp", title="Aai", text="Again?"))
    for _ in range(3):
        app.processEvents()
    check("switched off, nothing pops up", shown, [])

    shown.clear()
    hub.config.notification_popups = True
    hub._phone_dnd = "priority"
    hub.config.dnd.mode = "phone_to_desktop"
    hub._add(Notification(id="3", app="WhatsApp", title="Aai", text="Quiet?"))
    for _ in range(3):
        app.processEvents()
    check("a silenced phone silences the desktop too", shown, [])
    window.close()


def main() -> int:
    windows_checks()
    linux_checks()
    interface_checks()
    print()
    if FAILURES:
        print(f"FAILURES ({len(FAILURES)}):")
        for failure in FAILURES:
            print(f"  - {failure}")
        return 1
    print("all platform checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
