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

## The phone's name and its look

`hello` carries `name` and `model`, and they are not the same thing: the name
is what the phone's owner called it in the phone's own settings, the model is
the part number. The desktop shows the name and puts the model underneath.

```
desktop -> phone   {"t":"wallpaper_get","req":4}
phone   -> desktop {"t":"wallpaper","rid":4,"colour":"#8f0312","format":"jpeg","binary":true,...} + binary frame
              or   {"t":"wallpaper","rid":4,"colour":"#8f0312"}
```

The picture is optional and often absent: a live wallpaper has no still image,
the home screen's is closed to ordinary apps on recent Android, and the lock
screen's needs a permission that no longer exists to grant. The phone tries the
home wallpaper, then the lock wallpaper, and sends whichever it gets.

Where it gets neither, the desktop draws a default wallpaper rather than
anything derived from the phone: a phone tile is a picture behind a bezel, and
a flat colour in that shape does not look like a phone. The `colour` field is
still sent for completeness -- the wallpaper's own colours where the platform
reports them, otherwise the accent Android derives from the wallpaper -- but
nothing is painted with it.

## Two connections per desktop

A desktop opens its main link and, once the phone lists `file_channel` among its
capabilities, a second connection to the same address that authenticates with
the same token plus a role:

```
desktop -> phone   {"t":"auth","token":"<stored token>","role":"files"}
```

That connection subscribes to nothing and carries only file transfers. Before
it existed, a large file on the main link queued megabytes ahead of that
desktop's audio frames and notifications -- the phone's writer is one ordered
queue. On a separate socket TCP shares the network between them instead.
Files shared from the phone go down a desktop's file connection when it has
one and its main link when it does not; text shared from the phone is a
clipboard message and always takes the main link. Sefirah makes the same split.

## The phone's storage

```
desktop -> phone   {"t":"storage_start","req":7}
phone   -> desktop {"t":"reply","rid":7,"port":8766,"user":"tessera","password":"<random>",
                    "path":"/storage/emulated/0","hostKey":"ecdsa-sha2-nistp256 AAAA..."}
desktop -> phone   {"t":"storage_stop"}
desktop -> phone   {"t":"storage_grant","req":8}      # All files access, through Shizuku
```

The phone runs Apache MINA SSHD with the SFTP subsystem only -- no shell, no
exec, no forwarding -- and the desktop mounts it with sshfs, or GVfs where sshfs
is missing. The password is random per server start. The host key's public half
travels over this link, which is already authenticated against the pinned
certificate, and the desktop mounts with `StrictHostKeyChecking=yes` against a
known_hosts file containing only that key: a server on the network presenting
any other key is refused. The server is counted per desktop and stops when the
last one sends `storage_stop` or disconnects.

Capabilities: `storage` (Android 11 or later), `storage_allowed` (All files
access is granted), `storage_grant` (it is not, and Shizuku can grant it).

## File transfer

Either end may offer a file; the other accepts or refuses before any bytes
move. Chunks are 256 KiB, each a JSON header followed immediately by its binary
frame.

```
sender   -> peer     {"t":"file_offer","id":"<hex>","name":"a.pdf","size":12345,"mime":"application/pdf"}
peer     -> sender   {"t":"file_accept","id":"<hex>"}
                 or  {"t":"file_reject","id":"<hex>","message":"why not"}
sender   -> peer     {"t":"file_chunk","id":"<hex>","binary":true,"length":262144} + binary frame
                     ... repeated ...
sender   -> peer     {"t":"file_done","id":"<hex>"}
peer     -> sender   {"t":"file_saved","id":"<hex>","path":"<where it went>"}
either   -> other    {"t":"file_cancel","id":"<hex>","message":"why"}
```

Three rules make this safe rather than merely working:

* **`file_saved` is what finishes a transfer**, not `file_done`. The sender has
  only handed its last bytes to a socket at that point; treating that as
  success reported files as delivered that the receiver never wrote.
* **Nothing is loaded whole.** The sender reads a chunk at a time and stops
  when its socket has a watermark's worth outstanding; the receiver writes each
  chunk as it lands. Memory does not grow with the size of the file at either
  end.
* **A name is not a path.** The receiver takes the last component of `name` and
  strips anything that could escape its download folder, and writes to a
  temporary name until the transfer completes, so an interrupted transfer never
  looks like a finished file.

The phone's share sheet ("Share to Tessera") is an offer in the phone-to-desktop
direction; text shared this way arrives as an ordinary `clipboard` message
instead.

## Subscriptions

```
desktop -> phone   {"t":"sub","topics":["notifications","dnd","battery","status"]}
```

The phone then pushes events as they happen. Nothing is polled.

### Events

| Message | Meaning |
| --- | --- |
| `{"t":"notification","id","app","package","title","text","time","repliable","dismissable","icon"}` | Posted or updated |
| `{"t":"notification_removed","id"}` | Dismissed on either side |
| `{"t":"dnd","mode":"off\|priority\|alarms\|none"}` | Interruption filter changed |
| `{"t":"battery","level","charging"}` | Battery changed |
| `{"t":"status","battery","wifi","cell","ringer","volume"}` | Battery detail, signal and ringer changed |

`icon` is an id; fetch the bytes with `{"t":"icon_get","icon":"<id>"}`, which
replies with a JSON header plus a binary PNG frame.

`status` is what the device panel's complications and battery block read, sent
once on subscribe and then only when something in it changes:

```json
{"t": "status",
 "battery": {"level": 18, "charging": false, "status": "discharging",
             "source": "", "health": "good", "temperature": 31.4,
             "current": -842, "toFull": 0},
 "wifi": {"connected": true, "level": 3, "max": 4},
 "cell": {"operator": "Jio", "level": 3, "max": 4, "type": "5G"},
 "ringer": "vibrate", "volume": 62}
```

Every field is optional. The phone leaves out what the platform refuses --
network type needs `READ_PHONE_STATE`, which the user may have declined -- and
the desktop shows what it has. `current` is milliamps and signed; `toFull` is
milliseconds; `temperature` is degrees Celsius. `level`/`max` are the
platform's own signal scale rather than a percentage, because that is what a
status bar draws. It supersedes `battery`, which only KDE Connect still
sends.

`ringer_set` changes the mode rather than reporting it. Going silent needs the
same notification policy access Do Not Disturb uses, so the phone answers with
an error rather than throwing when that has not been granted. The change is
announced by the `status` frame the ringer broadcast triggers, so a caller does
not have to ask again.

### Commands

| Message | Effect |
| --- | --- |
| `{"t":"notif_dismiss","id"}` | Dismiss on the phone |
| `{"t":"notif_reply","id","text"}` | Inline reply |
| `{"t":"dnd_set","mode"}` | Set the interruption filter |
| `{"t":"ringer_set","mode":"normal\|vibrate\|silent"}` | Set the ringer; answered with an error if refused |
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
