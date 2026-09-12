# Tessera

A Windows Phone Link equivalent for Fedora/KDE and a Samsung Galaxy S25:
notifications, one-time passcodes, messages, photos, Do Not Disturb, screen
mirroring, per-app windows, phone-as-webcam and one-click hotspot.

Two halves:

| | |
| --- | --- |
| `tessera/` | The Fedora desktop app (Python + PySide6/Qt6) |
| `android/` | The companion app for the phone (Kotlin) |

## Why a companion app

Driving a phone from the outside with `adb` means polling, and polling costs
battery. The companion app is event-driven: Android calls it when a notification
arrives or the interruption filter changes, so it costs essentially nothing
while idle. adb is kept only where Android genuinely leaves no alternative.

## What powers what

| Feature | How it works | Needs |
| --- | --- | --- |
| Notifications | `NotificationListenerService`, pushed live | Companion app |
| Reply / dismiss | `RemoteInput` on the original notification | Companion app |
| One-time passcodes | Parsed out of notification text, one-click copy | Companion app |
| Do Not Disturb | `onInterruptionFilterChanged` ⇄ Plasma inhibition | Companion app |
| Messages | Telephony provider; sending via `SmsManager` | Companion app |
| Photos | MediaStore, thumbnails generated on the phone | Companion app |
| Webcam | Phone encodes H.264 → ffmpeg → v4l2loopback | Companion app |
| Hotspot | Tethering binder via Shizuku's shell uid | Companion app + Shizuku |
| Calls | Telecom, with caller identity from the dialer's notification | Companion app |
| Clipboard | Polled through Shizuku's shell access | Companion app + Shizuku |
| Calls on the computer | Bluetooth HFP, computer acts as the headset | Bluetooth pairing |
| Music from the phone | Bluetooth A2DP, computer acts as the sink | Bluetooth pairing |
| Screen mirroring | scrcpy | adb |
| Single app in a window | scrcpy virtual display | adb + scrcpy 3.0+ |

KDE Connect, if you already use it, is picked up automatically as a fallback
source for notifications and battery before the companion app is paired.

## The overview

Tessera opens on a dashboard rather than a menu: quick switches along the top,
then tiles for what is playing, recent calls, notifications, messages and
photos. Each tile shows a few rows and hands off to its full page, so the things
people check constantly need no navigating.

## Bluetooth audio

Calls and music are ordinary Bluetooth profiles, so they need no companion app —
only that the phone and computer are paired. What Tessera adds is one place to
switch between them and a plain explanation when something fails.

**Connecting moves no audio.** Track details and call control travel on AVRCP's
control channel, which needs no audio stream, so pairing up does not interrupt
whatever is already playing on your headphones. Streaming starts only when you
press *Play phone audio here* or *Take calls here*, and *Stop audio* parks the
link without disconnecting. Automatic call routing exists but is off by default,
for the same reason.

The two modes are mutually exclusive because Bluetooth carries one at a time:

* **Music** (A2DP) — high quality, one direction, microphone not involved.
* **Calls** (HFP) — two directions at telephone quality, with this computer's
  microphone live. Tessera can also make the phone the default audio device for
  the duration of a call, so the audio does not land on the wrong speakers.

PipeWire enables both roles by default; you can confirm it with
`bluetoothctl show`, which lists **Audio Sink** and **Handsfree** among the
adapter's UUIDs. Track titles come from AVRCP via bluez's `mpris-proxy`, which
Tessera starts on demand — a side effect being that the phone then appears in
the desktop's own media controls too.

If connecting fails with `br-connection-key-missing`, both devices still list
the pairing but the stored key no longer matches. Forget the device on both and
pair again; Tessera offers the button when it sees that error.

### Receiving LDAC

A2DP codecs are directional: sending one needs an encoder, receiving it needs a
decoder. Sony released the LDAC encoder and never a decoder, so every
distribution can drive LDAC headphones and none can accept LDAC from a phone.
The ceiling that leaves is aptX — 44.1 kHz, 16 bit, 352 kbit/s.

