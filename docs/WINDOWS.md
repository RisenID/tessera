# Tessera on Windows

The same app, the same companion app on the phone, the same protocol. What
differs is the handful of things that are Linux kernel or Linux desktop
machinery, and those are named here rather than left to fail.

## What works

| | |
| --- | --- |
| Pairing over Wi-Fi | Unchanged: TLS, a six-digit code, a pinned certificate |
| Notifications, replies, dismissals | Unchanged |
| Desktop popups | Through the tray, which is a real Windows toast |
| One-time passcodes | Unchanged, including the copy button in the sidebar |
| Messages (SMS/MMS) | Unchanged |
| Calls: log, answer, decline, dial | Unchanged |
| Photos and videos | Unchanged |
| Apps: the launcher, one app per window | Needs scrcpy and adb, both of which have Windows builds |
| Screen mirroring | Same |
| Clipboard sharing | Qt's clipboard, and any of the phone's three routes: Shizuku, the Accessibility switch, or adb (`adb.exe`) with nothing on the phone |
| File transfer, both ways, and the phone's share sheet | Unchanged; files land in the Downloads known folder, wherever it has been moved, and arrive with a tray toast |
| The phone's audio over the link | Qt Multimedia's audio output, which the build now keeps |
| The phone's name and wallpaper in the sidebar | Unchanged |
| The phone's hotspot | Started on the phone, joined here with `netsh` |
| Battery, signal, ringer | Unchanged: the phone reports them |
| Ringer mode, Do Not Disturb on the phone | Unchanged |
| Start at login | A value under `HKCU\…\CurrentVersion\Run`, which is what the Startup Apps page in Settings lists |

## What is missing, and why

Two of these are work outstanding, not platform limits. An earlier version of
this document said Windows could not do them at all; that was wrong, and
`docs/PHONE_AUDIO_AND_CAMERA.md` sets out the path for each.

**Playing the phone's audio through this computer.** Not written yet. Windows
10 2004 and later does have a Bluetooth A2DP *sink*, driven by an app through
`Windows.Media.Audio.AudioPlaybackConnection` — a plain WinRT API that needs no
package identity and no elevation. Until it is written, the Audio page, the
sidebar's audio switch and the LDAC decoder are absent rather than broken.

**Using the phone as a webcam.** Not written yet. Windows 11 22H2 and later can
host a virtual camera in *user mode* through `MFCreateVirtualCamera`: a COM DLL
loaded by the Frame Server, with no driver signing and no WHQL. It does have to
be registered once as an administrator, which a per-user installer cannot do on
its own, so it would be an optional extra step rather than part of the install.
Until then the Webcam page is absent, and the phone's camera can still be seen
through screen mirroring.

**Do Not Disturb in both directions.** Focus Assist cannot be set by another
program; Microsoft exposes no API for it. So the sync runs one way: when the
phone goes quiet, Tessera's own popups go quiet with it, and Windows keeps its
own setting. The Do Not Disturb page says exactly this.

**The phone's storage as a drive.** Not written yet. The phone's half is the
same SFTP server; Windows has no SFTP filesystem of its own, so it needs WinFsp
and SSHFS-Win (both free, both installable with winget) to show the phone as a
drive letter. The mount would have to keep the Linux version's one real
safeguard -- accepting only the host key the phone sent over the paired link --
which SSHFS-Win's `net use` front end does not offer, so it would drive its
`sshfs.exe` directly.

**Replying inside a popup, and media controls in the volume flyout.** Not
written yet. Both are WinRT: a toast with a text box needs the app registered
with an AppUserModelID, and the flyout's player is
`SystemMediaTransportControls`. On Windows, popups come from the tray instead,
with no reply box, and the phone's media is controlled from the Audio page.

**KDE Connect as a fallback source.** Its control interface is D-Bus. The
companion app covers the same ground and more.

Each of these is one line in `tessera/core/platform.py`, which is the only
place in the app that knows what platform it is on. The interface reads that
table: a page whose feature is impossible is never built, its tab and menu
entry are never offered, its switch never appears, and Settings shows the
reason instead of a checkbox that cannot be ticked.

## Installing

Download `Tessera-<version>-setup.exe` and run it. It installs per user under
`%LOCALAPPDATA%\Programs\Tessera` — no administrator, no UAC prompt — and
offers a desktop shortcut and start-at-login.

There is also `Tessera-<version>-win64.zip`: unpack it anywhere and run
`Tessera.exe`. Nothing is written outside `%APPDATA%\Tessera` and
`%LOCALAPPDATA%\Tessera`.

### The two optional tools

Mirroring the screen and opening a single app both use **scrcpy** over **adb**.
Tessera does not bundle them; it finds them if they are installed, looking on
PATH and then where their installers put them (the Android SDK's
platform-tools, winget's links directory, Scoop shims, Chocolatey's bin,
`%ProgramFiles%\scrcpy`). Settings names the command for whichever package
manager you have:

```
winget install --exact --id Genymobile.scrcpy
winget install --exact --id Google.PlatformTools
```

Everything else — notifications, messages, calls, photos, the hotspot, the
clipboard — needs nothing but the app.

## Where things are kept

| | |
| --- | --- |
| Settings, pairing | `%APPDATA%\Tessera\config.json` |
| Log, cached app icons | `%LOCALAPPDATA%\Tessera\` |
| Start at login | `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`, value `Tessera` |

## Building it

On a Windows machine with Python 3.11 or newer, from a checkout:

```powershell
.\scripts\build-windows.ps1
```

That makes a virtual environment under `build\venv`, installs PySide6 and
PyInstaller into it, draws the application icon from the app's own glyph code,
and produces:

```
dist\Tessera\Tessera.exe            the app, one folder
dist\Tessera-<version>-win64.zip    the same, portable
dist\Tessera-<version>-setup.exe    an installer, if Inno Setup is on PATH
```

`packaging/windows/tessera.spec` is the PyInstaller spec. It keeps five Qt
modules — Core, Gui, Widgets, Network and Svg — and excludes the rest, which is
most of Qt's size; WebEngine alone is half of it.

## Icons

Windows has no freedesktop icon theme, so `QIcon.fromTheme` returns nothing and
the app drew emoji. `tessera/ui/glyphs.py` draws what it needs instead: a fixed
set of line-art SVGs, plus the stepped families (Wi-Fi arcs, cellular bars,
battery fill) generated from a level rather than enumerated. The application
icon comes out of the same code, which is why there is no `.ico` in the
repository — `scripts/make-icon.py` draws it at build time.

## What has not been tested

Everything in this document was written on Linux, and the Windows-specific
paths have been exercised only through `TESSERA_PLATFORM=windows`, which moves
the app's idea of the platform without pretending the kernel changed:

```
python3 scripts/check-platform.py
```

That covers directories, the capability table, package-manager advice, the
registry writes (against a stub), the `netsh` parsing and profile XML (against
recorded output), the icon fallback, the popups and the interface's handling of
an impossible feature. It cannot cover PyInstaller, the installer, a real
toast, a real `netsh wlan connect`, or whether adb and scrcpy behave the same
way when driven from a frozen build. Those need a Windows machine.
