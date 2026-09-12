# The phone's audio and the phone's camera: what the paths actually are

Written after checking, rather than assuming. Two entries in the Windows
capability table used to say these features were impossible there. Both claims
were wrong, and the table now says "not written yet" with the path named.

## How Phone Link does it

Microsoft documents none of this as protocol, so the transport is inference;
the pieces each end uses are not.

**Media and app audio: not Bluetooth.** Phone Link's "hear audio on this PC or
on my phone" switch belongs to the app-streaming session — the audio rides
inside the same stream as the mirrored screen, produced on the phone by
Android's own playback capture. This is the same route scrcpy has taken since
2.0. Nothing about it involves A2DP, and so nothing about it disturbs a pair of
headphones already connected to the phone.

**Call audio: Bluetooth, because it has to be.** Calls use the Hands-Free
Profile with the PC in the hands-free role. That is why headsets misbehave
during Phone Link calls — HFP is a one-device-at-a-time link, and the PC is
competing for it.

**The camera: a real virtual camera.** "Use your Android phone as a connected
camera" (Windows 11, Link to Windows 1.24012 or newer, Android 9 or newer)
takes frames over the phone link and publishes them through Cross Device
Experience Host as a camera every video application can select. No driver is
installed, which places it on the user-mode Media Foundation virtual camera API
below.

**The lesson.** Phone Link uses Bluetooth only where the platform leaves no
choice. Everything else travels on the connection it already has to the phone.
Tessera owns both ends of exactly such a connection, so the same path is open
to it — and on Linux it would be an improvement on what we do today.

## Audio: the four paths

| Path | What it is | Licence | Where |
| --- | --- | --- | --- |
| The app's own channel | Android `AudioPlaybackCapture` (API 30+) encodes audio on the phone; the desktop decodes and plays it | ours, or scrcpy Apache-2.0 | everywhere |
| Windows A2DP sink | `Windows.Media.Audio.AudioPlaybackConnection` | Windows | Windows 10 2004+ |
| Linux A2DP sink | PipeWire + BlueZ, with LDAC decoded by the vendored `libldacdec` | in tree | Linux |
| Calls | Bluetooth HFP; PipeWire/mSBC on Linux, a "Hands-Free AG Audio" endpoint on Windows | platform | everywhere |

**The app's own channel** is the best fit for the rule this project has kept
throughout: connecting must never pull audio away from the headphones. There is
no profile to switch, no A2DP to negotiate, nothing for the phone's audio
routing to notice. scrcpy 4.1 does it today with `--audio-source=playback`, and
`--audio-dup` keeps the sound playing on the phone as well. It needs Android 11
or newer, which the S25 comfortably is.

**Windows A2DP sink** is real and has been since Windows 10 2004 (SDK 19041).
Windows carries the sink; an application decides when it is open. The sequence
is short:

* `AudioPlaybackConnection.GetDeviceSelector()` into a `DeviceWatcher` to find
  paired devices that can send audio;
* `TryCreateFromId(id)` then `StartAsync()` — this *allows* a connection and
  starts nothing;
* `OpenAsync()` — this is the moment audio begins arriving;
* `Dispose()` to give it back.

That `Start` / `Open` split is the headphone rule expressed as an API: pairing
and watching cost nothing, and audio only moves when something asks for it.

No package identity is needed and no elevation: `AudioPlaybackConnector` (MIT)
is a plain tray executable doing precisely this. From Python it is
`pip install winrt-Windows.Media.Audio winrt-Windows.Devices.Enumeration`,
which ship wheels for 3.11 through 3.14 — so this is Python work in
`backends/audio_win.py`, with no native code at all.

## The camera: three paths

**v4l2loopback** — GPL-2, a kernel module, what we already use on Linux. Root
once to load it, then every application sees an ordinary webcam. Nothing better
exists there; a PipeWire video node avoids the module but only newer browsers
and OBS would find it.

**Media Foundation virtual camera** — `MFCreateVirtualCamera`, Windows 11
22H2 (build 22621) and later. This is the one that matters, and it is *not* a
driver:

* a user-mode COM DLL that the Frame Server service loads — no signing, no
  WHQL, no Windows Update;
* the resulting camera is a first-class device: Device Manager lists it, camera
  privacy settings apply to it, and Media Foundation applications see it, which
  DirectShow filters never manage — the Windows Camera app and Teams included;
* it must be registered with `regsvr32` under HKLM, which needs an
  administrator **once**. A per-user installer cannot do that, so this belongs
  behind an optional "set up the virtual camera" step, not in the install;
