# Windows

Same app, companion and protocol. Linux-only pieces are hidden with a reason
(`core/platform.py`).

## Works

- Pairing, notifications and replies, passcodes, messages, calls, photos
- Popups through the tray
- Clipboard (Shizuku, accessibility or `adb.exe`)
- File transfer and share sheet (saved to the Downloads known folder)
- Phone audio over the link
- Sidebar name and wallpaper, battery, signal, ringer, phone DND
- Hotspot, joined with `netsh`
- Screen mirroring and app windows (needs scrcpy and adb)
- Start at login (`HKCU\...\CurrentVersion\Run`)

## Missing

| Feature | Why | Path |
| --- | --- | --- |
| Bluetooth audio from the phone | Not written | `AudioPlaybackConnection` (Windows 10 2004+) |
| Webcam | Not written | `MFCreateVirtualCamera` (Windows 11 22H2+, admin once) |
| Phone storage | Not written | WinFsp + SSHFS-Win's `sshfs.exe`, keeping host-key pinning |
| Reply in popups, media flyout | Not written | WinRT toasts (needs AppUserModelID), SMTC |
| Desktop → phone DND | No API for Focus Assist | — |
| KDE Connect | D-Bus only | Companion covers it |

See `PHONE_AUDIO_AND_CAMERA.md` for the audio and camera plans.

## Install

Run `Tessera-<version>-setup.exe` (per user, no admin) or unzip
`Tessera-<version>-win64.zip`. Optional tools:

```
winget install --exact --id Genymobile.scrcpy
winget install --exact --id Google.PlatformTools
```

Settings: `%APPDATA%\Tessera\config.json`. Logs and icons: `%LOCALAPPDATA%\Tessera\`.

## Build

With Python 3.11+:

```powershell
.\scripts\build-windows.ps1
```

Produces `dist\Tessera\Tessera.exe`, a zip, and an installer if Inno Setup is
installed. The PyInstaller spec is `packaging/windows/tessera.spec`; unused Qt
modules are excluded. Icons are drawn by `ui/glyphs.py` since Windows has no
icon theme.

## Testing

Only simulated, via `TESSERA_PLATFORM=windows`:

```
python3 scripts/check-platform.py
```

Covers paths, the feature table, package advice, registry writes, `netsh`
parsing and file handling. PyInstaller output, real toasts, `netsh wlan
connect` and frozen-build adb/scrcpy need a Windows machine.
