# Every feature, and whether there is a better way

Written after the audio work, which started from the same question and found
that the route the app was using (Bluetooth A2DP) was the wrong one: the phone
already had a connection to this computer, and sending a copy of its mix over
that connection is better in every way that matters. This is that question
asked of everything else.

Each entry says what the feature does today, whether a better mechanism exists,
and what it would cost. Verdicts are **keep**, **improve**, or **replace**.

The recurring test: *is this being done through an outside dependency when the
companion link could carry it?* — the link is already authenticated, encrypted,
push-based and cross-platform, and every feature carried on it works on Windows
for free.

---

## The scoreboard

| Feature | Today | Verdict |
| --- | --- | --- |
| Notifications, replies, OTP | Companion, push | **keep** |
| Messages, calls (control), photos, apps list | Companion | **keep** |
| Battery, signal, ringer | Companion, push | **keep** |
| Phone audio | Companion link (new) | **keep** |
| Call audio | Bluetooth HFP | **keep** — no alternative exists |
| Do Not Disturb (phone) | Companion, push | **keep** |
| Do Not Disturb (desktop) | Subscribed; the timer is a fallback | **keep** — the review had this wrong |
| Hotspot | Shizuku / tethering binder | **keep** |
| Webcam | Companion frames → v4l2loopback | **keep** on Linux |
| ~~Clipboard polled with the screen off~~ | Gated on the screen being on | **done** |
| ~~Ring the phone~~ | Companion, alarm stream | **done** |
| ~~adb never reconnects~~ | Remembered address, mDNS, the companion's own | **done** |
| ~~Now playing on the desktop~~ | Published as our own MPRIS player | **done** |
| ~~Desktop popups~~ | The desktop's server, with inline reply | **done** |
| Screen mirroring, app windows | scrcpy over adb, reconnecting on its own | **partly done** — over the link is the remaining half |
| **File transfer** | absent | **add** — the link already carries binaries |

---

## Replace

### Ring the phone — currently needs KDE Connect

`panel._ring()` calls `hub.kdeconnect.ring()`, so the tile only works if the
user has KDE Connect installed *and separately paired* with the same phone, and
it cannot work on Windows at all — KDE Connect is reached over D-Bus.

The companion app can ring the phone itself in about thirty lines: play a tone
with `USAGE_ALARM` over the alarm stream, which sounds even when the ringer is
silenced, and stop on a second command or a tap on the notification. That is
what "find my phone" means, and it is the one feature in the app that still
depends on a second app being installed.

**Cost:** small, both ends. **Benefit:** one fewer dependency, works on Windows,
works when the phone is silenced.

---

## Improve

### Now playing: read it from Bluetooth, but publish it to the desktop

`backends/mpris.py` *reads* MPRIS — BlueZ's `mpris-proxy` republishes the
phone's AVRCP player on the session bus, so the phone appears in Plasma's media
applet only while it is connected over Bluetooth.

The companion link already delivers the same information (`NowPlaying`), and
now it delivers the audio too. The missing half is publishing: an MPRIS player
of our own, fed by the companion, would put the phone in the desktop's media
applet and under the keyboard's media keys with no Bluetooth at all. It is the
exact shape of the audio fix — the data is already here, the desktop
integration is what is missing — and it pairs with it: audio playing here while
the desktop's own media controls do nothing is a gap the user will notice.

**Cost:** medium; an MPRIS server is about 200 lines of D-Bus.
**Benefit:** phone media in the desktop's own controls, no Bluetooth.
**Windows:** the same idea is SMTC, and worth doing only after Linux.

### Desktop popups: a balloon with nothing to press

`ui/popups.py` raises notifications through `QSystemTrayIcon.showMessage`,
chosen because it is the one route Qt has on every platform. It shows text and
nothing else — no reply box, no "mark as read", no per-app mute, no icon of the
app that sent it.

The phone side is already capable: `NotificationBridge.reply` sends an inline
reply through `RemoteInput`, and the desktop already uses it from the
Notifications page. The gap is only in the popup. On Linux,
`org.freedesktop.Notifications` gives actions and, where the server advertises
`inline-reply` (KDE and GNOME both do), a reply field directly in the popup —
which is the thing Phone Link's popups are actually good at. On Windows the
same needs a toast with an AppUserModelID registered by the installer.

**Cost:** medium on Linux, larger on Windows. **Benefit:** replying without
opening the app, which is most of what a phone-link app is for.

### Screen mirroring and app windows: adb is the weak link

