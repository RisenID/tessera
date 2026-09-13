# Feature review

Each feature checked for a better mechanism, preferring the companion link
(authenticated, push-based, cross-platform) over outside dependencies.

| Feature | Mechanism | Status |
| --- | --- | --- |
| Notifications, replies, OTP | `NotificationListenerService`, pushed | Keep |
| Messages, calls, photos, apps | Companion requests | Keep |
| Battery, signal, ringer | Pushed from broadcasts | Keep |
| Phone audio | Bluetooth A2DP, link capture as fallback | Keep |
| Call audio | Bluetooth HFP (call capture needs a privileged permission) | Keep |
| Do Not Disturb | Pushed from the phone; desktop via D-Bus signal | Keep |
| Hotspot | Tethering binder via Shizuku | Keep |
| Webcam (Linux) | Companion H.264 → v4l2loopback | Keep |
| Ring the phone | Companion, alarm stream | Done |
| adb reconnect | Remembered address, mDNS, companion address | Done |
| Media on the desktop | Published as an MPRIS player | Done |
| Desktop popups | Notification server with inline reply | Done |
| Clipboard | Shizuku, accessibility, or adb helper; no polling in the dark | Done |
| File transfer | Chunked, both ways, own connection, share sheet | Done |
| Phone storage | SFTP on the phone, sshfs on the desktop | Done |
| Screen mirroring | scrcpy over adb | Open |

## Open

**Mirroring over the link.** MediaProjection capture into the same H.264
pipeline as the camera would remove adb from mirroring. Input injection would
still need Shizuku. Large.