PipeWire is not the obstacle: its LDAC plugin already contains a complete
decode path, compiled out for want of a library exporting `ldacBT_decode`.
`native/ldac-decoder/` supplies one, built on the clean-room decoder in
`third_party/libldacdec`.

**Settings → Bluetooth audio → Set up LDAC** does the whole thing, including
asking for the build packages through polkit, and shows the build's output as
it runs. *Remove* reverses it.

The same steps from a terminal:

```bash
sudo dnf install gcc libldac-devel bluez-libs-devel
./scripts/build-ldac-decoder.sh          # or tessera-ldac-decoder, once installed
```

Either way that builds the decoder, rebuilds PipeWire's LDAC codec plugin from
the matching release, and installs both under `~/.local/lib64/tessera` — reached
by prepending that directory to `SPA_PLUGIN_DIR` for WirePlumber, which replaces
exactly one plugin and touches nothing the package manager owns. Afterwards the
phone can negotiate LDAC: 909 kbit/s at up to 96 kHz. `--check` reports the
current state and `--uninstall` reverses all of it. Rebuild after a PipeWire
upgrade, since the plugin is tied to the release it was built from — the button
in Settings says so too.

The decoder's own output is 16-bit, so the gain is bandwidth and sample rate
rather than depth. `native/ldac-decoder/README.md` has the detail, including
round-trip verification against Sony's encoder.

## Three things Android will not let this app do

These are constraints, not omissions. Each has the best workaround built in.

**Starting a hotspot.** `TETHER_PRIVILEGED` is `signature|privileged`, so no
ordinary app can hold it — a device owner cannot be granted it either, because
`DevicePolicyManager` cannot grant signature permissions. That is why Windows'
Instant Hotspot only works on phones where the OEM preinstalls *Link to Windows*
as a privileged system app.

