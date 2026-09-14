# Phone audio and camera

## How Phone Link does it

- **Media audio:** Android playback capture over the existing connection, not
  Bluetooth, so the phone's headphones are never disturbed. scrcpy does the
  same.
- **Call audio:** Bluetooth HFP, the only option.
- **Camera:** a user-mode Media Foundation virtual camera fed over the link.

## Audio routes

| Route | Mechanism | Platforms |
| --- | --- | --- |
| Over the link | `AudioPlaybackCapture` on the phone, `QAudioSink` on the desktop | All |
| A2DP sink (Linux) | PipeWire + BlueZ, LDAC via `libldacdec` | Linux |
| A2DP sink (Windows) | `Windows.Media.Audio.AudioPlaybackConnection` | Windows 10 2004+, `backends/bluetooth_win.py` |
| Calls | Bluetooth HFP | All |

Windows A2DP: find devices with `AudioPlaybackConnection.GetDeviceSelector()`,
then `TryCreateFromId` → `StartAsync` (allows) → `OpenAsync` (audio flows) →
`Dispose`. No elevation or package identity; usable from Python via the
`winrt-Windows.Media.Audio` wheels.

### Muting the phone

Capture happens before the phone's volume stage (measured on the S25: volume 0
still captured full signal), so the phone can be muted while the desktop plays.
`MediaMute` uses `ADJUST_MUTE`, which Android releases if the process dies.

## Camera routes

- **Linux:** v4l2loopback, loaded once as root.
- **Windows 11 22H2+:** `MFCreateVirtualCamera`. A user-mode COM DLL loaded by
  Frame Server; registered once as admin under HKLM, stored in
  `%ProgramData%`, kept alive by the creating process, fed frames through
  shared memory (NV12). Seen by all Media Foundation apps.
- **Windows 10:** DirectShow (`akvirtualcamera` GPLv3 as a separate process, or
  `softcam` MIT). Not seen by Media Foundation apps.

## Windows order

1. A2DP sink: done (`backends/bluetooth_win.py`), checked against a stand-in
   for WinRT.
2. Virtual camera: companion frames → shared memory → small MIT media source in
   `native/`, registered by an optional elevated step.

Nothing here has been run on Windows.

## Sources

- [AudioPlaybackConnection](https://learn.microsoft.com/en-us/windows/apps/develop/media-playback/enable-remote-audio-playback), [AudioPlaybackConnector](https://github.com/ysc3839/AudioPlaybackConnector)
- [MFCreateVirtualCamera](https://learn.microsoft.com/en-us/windows/win32/api/mfvirtualcamera/nf-mfvirtualcamera-mfcreatevirtualcamera), [VCamSample](https://github.com/smourier/VCamSample), [VL.Video.VirtualCamera](https://github.com/bj-rn/VL.Video.VirtualCamera), [BestCam](https://github.com/OneLimeStudio/BestCam)
- [akvirtualcamera](https://github.com/webcamoid/akvirtualcamera), [softcam](https://github.com/tshino/softcam)
- [scrcpy audio](https://blog.rom1v.com/2023/03/scrcpy-2-0-with-audio/)
