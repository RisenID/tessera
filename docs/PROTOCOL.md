# Tessera protocol v1

The desktop app talks to the phone over one TLS connection. The phone is the
server; the desktop connects to it. Everything below travels on that single
socket, including camera video, so there is exactly one thing to pair, one
thing to firewall, and one thing to reconnect.

## Why the phone is the server

The desktop is the component that gets suspended, moved between networks and
restarted; the phone is the component that is always on. Having the phone
listen means a laptop waking from sleep just reconnects, with no push
infrastructure and no third-party relay.

## Transport

* TCP, TLS 1.2+, default port **8765**.
* The phone generates a self-signed certificate on first run. The desktop pins
  its SHA-256 fingerprint at pairing time and refuses any other certificate
  afterwards, which is what makes the link safe on untrusted Wi-Fi.
* Discovery is mDNS: the phone advertises `_tessera._tcp.local` with TXT
  records `id`, `name` and `model`. Manual IP entry is always available.

## Framing

Every frame is:

```
uint32  length   (big endian, of type + payload)
uint8   type     1 = JSON, 2 = binary
bytes   payload
```

A binary frame always belongs to the JSON frame immediately before it, which
carries `"binary": true` and describes what the bytes are. This keeps large
payloads (photos, video) off the JSON path without a second connection.

## Handshake

```
desktop -> phone   {"t":"hello","v":1,"client":"tessera-linux"}
phone   -> desktop {"t":"hello","v":1,"id":"...","name":"Galaxy S25","model":"SM-S931B"}
desktop -> phone   {"t":"auth","token":"<stored token>"}
phone   -> desktop {"t":"auth_ok","caps":["notifications","dnd","sms","media","camera"]}
```

If the desktop has no token yet:

```
desktop -> phone   {"t":"pair","code":"839201"}     # 6 digits shown on the phone
phone   -> desktop {"t":"pair_ok","token":"<64 hex>"}
```

The token is stored on both ends; the code is valid for 60 seconds and only
while the pairing screen is open. `caps` tells the desktop which features the
user has actually granted permissions for, so the UI can disable the rest
instead of failing at the point of use.

## Requests and responses

Any desktop message may carry a `"req"` correlation id. The reply carries the
same value in `"rid"`. Requests without a `"req"` are fire-and-forget.

The key is `"req"` rather than `"id"` on purpose: several commands carry their
own `"id"` field (a media item, a notification key), and reusing that key for
correlation silently overwrites them.

Errors come back as `{"t":"error","rid":N,"message":"..."}`.

## Subscriptions

```
desktop -> phone   {"t":"sub","topics":["notifications","dnd","battery"]}
```

The phone then pushes events as they happen. Nothing is polled.

### Events

| Message | Meaning |
| --- | --- |
| `{"t":"notification","id","app","package","title","text","time","repliable","dismissable","icon"}` | Posted or updated |
| `{"t":"notification_removed","id"}` | Dismissed on either side |
| `{"t":"dnd","mode":"off\|priority\|alarms\|none"}` | Interruption filter changed |
| `{"t":"battery","level","charging"}` | Battery changed |

`icon` is an id; fetch the bytes with `{"t":"icon_get","icon":"<id>"}`, which
replies with a JSON header plus a binary PNG frame.

### Commands

| Message | Effect |
| --- | --- |
| `{"t":"notif_dismiss","id"}` | Dismiss on the phone |
| `{"t":"notif_reply","id","text"}` | Inline reply |
| `{"t":"dnd_set","mode"}` | Set the interruption filter |
| `{"t":"sms_threads","limit"}` | Conversation list |
| `{"t":"sms_messages","thread","limit"}` | One transcript |
| `{"t":"sms_send","address","text"}` | Send an SMS |
| `{"t":"media_list","limit","cursor"}` | Photo/video index, newest first |
| `{"t":"media_get","id","thumb":true}` | JSON header + binary JPEG |
| `{"t":"camera_start","facing","width","height","fps"}` | Begin H.264 stream |
| `{"t":"camera_frame", ...}` | Header for each binary video frame |
| `{"t":"camera_stop"}` | End the stream |
| `{"t":"hotspot_panel"}` | Open the tethering panel on the phone |
| `{"t":"hotspot_start","ssid","passphrase","band"}` | Start tethering; answered with the AP details and `addresses` |
| `{"t":"net_addresses"}` | `addresses`: every IPv4 the phone is reachable on |

## Camera

`camera_start` is answered with `{"t":"camera_started","codec":"h264","sps_pps":"<base64>"}`
followed by a stream of `{"t":"camera_frame","pts":N,"key":bool,"binary":true}`
frames, each followed by its Annex-B payload. The desktop pipes those bytes
straight into ffmpeg, which writes to a v4l2loopback device.

## What the phone cannot do

Enabling internet-sharing tethering needs the privileged `TETHER_PRIVILEGED`
permission, which is not available to normal apps; `startLocalOnlyHotspot`
exists but deliberately shares no internet. So `hotspot_panel` only opens the
settings panel for a single tap. Fully unattended hotspot control is available
through the optional adb path instead, and the desktop app offers both.

