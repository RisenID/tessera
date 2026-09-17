# Tessera

Phone Link for Linux (and Windows): notifications, passcodes, messages, calls,
photos, clipboard, files, phone storage, audio, screen mirroring, webcam and
hotspot. Built on Fedora/KDE with a Galaxy S25.

| | |
| --- | --- |
| `tessera/` | Desktop app (Python, PySide6) |
| `android/` | Companion app (Kotlin) |

Windows support and its gaps are in [docs/WINDOWS.md](docs/WINDOWS.md).
What the app reads and where it goes: [Privacy Policy](.github/Privacy.md).

## Features

| Feature | How | Needs |
| --- | --- | --- |
| Notifications, replies, passcodes | `NotificationListenerService` | Companion app |
| Do Not Disturb sync | Interruption filter ⇄ desktop inhibit | Companion app |
| Messages, calls, photos | Telephony, Telecom, MediaStore | Companion app |
| Files both ways, share sheet | Dedicated TLS connection | Companion app |
| Phone storage in the file manager | SFTP server on the phone, mounted with sshfs (Cloud Files sync root on Windows) | All files access |
| Clipboard | Shizuku, the accessibility service, or adb | One of those |
| Phone audio | Playback capture over the link, or Bluetooth A2DP | Companion app / pairing |
| Calls on the computer | Bluetooth HFP | Pairing |
| Webcam | Phone H.264 → ffmpeg → v4l2loopback | Companion app |
| Hotspot | Tethering binder through Shizuku | Shizuku |
| Screen mirroring, app windows | scrcpy | adb |
| Links and notifications to the phone; links from the phone open here | `open_url`, `notify`; the share sheet | Companion app |
| A photo taken with the phone from here | Camera2 still capture | Camera |
| The phone as this computer's microphone | AudioRecord → a virtual source (a chosen output on Windows) | Microphone |
| Calendar, next alarm, timers and alarms | CalendarContract, AlarmManager, the clock app | Calendar |
| Photo backup as photos are taken | MediaStore observer → `media_get` | Photos |
| A saved Wi-Fi network sent to the phone | nmcli / netsh → `ACTION_WIFI_ADD_NETWORKS` | Companion app |
| The phone as a trackpad, keyboard and media remote | RemoteDesktop portal (Linux) / SendInput (Windows) | Companion app |
| Typing into the phone from here | Shizuku, the accessibility service, or adb | One of those |
| Lock when the phone walks away | Bluetooth LE beacon, watched by BlueZ / WinRT | Presence |
| Plasma applet, tray tooltip and menu | `tessera status --json` | The app |

KDE Connect is used as a fallback source until the companion app is paired.

## Command line

The running app answers `tessera <command>` over a local socket
(`tessera-cli.exe` on Windows, where the main executable has no console):

```
tessera status [--json]      the phone: connection, battery, signal, ringer, media, code
tessera notify TEXT [-t T]   a notification on the phone
tessera sms NUMBER TEXT      a text message from the phone
tessera send FILE...         files to the phone
tessera open [URL]           a link on the phone (the clipboard's when omitted)
tessera type TEXT [--enter]  into whatever has focus on the phone
tessera photo [PATH]         a photo with the phone's camera, saved here
tessera mic on|off           the phone as this computer's microphone
tessera timer 5m [LABEL]     a timer on the phone; tessera alarm 07:30
tessera wifi [SSID]          a saved network to the phone
tessera copy TEXT            onto this computer's clipboard
tessera ring | show | lock
```

Dolphin gets a "Send to phone" entry and links an "Open on phone" application;
Windows gets Send to → Phone (Tessera).

## Install

**Fedora:**

```bash
./scripts/build-rpm.sh
sudo dnf install ~/rpmbuild/RPMS/noarch/tessera-*.noarch.rpm
```

**Any distro** (needs `python3-pyside6`), installed into `$HOME`:

```bash
./scripts/install-user.sh          # --uninstall, --check
```

Optional tools (the app tells you the package name for your distro):
scrcpy, ffmpeg, v4l2loopback, pipewire utilities, adb, sshfs.
`./scripts/setup-fedora.sh` installs them on Fedora.

**Phone:** the Android toolchain lives in `~/android`.

```bash
./scripts/build-companion.sh       # builds and installs over adb
```

Grant permissions from the app's checklist. Install
[Shizuku](https://shizuku.rikka.app/) for the hotspot and silent audio start.

**Pairing:** tap *Show pairing code* on the phone, pick the phone in Settings
on the desktop, enter the code. The desktop pins the phone's certificate, so
the link is safe on untrusted Wi-Fi.

## Receiving LDAC

Linux has no LDAC decoder, so phones fall back to aptX. *Settings → Bluetooth
audio → Set up LDAC* builds one from `third_party/libldacdec` and a matching
PipeWire plugin into `~/.local/lib64/tessera`. From a terminal:

```bash
sudo dnf install gcc libldac-devel bluez-libs-devel
./scripts/build-ldac-decoder.sh    # --check, --uninstall
```

Rebuild after PipeWire upgrades. Details in `native/ldac-decoder/README.md`.

## Android limits

- **Hotspot:** needs `TETHER_PRIVILEGED`. Tessera calls the tethering binder
  through Shizuku's shell uid (`cmd wifi` is root-only).
- **Touch input:** needs `INJECT_EVENTS`, so control goes through scrcpy.
- **OTP SMS on Android 17:** apps targeting API 37 have them withheld, so the
  app targets 36.

## Layout

```
tessera/core       config, hub, subprocess helpers
tessera/backends   companion client, adb, bluetooth, audio, storage, ...
tessera/ui         window, sidebar, pages
android/app        service, protocol, features
native/            LDAC decoder shim
scripts/           build, install and check-*.py tests
docs/              protocol, design, Windows
```

The version lives in `packaging/tessera.spec` and is shared by the APK.
Run `python3 scripts/check-*.py` to test the desktop side without a phone, on
Linux or Windows; each system also runs checks only it can.

## Credits

LDAC Audio for Linux is adapted from [libldacdec](https://github.com/hegdi/libldacdec)
by hegdi.  
Inspired by and loosely based on [KDE Connect](https://github.com/kde/kdeconnect-kde)
by the KDE Team.  
Phone storage on Windows is adapted from [Sefirah](https://github.com/shrimqy/Sefirah)
by shrimqy.
