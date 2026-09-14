# Windows

Same app, companion and protocol. What Windows cannot do is hidden with a
reason (`core/platform.py`).

## Works

- Pairing, notifications and replies, passcodes, messages, calls, photos
- Popups through the tray, named Tessera
- Clipboard (Shizuku, accessibility or `adb.exe`)
- File transfer and share sheet (Downloads known folder)
- Phone audio over the link, or over Bluetooth (`AudioPlaybackConnection`)
- Call audio over Bluetooth (`PhoneLineTransportDevice`)
- Phone storage as a drive (WinFsp + SSHFS-Win)
- Webcam, Windows 11 (`native/win-camera`)
- Battery, signal, ringer, phone DND, wallpaper
- Hotspot (`netsh`), screen mirroring and app windows (scrcpy, adb)
- Start at login

## Missing

| Feature | Why |
| --- | --- |
| Bluetooth codec choice, LDAC | Windows picks the codec; the phone reports it |
| Phone screen as a webcam | scrcpy has no Windows video sink |
| Reply in popups, media flyout | Not written (WinRT toasts, SMTC) |
| Desktop → phone DND | No API for Focus Assist |
| KDE Connect | D-Bus only |

## Bluetooth

Pair in Settings → Bluetooth & devices. Connect starts an
`AudioPlaybackConnection` (nothing moves); Play phone audio here opens it,
retrying with a fresh connection until audio flows, and resumes playback the
switch paused. Take calls here registers Tessera for the phone's hands-free
line. Forget the phone in Windows' own settings.

Volume is the phone's media volume (`volume_set`). The codec is whatever the
phone and Windows agree on; the phone reports it, and which codecs this PC
accepts (`offered`).

## Webcam

`TesseraCamera.dll` is a Media Foundation source; `tessera-camera.exe` creates
the camera and passes it NV12 frames that ffmpeg decodes from the phone. The
first start asks for administrator approval to copy the DLL to
`%ProgramData%\Tessera` and register it (Frame Server reads only HKLM).

## Phone storage

`backends/storage_win.py` runs `sshfs.exe` on the first free letter from Z:,
trusting only the key the phone sent over the paired link. The drive gets a
phone icon and the phone's name (per-user Explorer keys, removed on unmount).
Its size comes from the companion's `statvfs@openssh.com`, which MINA SSHD
lacks. Log: `%LOCALAPPDATA%\Tessera\sshfs.log`.

## Install

`Tessera-<version>-setup.exe`, per user. It installs scrcpy, adb, FFmpeg,
WinFsp and SSHFS-Win with winget, skipping any already there. With the zip:

```
winget install --exact --id Genymobile.scrcpy
winget install --exact --id Google.PlatformTools
winget install --exact --id Gyan.FFmpeg
winget install --exact --id WinFsp.WinFsp
winget install --exact --id SSHFS-Win.SSHFS-Win
```

## Build

Python 3.11+ and Visual Studio Build Tools (C++):

```powershell
.\scripts\build-windows.ps1
```

Builds and self-tests the camera component, runs PyInstaller, checks the
frozen app imports everything, and writes the zip and installer.

The companion app: JDK 21, the Android SDK in `%LOCALAPPDATA%\Android\Sdk`,
and Gradle 9.6+ unpacked in `%LOCALAPPDATA%\Programs`.

```powershell
.\scripts\build-companion.ps1            # debug, installs over adb
.\scripts\build-companion.ps1 release    # signed
```

Release signing reads `%USERPROFILE%\.gradle\gradle.properties`:
`TESSERA_KEYSTORE`, `TESSERA_KEYSTORE_PASSWORD`, optionally
`TESSERA_KEY_ALIAS` and `TESSERA_KEY_PASSWORD`. Output goes to
`%USERPROFILE%\android\build\tessera`.

## Testing

`scripts/check-*.py` run on both systems. `@only_on("windows")` and
`@only_on("linux")` groups (`scripts/sandbox.py`) run only on their system.
Not covered: a real phone streaming, calls, a real mount, a camera app
opening Tessera Camera.