## Apps

| Message | Effect |
| --- | --- |
| `{"t":"app_list"}` | Launchable apps: `package`, `name`, `system` |
| `{"t":"app_launch","package"}` | Open the app on the phone's own screen |

The desktop pairs this inventory with scrcpy to open an app in its own window
on a virtual display, leaving the phone's screen free. Input injection needs the
signature-level `INJECT_EVENTS` permission, so the companion app cannot provide
control itself -- scrcpy's shell-user server does that instead.

## Clipboard

| Message | Direction | Meaning |
| --- | --- | --- |
| `{"t":"clipboard","text"}` | phone → desktop | The phone's clipboard changed |
| `{"t":"clipboard_set","text"}` | desktop → phone | Replace the phone's clipboard |
| `{"t":"clipboard_get"}` | desktop → phone | Read it once, answered with `text` |

The phone announces changes only while a desktop is subscribed. Android gives a
background app no clipboard callback and no read access at all from Android 10,
so the value is polled through Shizuku's shell access; the poll stops as soon as
the last desktop disconnects. Each side records the value it applied from the
other, so a shared value is not bounced back and forth.

Capability `clipboard` is advertised only when Shizuku is available.

## Calls

| Message | Direction | Meaning |
| --- | --- | --- |
| `{"t":"call","state","name","detail","canControl"}` | phone → desktop | Call state changed: `ringing`, `active` or `idle` |
| `{"t":"call_state"}` | desktop → phone | Ask for the state now |
| `{"t":"call_answer"}` | desktop → phone | Answer the ringing call |
| `{"t":"call_end"}` | desktop → phone | Hang up, or decline while ringing |
| `{"t":"call_dial","number"}` | desktop → phone | Place a call |
| `{"t":"calls_recent","limit"}` | desktop → phone | Recent calls from the log |

Answering and hanging up use Telecom and need only `ANSWER_PHONE_CALLS`, an
ordinary runtime permission — unusually for Tessera, no privileged access is
involved. `call_dial` reports a note rather than an error when it could only
open the dialer: without `CALL_PHONE` the number is filled in and the user
presses call.

The caller's identity comes from the dialer's own notification. Since Android
12 the telephony callbacks no longer carry the number, and the call log gains
no entry until the call ends, so during a ringing call the notification is the
only source — and one Tessera already receives.

Capabilities: `calls` when the log is readable, `call_control` when calls can
be answered.

## Surviving the jump to the hotspot

Joining the phone's hotspot destroys the network the two were talking over, and
the phone's address on the new one cannot be worked out from the desktop: the
tether interface is named differently on every vendor ROM (`ap0`, `swlan0`,
`wlan1`), its subnet is the phone's own choice, and mDNS is frequently not
carried across a soft AP. Assuming the default gateway is the phone is right
often enough to look reliable and wrong in exactly the cases that strand the
link.

So the phone says where it is while the two can still talk. `hotspot_start`
answers with

```json
{"addresses": [{"interface": "ap0", "address": "192.168.43.1", "tether": true},
               {"interface": "wlan0", "address": "192.168.100.13", "tether": false}]}
```

The phone waits for the tether interface to appear before replying, because
starting the hotspot returns before that interface exists and this reply is the
last thing the desktop hears on the old network. Tether-looking interfaces sort
first, but the desktop probes all of them -- in parallel, since a wrong address
costs a full connect timeout -- and retries while the new link's DHCP settles.
The first that accepts a TCP connection becomes the address the companion
client reconnects on, and is remembered.

Only IPv4 is sent. Reaching a phone over a soft AP on IPv6 needs a scope id the
desktop cannot infer from the address alone, so it would be noise.

`net_addresses` asks the same question on its own, for a hotspot started some
other way. Where there is no companion app at all, the desktop reads the same
list over adb with `ip -4 -o addr show`.

## Finding the phone

Three sources, tried in an order decided by what this computer can actually
reach:

1. the address that worked last time,
2. mDNS (`_tessera._tcp`, carrying the device id),
3. the default gateway -- the phone itself while on its hotspot,
4. and, from the second attempt, a sweep of this computer's own subnets for
   anything listening on the companion port.

Anything on a subnet this computer is not on goes last, whichever source it
came from. Demoted, not dropped: a routed network or a VPN can make such an
address reachable, and there is no way to tell from here.

The sweep exists because neither of the first two can be relied on. DHCP hands
the phone a different address, and Android's mDNS advertisement is fragile --
the registration belongs to the network it was made on, so it disappears on a
network change, and some access points filter multicast outright.

None of this is a trust decision. A listener on the port is an address worth
trying and nothing more: the phone is whichever one presents the certificate
pinned at pairing, and the pairing token is never sent to anything else,
because a failed fingerprint check ends the TLS handshake before the
connection is considered established. A mismatch at a guessed address is
therefore skipped quietly; only a mismatch at the address the desktop was
paired on is reported, since that one means the companion app was reinstalled
-- or something is impersonating it.