[Shizuku](https://shizuku.rikka.app/) solves it, but not the obvious way. Running
`cmd wifi start-softap` through Shizuku does **not** work: `WifiShellCommand`
gates every softap subcommand on the *root* uid, so as the shell user even
`cmd wifi --help` returns "Uid 2000 does not have access". The permission is not
the obstacle; the shell command handler is — `com.android.shell` does hold
`TETHER_PRIVILEGED`.

So Tessera goes straight to the tethering binder instead. Shizuku supplies that
binder with the shell uid as the caller, the permission check passes, and the
platform's own `TetheringManager` performs the marshalling — which avoids
hand-writing AIDL whose transaction codes shift between releases. Tessera also
sets the `SoftApConfiguration`, so the desktop already knows the SSID and
passphrase to join.

Verified on a Galaxy S25 running Android 17: the hotspot starts with the
requested SSID, and Wi-Fi stays connected alongside it. Shizuku must be
restarted after each reboot unless the phone is rooted; without it the app falls
back to opening the tethering panel for one tap.

**Injecting touches and keystrokes.** Screen control needs `INJECT_EVENTS`,
also signature-level. scrcpy solves this by running its server as the shell
user over adb, so mirroring and per-app windows go through scrcpy.

**Reading OTP messages from the SMS database on Android 17.** Apps targeting
API 37 have OTP-bearing SMS withheld for three hours — the broadcast is
suppressed *and* provider queries are filtered. The companion app therefore
targets API 36 (while compiling against 37), which opts out of that behaviour
entirely. Separately, the passcode panel reads **notifications**, not the SMS
database, so it keeps working regardless.

## Installing

### Desktop

The desktop half is Python and Qt, so there is nothing to compile and it runs
on any distribution. There are two ways in.

**Fedora and relatives** — build the RPM and install it:

```bash
./scripts/build-rpm.sh
sudo dnf install ~/rpmbuild/RPMS/noarch/tessera-*.noarch.rpm
```

That pulls in everything the core features need, installs the launcher and
icon, and drops in the v4l2loopback options file for the virtual camera.

**Any distribution** — install for your user, no root and no packaging:

```bash
sudo <your package manager> install python3-pyside6   # the only dependency
./scripts/install-user.sh
```

That writes a launcher to `~/.local/bin`, a menu entry, the icon, and the
WirePlumber configuration that lets this computer receive audio — all inside
`$HOME`. It runs the checkout in place, so updating is a `git pull`.
`--uninstall` removes every file it created; `--check` reports what is there.

Deliberately not pip: Fedora and Debian both mark the system interpreter as
externally managed, so `pip install --user .` fails on both without being told
to break system packages, and a virtualenv would want its own copy of Qt.

Either way, launch **Tessera** from the application menu or run `tessera`.

**Settings → Startup** has *Start Tessera when I log in* and *Start minimised
to the tray*. The first writes an XDG autostart entry, which every desktop
reads and lists in its own autostart settings, so it can be seen and undone
there too. At login rather than at boot: the app needs a session to draw in
and a tray to sit in. Starting minimised falls back to showing the window if
the desktop has no system tray, so the app can never end up running with no
way to reach it.

#### The optional extras

Four features need a program Tessera does not bundle. Each is optional, and the
app detects what is missing at runtime and tells you the command for *your*
distribution — the package names differ, and `tessera/core/packages.py` holds
the table:

| Feature | Needs | Fedora | Debian/Ubuntu | Arch |
| --- | --- | --- | --- | --- |
| Screen mirroring, app windows | scrcpy 3.0+ | `scrcpy` | `scrcpy` | `scrcpy` |
| Webcam over the companion app | ffmpeg | `ffmpeg` | `ffmpeg` | `ffmpeg` |
| Virtual camera device | v4l2loopback | `v4l2loopback` | `v4l2loopback-dkms` | `v4l2loopback-dkms` |
| Receiving Bluetooth audio | pw-dump, pw-link | `pipewire-utils` | `pipewire-bin` | `pipewire` |
| Screen mirroring, adb fallback | adb | `android-tools` | `adb` | `android-tools` |

On Fedora, `./scripts/setup-fedora.sh` enables RPM Fusion and installs them.

**v4l2loopback needs kernel headers matching your running kernel**, because it
is an out-of-tree module that rebuilds on every kernel update. On a custom
kernel the package is not `kernel-devel` — on CachyOS it is
`kernel-cachyos-lto-devel`.

#### What differs between desktops

| | |
| --- | --- |
| Light/dark theme | Read from Qt's palette, so it follows any desktop |
| Do Not Disturb → desktop | Plasma's `Inhibit`; GNOME, Cinnamon and XFCE through their own switch; dunst through `dunstctl` |
| Desktop → Do Not Disturb | Plasma announces its own toggle, so it is free there; elsewhere it is polled |
| Media keys, track info | MPRIS, which every desktop implements |
| Joining the phone's hotspot | NetworkManager. An `iwd`- or `systemd-networkd`-only system cannot join from here |
| LDAC decoder installation | Writes a systemd user drop-in; without systemd it prints the one variable to set by hand |

A desktop with no notification switch Tessera recognises — a bare compositor,
say — says so rather than pretending: Do Not Disturb still works from the
desktop to the phone, just not the other way.


### Phone

Everything the Android build needs lives under `~/android` — JDK 21, Gradle
9.7.1, SDK 37, and all build output. Nothing Gradle produces is written into
the checkout, so `git status` stays readable and there is no wrapper JAR to
keep in the tree. Build and install with:

```bash
./scripts/build-companion.sh
```

Or by hand:

```bash
source scripts/android-env.sh
cd android && gradle assembleDebug
```

The APK lands at `~/android/build/tessera/modules/app/outputs/apk/debug/app-debug.apk`.
Set `TESSERA_BUILD_DIR` to put it somewhere else.

**Both halves carry one version number**, read from `Version:` in
`packaging/tessera.spec` — the file that has to be edited for a release anyway.
Gradle picks it up as a build input, so changing it there restamps the APK;
`versionName` is the version verbatim and `versionCode` packs it into an
integer (1.10.0 becomes `1010000`). There is no second copy to keep in sync,
and a phone running an older build is visible rather than permanently claiming
to be version 1.0.

Fedora 44 ships only JDK 25, which AGP rejects, so `scripts/android-env.sh`
points `JAVA_HOME` at the JDK 21 in `~/android` rather than changing your
system Java.
The stack is AGP 9.4.0 / Gradle 9.7.1 / `compileSdk 37`; AGP 9 has built-in
Kotlin support, so there is deliberately no `org.jetbrains.kotlin.android`
plugin.

Open the app and grant, from its checklist:

- **Notification access** — notifications, replies, passcodes, Do Not Disturb
- **Do Not Disturb access** — changing the interruption filter
- **Messages, photos, camera** — the runtime permission prompts

For one-click hotspot, install [Shizuku](https://shizuku.rikka.app/) and start it
(wireless debugging needs no PC after the first time), then press *Connect to
Shizuku*.

### Pairing

1. Phone: tap **Show pairing code**.
2. Desktop: **Settings** → pick the phone (found over mDNS) or type its address.
3. Enter the six-digit code.

The desktop pins the phone's TLS certificate fingerprint at pairing and refuses
any other certificate afterwards, so the link is safe on untrusted Wi-Fi. Codes
are single-use and expire after 60 seconds.

## Design notes

* **One socket.** Control messages, photos and live video all share a single
  pinned-TLS connection — one thing to pair, firewall and reconnect.
  See [docs/PROTOCOL.md](docs/PROTOCOL.md).
* **The phone is the server.** The laptop is what sleeps and changes networks;
  reconnecting is its job.
* **Capabilities are advertised.** The phone reports only what you actually
  granted, so the desktop disables what it cannot do instead of failing later.
* **Blocking work never touches the GUI thread.** Everything shells out through
  a worker pool; long-lived children (scrcpy, ffmpeg) are supervised processes.

## Layout

```
tessera/
  core/      config, subprocess plumbing, OTP extraction, the central hub
  backends/  companion client, adb, KDE Connect, DND, webcam, hotspot, mirror
  ui/        theme, shared widgets, one module per page
android/
  app/src/main/java/dev/tessera/companion/
    protocol/  framing and the per-connection session
    net/       TLS listener, mDNS advertising
    features/  notifications, DND, SMS, media, camera, privileged shell
native/
  ldac-decoder/  Sony's decoder ABI over a clean-room LDAC decoder
third_party/
  libldacdec/      submodule: the LDAC decoder itself
  kdeconnect-kde/  submodule: reference for the protocol Tessera falls back to
```

Submodules are not needed to run or package the desktop app. The LDAC decoder
needs one of them, and nothing else does:

```bash
git submodule update --init third_party/libldacdec
```

`git submodule update --init` fetches the KDE Connect sources as well, which
are reference material for the protocol Tessera falls back to.

## Status

The desktop app runs and every page is exercised. The pieces that could be
verified against real system interfaces — Plasma DND inhibition, KDE Connect
D-Bus, NetworkManager, v4l2loopback, the framing codec, OTP extraction, config
persistence — were tested directly.

The companion app is **installed and running on a Galaxy S25 (SM-S931B,
Android 17)**. Verified on device:

* the foreground service starts and survives,
* the TLS listener comes up and completes a TLS 1.3 handshake,
* mDNS advertising works and the desktop discovers the phone automatically,
* the `hello` exchange returns the phone's name and id, the desktop pins the
  certificate fingerprint, and a wrong pairing code is rejected correctly.

Still unproven, because they need a granted permission or a user gesture:
completing a pairing, the notification and DND bridges, SMS, media, the Camera2
encoder, and Shizuku's privileged shell.

## Credits

LDAC Audio for Linux is adapted from [libldacdec](https://github.com/hegdi/libldacdec)
by hegdi.
Inspired by and loosely based on [KDE Connect](https://github.com/kde/kdeconnect-kde)
by the KDE Team.