* the DLL has to sit where `LocalService` can read it — `%ProgramData%` or
  Program Files, never a user profile directory;
* the process that created the camera must stay alive. If it dies the camera
  serves black frames until it returns;
* frames can come from another process, and the established way is named shared
  memory in the `Global\` namespace with a seqlock or mutex: `VL.Video.Virtual`
  `Camera` (MIT) uses a ring buffer of BGRA slots, and `BestCam` (GPL-2) — an
  Android-phone webcam, the same job as ours — documents its NV12 layout field
  by field. NV12 is the camera pipeline's native format, so producing it avoids
  a conversion inside the source.

**DirectShow filters** — `akvirtualcamera` (GPLv3) and `softcam` (MIT). These
work on Windows 10, and browsers, Zoom, Discord and OBS see them; Media
Foundation applications do not. `akvirtualcamera` is interesting for a different
reason: `AkVCamManager stream <device> RGB24 <w> <h>` reads raw frames on
standard input, which is a working virtual camera for the cost of a pipe and no
native code. GPLv3 against this project's MIT means driving it as a separate
program, never linking or bundling it — the arrangement we already have with
scrcpy and v4l2loopback.

**Where the frames come from.** scrcpy 4.1 can capture the phone's camera
directly (`--video-source=camera --camera-id=…`), and on Linux `--v4l2-sink`
closes the loop with no code of ours in the middle. Windows has no sink flag,
so either the frames come out of scrcpy — its recording target is handed to
FFmpeg's avformat, so `--record=pipe:1 --record-format=mkv` may well work, and
I could not test it with no device attached — or the companion app sends camera
frames on the socket it already holds, which is what Phone Link does and what
keeps the desktop side identical on both platforms.

## Suggested order

1. **Audio over the companion channel.** Cross-platform, the largest gain, the
   best fit for the headphone rule, and testable here on Linux against the S25.
   Kotlin: playback capture, encode, send. Desktop: decode and play.
2. **Windows A2DP sink.** Small, pure Python, and it brings Windows level with
   what Linux does today for a phone that is merely paired.
3. **Windows camera.** Companion camera frames into a shared-memory buffer read
   by a small MIT media source in `native/`, registered by an optional elevated
   step. DirectShow only if Windows 10 has to be covered.

## What was checked, and how

Verified: the API surface, versions and licences above, from Microsoft's own
documentation and each project's repository; and scrcpy 4.1's flags on this
machine. Not verified: any of it running on Windows, `--record=pipe:1` (no adb
device was attached), and Phone Link's internal transport, which Microsoft does
not publish — the camera being an MF virtual camera is inference from Cross
Device Experience Host, from its working in any video application, and from no
driver being installed.

## Sources

* [Enable audio playback from remote Bluetooth-connected devices](https://learn.microsoft.com/en-us/windows/apps/develop/media-playback/enable-remote-audio-playback) — `AudioPlaybackConnection`
* [AudioPlaybackConnector](https://github.com/ysc3839/AudioPlaybackConnector) — MIT, unpackaged, Windows 10 2004+
* [winrt-Windows.Media.Audio](https://pypi.org/project/winrt-Windows.Media.Audio/) — the Python projection
* [MFCreateVirtualCamera](https://learn.microsoft.com/en-us/windows/win32/api/mfvirtualcamera/nf-mfvirtualcamera-mfcreatevirtualcamera) and [VCamSample](https://github.com/smourier/VCamSample) — MIT sample, registration and Frame Server access
* [VL.Video.VirtualCamera](https://github.com/bj-rn/VL.Video.VirtualCamera) — MIT, shared-memory frames from another process
* [BestCam](https://github.com/OneLimeStudio/BestCam) — GPL-2, an Android webcam with a documented NV12 shared-memory layout
* [akvirtualcamera](https://github.com/webcamoid/akvirtualcamera/wiki/Usage-and-examples) — GPLv3, frames on standard input
* [softcam](https://github.com/tshino/softcam) — DirectShow with a sender API
* [scrcpy 2.0, with audio](https://blog.rom1v.com/2023/03/scrcpy-2-0-with-audio/) and [scrcpy](https://github.com/Genymobile/scrcpy) — Apache-2.0
* [Using a mobile device's camera as a webcam](https://blogs.windows.com/windows-insider/2024/02/29/ability-to-use-a-mobile-devices-camera-as-a-webcam-on-your-pc-begins-rolling-out-to-windows-insiders/) — the Phone Link feature
