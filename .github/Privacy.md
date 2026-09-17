# Privacy Policy

**Effective date:** 17 September 2026

This policy covers the Tessera companion app for Android and the Tessera
desktop app for Linux and Windows.

## In short

Tessera has no servers, no accounts and no analytics. Everything it reads from
your phone goes to a computer you paired yourself, over an encrypted connection
on your own network. Nothing is sent to us, and nothing is sent to anyone else.

## What Tessera reads, and why

Tessera reads these only while the feature that needs them is switched on, and
sends them only to the computers you have paired:

- **Notifications** — mirrored to your computer so you can read, reply to and
  dismiss them there. One-time passcodes are recognised in notification text so
  the computer can offer to copy them.
- **Messages and calls** — SMS conversations, the call log and call state, so
  you can read and send texts and answer or place calls from your computer.
- **Contacts** — matched against numbers so names appear instead of digits.
- **Calendar and alarms** — upcoming events and the next alarm, shown on the
  computer's overview. Timers and alarms you set from the computer are handed
  to your phone's clock app.
- **Photos and videos** — browsed from the computer, and copied across when you
  ask, or automatically as they are taken if you turn backup on.
- **Files and phone storage** — files you share to the computer, and, if you
  enable it, your phone's storage shown as a folder on the computer.
- **Clipboard** — what you copy on either device, shared with the other.
- **Camera and microphone** — used as a webcam or microphone for the computer,
  and for single photos you request from it. Only while that is running.
- **Phone audio** — what your phone is playing, sent to the computer to play
  there, if you start it.
- **Device status** — battery, signal, Wi-Fi, ringer mode, Do Not Disturb,
  wallpaper, device name and model, and which apps are installed, shown on the
  computer.
- **Bluetooth presence** — if you enable it, your phone advertises a beacon so
  your computer can lock itself when the phone goes out of range.

**Where it goes.** Only to the computers you paired, over your local network or
a direct connection between the two devices. Nothing goes to an external
server, a cloud service or a third party. We never receive any of it.

## What Tessera does not do

- No analytics, telemetry or crash reporting.
- No advertising, tracking or profiling.
- No accounts, and no data of yours stored anywhere we control.
- No location permissions of any kind are requested or used.
- No list of all installed packages is requested; only apps with a launcher
  icon are visible to the app, for showing names and icons.

## Permissions

### Connection and background running

| Permission | Why |
| --- | --- |
| `INTERNET`, `ACCESS_NETWORK_STATE`, `ACCESS_WIFI_STATE` | Accept a connection from your paired computer on your network. |
| `CHANGE_WIFI_MULTICAST_STATE` | Let the computer find the phone on the network (mDNS). |
| `FOREGROUND_SERVICE`, `FOREGROUND_SERVICE_CONNECTED_DEVICE` | Keep the link to your computer alive, with a visible ongoing notification. |
| `FOREGROUND_SERVICE_CAMERA`, `FOREGROUND_SERVICE_MICROPHONE`, `FOREGROUND_SERVICE_MEDIA_PROJECTION` | Required by Android while the camera, microphone or phone audio is streaming. |
| `WAKE_LOCK` | Hold a low-latency Wi-Fi lock while you use the phone as a trackpad, so pointer movement is not delayed. |
| `RECEIVE_BOOT_COMPLETED` | Start listening again after the phone restarts, so the link comes back on its own. |
| `REQUEST_COMPANION_RUN_IN_BACKGROUND`, `REQUEST_COMPANION_USE_DATA_IN_BACKGROUND` | Stay reachable as a companion device when the app is not open. |
| `POST_NOTIFICATIONS` | Show the app's own notifications: the connection status, files that arrive, and messages your computer sends to the phone. |

### Notifications

| Permission | Why |
| --- | --- |
| `BIND_NOTIFICATION_LISTENER_SERVICE` | Read notifications so they can be mirrored to your computer, and dismiss or reply to them when you do so there. |
| `ACCESS_NOTIFICATION_POLICY` | Read and set Do Not Disturb, so silencing one device can silence the other. |
| `RECEIVE_SENSITIVE_NOTIFICATIONS` | Optional, and only if you grant it through Shizuku. Android 15 and later hide one-time passcodes from notification listeners; without this they are hidden from Tessera too, and passcodes will not reach your computer. See *Optional privileged features*. |

### Messages and calls

| Permission | Why |
| --- | --- |
| `READ_SMS`, `SEND_SMS` | Show your conversations on the computer and send texts from it. |
| `READ_CALL_LOG` | Show recent calls on the computer. |
| `READ_PHONE_STATE` | Know when a call is ringing, active or ended, so the computer can show it. |
| `ANSWER_PHONE_CALLS`, `CALL_PHONE` | Answer, end and place calls from the computer. |
| `READ_CONTACTS` | Show names rather than numbers on calls and messages. |

