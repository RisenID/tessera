#!/usr/bin/env python3
"""Exercise the Windows and Linux paths from wherever this is run.

Most of this pretends, through TESSERA_PLATFORM, so both systems' paths are
checked on either. What cannot be pretended -- the kernel, the shell's own
folders, WinRT -- is checked only on the system that has it.
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

# Before any tessera import: a check must never write the real configuration.
from sandbox import HOST, isolate, only_on                           # noqa: E402

isolate()

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


def as_this_computer():
    """Reload the platform module with nothing pretended."""
    os.environ.pop("TESSERA_PLATFORM", None)
    return importlib.reload(importlib.import_module("tessera.core.platform"))


# -- the real system ----------------------------------------------------------


@only_on("windows")
def this_windows() -> None:
    print("\n== This Windows ==")
    import subprocess

    platform = as_this_computer()
    check("Windows is detected without being told", platform.NAME, "windows")
    check("helpers start without a console window",
          platform.no_window_flags(), subprocess.CREATE_NO_WINDOW)

    transfer = importlib.reload(importlib.import_module("tessera.backends.filetransfer"))
    known = transfer._windows_downloads()
    check("the shell says where Downloads is", known is not None and known.is_dir(), True)
    check("and received files go there", transfer.default_directory(), known)

    bluetooth = importlib.reload(importlib.import_module("tessera.backends.bluetooth"))
    check("Bluetooth is answered by WinRT",
          bluetooth.connect_quietly.__module__, "tessera.backends.bluetooth_win")
    check("and this Python has the WinRT wheels",
          importlib.import_module("tessera.backends.bluetooth_win").available(), True)

    contains("SSHFS-Win's folder is searched for sshfs",
             platform._EXTRA_PATHS["windows"], r"%ProgramFiles%\SSHFS-Win\bin")
    storage_win = importlib.import_module("tessera.backends.storage_win")
    print(f"     (on this machine: WinFsp {'found' if storage_win.winfsp_installed() else 'missing'}, "
          f"sshfs {storage_win.sshfs_path() or 'missing'})")


@only_on("linux")
def this_linux() -> None:
    print("\n== This Linux ==")
    platform = as_this_computer()
    check("Linux is detected without being told", platform.NAME, "linux")
    check("there is no console flag to pass", platform.no_window_flags(), 0)
    bluetooth = importlib.reload(importlib.import_module("tessera.backends.bluetooth"))
    check("Bluetooth is answered by BlueZ",
          bluetooth.connect_quietly.__module__, "tessera.backends.bluetooth")


# -- pretending ---------------------------------------------------------------


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
    check("the kernel is not faked", platform.REAL, HOST)
    check("config lands in APPDATA",
          platform.config_dir(), home / "Roaming" / "Tessera")
    check("state lands in LOCALAPPDATA",
          platform.state_dir(), home / "Local" / "Tessera")
    check("tools get .exe", platform.tool("adb"), "adb.exe")
    check("an .exe is not doubled", platform.tool("scrcpy.exe"), "scrcpy.exe")
    # The flag follows the kernel underneath, not the pretence.
    check("a console flag only where there are consoles",
          platform.no_window_flags(), 0x08000000 if HOST == "windows" else 0)

    print("-- what Windows cannot do --")
    for feature in ("kdeconnect", "mpris", "bluetooth_codecs"):
        check(f"{feature} refused", platform.supported(feature), False)
        contains(f"{feature} explained", platform.reason(feature), " ")
    for feature in ("notifications", "messages", "photos", "calls", "clipboard",
                    "screen", "apps", "hotspot", "dnd_sync", "otp",
                    "bluetooth_audio", "bluetooth_calls", "storage", "webcam"):
        check(f"{feature} offered", platform.supported(feature), True)

    print("-- the config file --")
    config = loaded["config"]
    fresh = config.Config()
    check("a panel width to start from", fresh.panel.width > 0, True)
    check("the link audio route waits for its switch, as Bluetooth works",
          fresh.features.phone_audio, False)
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
    if manager is not None and manager.key == "winget":
        contains("WinFsp has a package", packages.install_command("winfsp"), "WinFsp.WinFsp")
        contains("so does SSHFS-Win", packages.install_command("sshfs-win"),
                 "SSHFS-Win.SSHFS-Win")

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
    wifi.have = lambda _program: True          # netsh, which may not be here
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


def windows_parity_checks() -> None:
    """The later features, on Windows: files, audio, clipboard, storage."""
    print("-- files, audio, clipboard and storage on Windows --")
    platform = importlib.import_module("tessera.core.platform")
    check("still being Windows", platform.IS_WINDOWS, True)

    transfer = importlib.reload(importlib.import_module("tessera.backends.filetransfer"))
    ran: list[list[str]] = []
    proc = importlib.import_module("tessera.core.proc")
    original_run = proc.run
    proc.run = lambda argv, *args, **kwargs: ran.append(list(argv)) or original_run(["false"])
    try:
        folder = transfer.default_directory()
        transfer.reveal(Path.home() / "Downloads" / "photo.jpg")
    finally:
        proc.run = original_run
    expected = [Path.home() / "Downloads", Path.home()]
    if HOST == "windows":
        # A real shell answers first, and Downloads may have been moved.
        expected.insert(0, transfer._windows_downloads())
    check("received files go to Downloads or home, not an XDG answer",
          folder in expected, True)
    check("and nothing Linux-only is run to find it or show it",
          [argv[0] for argv in ran if argv[0] in ("xdg-user-dir", "dbus-send", "xdg-open")], [])

    spec = (ROOT / "packaging" / "windows" / "tessera.spec").read_text("utf-8")
    excluded = spec.split("EXCLUDED_QT = [", 1)[1].split("]", 1)[0]
    check("the build keeps QtMultimedia, which the phone's audio plays through",
          '"PySide6.QtMultimedia"' in excluded, False)
    contains("and bundles WinRT's audio namespace, which is imported late",
             spec, '"winrt.windows.media.audio"')

    clipboard_adb = importlib.reload(importlib.import_module("tessera.backends.clipboard_adb"))
    # This machine's own adb would be found by its bare name; on Windows
    # nothing called "adb" exists, so ask as Windows would, with nothing found.
    found = platform.find_tool
    platform.find_tool = lambda _name: ""
    try:
        argv = clipboard_adb.argv("192.168.1.5:5555")
    finally:
        platform.find_tool = found
    check("the adb clipboard route asks for adb.exe", Path(argv[0]).name, "adb.exe")
    contains("with a command for the phone's shell, not Windows'", argv[-1], "app_process")
    check("clipboard sharing is offered", platform.supported("clipboard"), True)

    bluetooth = importlib.reload(importlib.import_module("tessera.backends.bluetooth"))
    check("Bluetooth is answered by AudioPlaybackConnection",
          bluetooth.find_phone.__module__, "tessera.backends.bluetooth_win")
    audio = importlib.reload(importlib.import_module("tessera.backends.audio"))
    check("with no PipeWire tools to ask for", audio.tools_missing(), [])

    storage = importlib.reload(importlib.import_module("tessera.backends.storage"))
    storage_win = importlib.reload(importlib.import_module("tessera.backends.storage_win"))
    found_sshfs, found_winfsp = storage_win.sshfs_path, storage_win.winfsp_installed
    try:
        storage_win.sshfs_path = lambda: ""
        storage_win.winfsp_installed = lambda: False
        check("without WinFsp and SSHFS-Win there is no backend", storage.backend(), "")
        contains("and the advice says what to install", storage.missing_advice(), "WinFsp")
        storage_win.sshfs_path = lambda: r"C:\Program Files\SSHFS-Win\bin\sshfs.exe"
        storage_win.winfsp_installed = lambda: True
        check("with both, SSHFS-Win mounts it", storage.backend(), storage.SSHFS_WIN)
    finally:
        storage_win.sshfs_path, storage_win.winfsp_installed = found_sshfs, found_winfsp
    check("file transfer is offered", platform.supported("file_transfer"), True)
    check("the phone's audio over the link is offered", platform.supported("phone_audio"), True)


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

    check("every page is offered", sorted(IMPOSSIBLE), [])
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
    check("the codec card is hidden, as Windows picks the codec",
          window.settings_page.audio_card.isHidden(), True)
    check("the Bluetooth audio switch can be turned on",
          window.settings_page.feature_boxes["bluetooth_audio"].isEnabled(), True)
    check("so can the phone storage one",
          window.settings_page.feature_boxes["storage"].isEnabled(), True)
    check("the Bluetooth cards are shown on the Audio page",
          [c.isVisibleTo(window.audio_page) for c in window.audio_page._bluetooth_cards],
          [True, True, True])
    check("with the button for calls",
          window.audio_page.call_button.isVisibleTo(window.audio_page), True)
    check("and the link card waits for its switch, as on Linux",
          window.audio_page.link_card.isVisibleTo(window.audio_page), False)
    # The audio tile is not on by default, so choosing it is what proves the
    # platform no longer vetoes it.
    hub.config.panel.tiles.append("audio")
    hub.config.features.phone_audio = True
    window.panel.apply_tiles()
    check("the sidebar will take an audio switch on Windows",
          window.panel.tiles["audio"].property("feature_off"), False)
    check("and a webcam one",
          window.panel.tiles["camera"].property("feature_off"), False)
    check("but the clipboard one is there",
          window.panel.tiles["clipboard"].property("feature_off"), False)

    print("-- a burst of notifications --")
    import time
    from PySide6.QtCore import QEvent, QObject
    from tessera.core.models import Notification

    class Strays(QObject):
        count = 0

        def eventFilter(self, obj, event):  # noqa: N802
            if (event.type() == QEvent.Type.Show and obj.isWidgetType() and obj.isWindow()
                    and type(obj).__name__ == "FeedRow"):
                Strays.count += 1
            return False

    strays = Strays()
    app.installEventFilter(strays)
    # Rebuilds queued before the loop runs, as on connect. Old, so none reaches the tray.
    for index in range(6):
        hub._add(Notification(id=f"burst{index}", app="Mail", title=f"Note {index}",
                              when=time.time() - 3600))
        window.panel.refresh_feed()
    app.processEvents()
    app.removeEventFilter(strays)
    check("a burst never pops a rail row out as its own window", Strays.count, 0)

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

    # Two routes: the desktop's notification server where there is one, the
    # tray everywhere else -- which is what Windows actually gets.
    shown: list[tuple[str, str]] = []
    window.popups.tray.showMessage = lambda title, body, *_a: shown.append((title, body))
    window.popups.tray.isVisible = lambda: True
    rich = window.popups.notifier.available

    def told() -> bool:
        return bool(window.popups._by_phone) if rich else bool(shown)

    def forget() -> None:
        for given in list(window.popups._live):
            window.popups.notifier.close(given)
        window.popups._live.clear()
        window.popups._by_phone.clear()
        shown.clear()

    import time

    def settle() -> None:
        # Tray messages are gathered briefly before one is shown.
        from tessera.ui.popups import TRAY_GATHER_MS

        deadline = time.monotonic() + (TRAY_GATHER_MS + 300) / 1000
        while time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.02)

    note = Notification(id="1", app="WhatsApp", title="Aai", text="Dinner?")
    hub._add(note)
    settle()
    check("a notification pops up", told(), True)
    if not rich:
        check("with the app and the sender in the title", shown,
              [("WhatsApp: Aai", "Dinner?")])
    forget()

    hub.config.notification_popups = False
    hub._add(Notification(id="2", app="WhatsApp", title="Aai", text="Again?"))
    settle()
    check("switched off, nothing pops up", told(), False)
    forget()

    hub.config.notification_popups = True
    hub._phone_dnd = "priority"
    hub.config.dnd.mode = "phone_to_desktop"
    hub._add(Notification(id="3", app="WhatsApp", title="Aai", text="Quiet?"))
    settle()
    check("a silenced phone silences the desktop too", told(), False)
    forget()
    window.close()


def without_excluded_qt() -> None:
    """Every module imports with the Qt modules the Windows build leaves out.

    The build excludes them to save space, so a module that imports one
    without a fallback starts here and crashes there. A fresh interpreter,
    because this one has imported them already.
    """
    import re
    import subprocess

    print("\n== Without the Qt modules the Windows build leaves out ==")
    spec = (ROOT / "packaging" / "windows" / "tessera.spec").read_text("utf-8")
    excluded = re.findall(r'"(PySide6\.\w+)"', spec.split("EXCLUDED_QT = [", 1)[1].split("]", 1)[0])
    probe = (
        "import importlib, pkgutil, sys\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        f"blocked = set({excluded!r})\n"
        "class Block:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name in blocked:\n"
        "            raise ImportError(name + ' is not in the Windows build')\n"
        "sys.meta_path.insert(0, Block())\n"
        "import tessera\n"
        "for module in pkgutil.walk_packages(tessera.__path__, 'tessera.'):\n"
        "    if module.name == 'tessera.__main__':\n"
        "        continue\n"
        "    try:\n"
        "        importlib.import_module(module.name)\n"
        "    except Exception as exc:\n"
        "        print(f'{module.name}: {exc}')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, timeout=180,
        env={**os.environ, "TESSERA_PLATFORM": "windows", "QT_QPA_PLATFORM": "offscreen"},
    )
    check("QtDBus is among them, so this means something", "PySide6.QtDBus" in excluded, True)
    check("every module still imports", result.stdout.strip().splitlines(), [])
    check("and the probe itself ran", result.returncode, 0)


def main() -> int:
    # The real system first, before anything is reloaded under a pretence.
    this_windows()
    this_linux()
    without_excluded_qt()
    windows_checks()
    windows_parity_checks()
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