scrcpy over adb is the right *renderer* — low latency, hardware decode, input
injection, virtual displays for one-app windows (already used, scrcpy 3.0+).
The problem is adb itself: wireless debugging stops after a reboot, and
`hub.refresh_adb` only ever looks for an already-attached device. It never
tries to reconnect, so mirroring silently stops working and the user is left to
run `adb connect` by hand. (That happened during this session.)

Two levels of fix:

1. **Reconnect automatically** — the companion link already knows the phone's
   addresses (`net_addresses`), and `adb.enable_tcpip` already exists for while
   the cable is in. When adb has no device but the companion is connected, try
   `adb connect <address>:5555`, and offer a one-time "set this up over USB"
   step. **Small, and it fixes a real recurring failure.**
2. **Mirror over the link** — the companion already encodes H.264 with
   MediaCodec for the camera; screen capture through MediaProjection is the
   same pipeline with a different source, and it is how Phone Link does it. It
   would remove adb from mirroring entirely. The catch is input: touch and key
   injection need shell privileges, so it would go through Shizuku, which the
   app already uses for the hotspot and the clipboard. **Large — an
   audio-sized project — and the honest order is (1) now, (2) later.**

### Do Not Disturb: the review was wrong about this one

I wrote that the desktop's state was polled every five seconds and should be a
subscription. It already is one: `DndSync` connects to `PropertiesChanged` for
the notification server's `Inhibited` property in its constructor, and reacts
the moment the tray toggle is used. The five-second timer is a fallback for two
things that genuinely have no signal — a desktop whose server does not publish
`Inhibited`, and reading the phone over adb when the companion app is not
connected to push it. Both are gated: the adb read is skipped entirely while
the phone is pushing.

Nothing to change.

### Clipboard: polled on the phone, as it must be — but not in the dark

`ClipboardWatcher` polls every two seconds, and the comment explains why
correctly: Android gives a background app no clipboard callback, and reads are
restricted, which is also why Shizuku is required. There is no event to
subscribe to.

There was no point polling while the phone's screen was off, though: nothing
can copy while nobody is there. The poll is now gated on the screen state,
which a receiver keeps current, and it reads once when the screen comes back on
in case something was copied on the lock screen. **Done.**

---

## Add

### File transfer

There is none. Photos come *from* the phone through `MediaRepository`, and
nothing goes the other way. The link already carries binary frames in both
directions (photos, camera frames, audio), so the transport exists — what is
missing is a "send to phone" action, a drop target on the window, and a
notification on the phone when something arrives.

**Cost:** medium. **Benefit:** the obvious missing verb; Phone Link has
drag-and-drop both ways.

---

## Keep, and why

**Notifications, replies, one-time passcodes** — a `NotificationListenerService`
pushing over the link is the best available mechanism on Android; there is no
better one. Replies go back through `RemoteInput`, which is what the phone's own
notification shade uses.

**Messages, call control, the app list, photos** — all companion, all push or
request/reply over the link, none of them polling. Nothing to improve.

**Battery, signal strength, ringer mode** — pushed from `ACTION_BATTERY_CHANGED`
and the matching listeners, snapshot on subscribe. Correct as built.

**Call audio over Bluetooth HFP** — this one genuinely cannot move to the link.
Capturing a call's audio needs `CAPTURE_AUDIO_OUTPUT`, which is a privileged
permission no ordinary app can hold, and playback capture explicitly excludes
`USAGE_VOICE_COMMUNICATION`. HFP is how Phone Link does calls too, for the same
reason. Keep — and keep it separate from the music path, which now does not
need Bluetooth at all.

**Hotspot** — the tethering binder with a `cmd wifi` fallback, both through
Shizuku, because Android has no public API for starting a hotspot with a chosen
SSID. There is no better route; the desktop's own half is NetworkManager on
Linux and `netsh` on Windows.

**Webcam on Linux** — companion frames into v4l2loopback via ffmpeg. The module
needs root once to load, which is the only privileged step in the app, and
there is no alternative every application would see. (Windows needs a different
mechanism entirely: see `docs/PHONE_AUDIO_AND_CAMERA.md`.)

**Pairing** — mDNS discovery, TLS with a pinned self-signed certificate, a
six-digit code. Nothing here should change.

---

## Suggested order

1. **adb auto-reconnect** — small, fixes a failure that happens in normal use.
2. **Ring from the companion** — small, drops the last KDE Connect dependency.
3. **Desktop notifications with reply** — the biggest day-to-day gain.
4. **Publish MPRIS** — completes the audio work.
5. **Screen-on gating for the clipboard poll, DND by subscription** — tidy-ups.
6. **File transfer** — a new feature rather than a better way.
7. **Mirroring over the link** — the next large project, if adb should go
   entirely.