### Calendar and alarms

| Permission | Why |
| --- | --- |
| `READ_CALENDAR` | Show what is coming up on the computer's overview. |
| `SET_ALARM` | Hand a timer or alarm you set on the computer to your phone's clock app. |

### Photos, media and files

| Permission | Why |
| --- | --- |
| `READ_MEDIA_IMAGES`, `READ_MEDIA_VIDEO`, `READ_MEDIA_VISUAL_USER_SELECTED` | Browse your gallery from the computer and copy items across. You may grant access to selected items only. |
| `READ_EXTERNAL_STORAGE` (Android 12 and earlier) | The same, on older versions of Android. |
| `MANAGE_EXTERNAL_STORAGE` | Optional. Only for showing your phone's storage as a folder on the computer, served over an SFTP connection on your local network. Without it, file transfer and the share sheet still work. |

### Camera and microphone

| Permission | Why |
| --- | --- |
| `CAMERA` | Use the phone as a webcam for the computer, and take a photo when you ask for one from there. |
| `RECORD_AUDIO` | Use the phone as the computer's microphone. Android also requires this permission to capture what the phone is playing, even though the microphone is not used for that. |

### Bluetooth

| Permission | Why |
| --- | --- |
| `BLUETOOTH_CONNECT`, `BLUETOOTH` and `BLUETOOTH_ADMIN` (Android 11 and earlier) | Report which Bluetooth audio codec is in use when your phone's audio plays on the computer. |
| `BLUETOOTH_ADVERTISE` | Optional. Advertise a presence beacon so your computer can lock itself when the phone leaves. The beacon carries a fixed identifier and nothing about you or your location. |

## Optional privileged features

Two features work by giving Tessera abilities Android does not grant apps by
default. Both are optional, both are off until you turn them on, and the app
works without either.

- **Shizuku.** If you install and start [Shizuku](https://shizuku.rikka.app/),
  Tessera can run a small number of commands with shell-level privileges: to
  switch the Wi-Fi hotspot on, to type into a focused field, to read the
  clipboard without taking focus, and to grant itself three Android app
  operations — capturing phone audio without a prompt each time, reading your
  phone's storage for the folder view, and receiving sensitive notifications so
  passcodes reach your computer. These are used for those features only.
- **Accessibility service.** If Shizuku is not available, you can switch
  Tessera on under Accessibility instead. It is used for two things: noticing
  when you copy something, so the copied text can be sent to your computer; and
  finding the focused text field when you type into the phone from your
  computer. It reads window content only for that, and never sends the contents
  of your screen anywhere.

Neither is an accessibility aid, and the app declares itself as such. If you do
not want either, leave both off; clipboard sharing and typing are then the only
features that will not work.

## Security

- The phone is the server and the computer connects to it. Every connection
  uses **TLS 1.2 or later**, restricted to forward-secret cipher suites
  (ECDHE, AES-GCM and ChaCha20-Poly1305).
- The phone's identity is a self-signed certificate whose private key is
  generated in the **Android Keystore** and never leaves the device.
- **Pairing** uses a six-digit code shown on the phone. It expires after 60
  seconds and is invalidated after five wrong guesses. Your computer pins the
  phone's certificate fingerprint at pairing and refuses to connect to anything
  presenting a different one, so the link is safe on an untrusted network.
- Each paired computer holds its own token, which you can revoke from the phone
  at any time; doing so disconnects it immediately.
- The phone's storage, when you enable it, is served over SFTP with a fresh
  random password for each session, reachable only on your local network, and
  rooted at shared storage so the app's own files and pairing tokens are out of
  reach.

## Data retention

Nothing is retained on any server, because there is no server. On the phone the
app stores only what it needs to work: the pairing tokens and names of your
computers, its own device identifier and settings. Notifications, messages,
clipboard contents and media pass through memory as they are used and are not
kept. Removing a paired computer deletes its token; uninstalling the app
removes everything it stored.

## Your choices

Every feature is individually optional, on both the phone and the computer.
Revoke any Android permission at any time and the feature that used it stops,
while the rest keeps working. Remove a paired computer from the phone's list to
cut it off. Because nothing of yours is held anywhere we control, there is no
account to close and no data for us to export or delete on your behalf.

## Children

Tessera is a utility for pairing your own devices and is not directed at
children.

## Changes

If this policy changes, the effective date above changes with it, and the
history of this file records exactly what changed.

## Contact

Questions about this policy or about how Tessera handles your data:

- **Email:** me@risenid.in
- **Issues:** https://github.com/RisenID/tessera-priv/issues

---

_Last updated: 17 September 2026_
