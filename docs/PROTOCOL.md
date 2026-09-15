# Tessera protocol v1

The phone is the server; the desktop connects. The phone is always on, the
desktop is what sleeps and moves networks, so reconnecting is the desktop's job.

## Transport

- TCP + TLS 1.2+, port **8765**.
- Self-signed certificate on the phone. The desktop pins its SHA-256
  fingerprint at pairing and rejects any other.
- mDNS: `_tessera._tcp.local`, TXT `id`, `name`, `model`.

## Framing

```
uint32  length   (big endian, type + payload)
uint8   type     1 = JSON, 2 = binary
bytes   payload
```

A binary frame belongs to the JSON frame right before it, which has
`"binary": true`.

## Handshake

```
desktop -> phone   {"t":"hello","v":1,"client":"tessera-linux"}
phone   -> desktop {"t":"hello","v":1,"id":"...","name":"Galaxy S25","model":"SM-S931B"}
desktop -> phone   {"t":"auth","token":"<token>","name":"My laptop"}
phone   -> desktop {"t":"auth_ok","caps":["notifications","dnd",...]}
```

Pairing, when there is no token:

```
desktop -> phone   {"t":"pair","code":"839201","name":"My laptop"}
phone   -> desktop {"t":"pair_ok","token":"<64 hex>"}
```

`name` labels the computer in the phone's paired list, where it can be removed.
The phone refreshes it with `{"t":"computer_info","req"}`, answered with `name`.
A reconnecting computer replaces its older connection with the same token.

Codes last 60 seconds and burn after five wrong guesses; a wrong code closes the
connection. The desktop should show the certificate fingerprint for comparison
with the phone. An unauthenticated connection has 20 seconds to finish the
handshake. `caps` lists only what the user granted.

## Requests

A message with `"req": N` gets a reply with `"rid": N`. (`req`, not `id`, since
some commands use `id` themselves.) Errors: `{"t":"error","rid":N,"message":"..."}`.

## Name and wallpaper

`hello` carries the user's device name and the model. The desktop shows the name.

```
desktop -> phone   {"t":"wallpaper_get","req":4}
phone   -> desktop {"t":"wallpaper","rid":4,"colour":"#8f0312","format":"jpeg","binary":true} + JPEG
              or   {"t":"wallpaper","rid":4,"colour":"#8f0312"}
```

The image is often unavailable (live wallpapers, Android 13+ restrictions); the
desktop then draws a default. Fetched once per run, rechecked after 12 hours.

## Second connection for files

When the phone advertises `file_channel`, the desktop opens another connection
with `{"t":"auth","token":"...","role":"files"}`. It carries only file
transfers, so large files don't delay audio or notifications. Shared text from
the phone always uses the main link.

## File transfer

```
sender -> peer    {"t":"file_offer","id","name","size","mime"}
peer   -> sender  {"t":"file_accept","id"}  or  {"t":"file_reject","id","message"}
sender -> peer    {"t":"file_chunk","id","binary":true,"length"} + bytes   (256 KiB, repeated)
sender -> peer    {"t":"file_done","id"}
peer   -> sender  {"t":"file_saved","id","path"}
either -> other   {"t":"file_cancel","id","message"}
```

- Only `file_saved` completes a transfer.
- Neither side holds the whole file in memory; the sender pauses at a
  high-water mark.
- The receiver keeps only the base name and writes to a temporary file until done.

## Phone storage

```
desktop -> phone  {"t":"storage_start","req":7}
phone   -> desktop {"rid":7,"port":8766,"user":"tessera","password":"<random>",
                    "path":"/","hostKey":"ecdsa-sha2-nistp256 ..."}
desktop -> phone  {"t":"storage_stop"}
desktop -> phone  {"t":"storage_grant","req":8}
```

SFTP-only Apache MINA SSHD server, rooted at shared storage (`/` is
`/storage/emulated/0`). The desktop mounts with sshfs (or GVfs) and
`StrictHostKeyChecking=yes` against the host key received over this link.
Random password per start; the server stops when the last desktop leaves.

Caps: `storage`, `storage_allowed`, `storage_grant`.

## Subscriptions and events

```
desktop -> phone  {"t":"sub","topics":["notifications","dnd","battery","status","clipboard"]}
```

Only the listed topics are sent; `call` and `media` always are. Without
`topics`, everything. Sent again whenever the desktop's features change.

| Event | Meaning |
| --- | --- |
| `{"t":"notification","id","app","package","title","text","time","repliable","clearable","ongoing","sms","icon"}` | Posted or updated |
| `{"t":"notification_removed","id"}` | Removed |
| `{"t":"dnd","mode":"off\|priority\|alarms\|none"}` | Interruption filter |
| `{"t":"battery","level","charging"}` | Battery (KDE Connect style) |
| `{"t":"status","battery","wifi","cell","ringer","volume"}` | Panel readings |

Icons: `{"t":"icon_get","icon":"<id>"}` → header + PNG.

`status` example (every field optional):

```json
{"t": "status",
 "battery": {"level": 18, "charging": false, "status": "discharging", "source": "",
             "health": "good", "temperature": 31.4, "current": -842, "toFull": 0},
 "wifi": {"connected": true, "level": 3, "max": 4},
 "cell": {"operator": "Jio", "level": 3, "max": 4, "type": "5G"},
 "ringer": "vibrate", "volume": 62}
```

`current` mA, `toFull` ms, `temperature` °C, levels on the platform's own scale.

## Commands

| Message | Effect |
| --- | --- |
| `{"t":"notif_dismiss","id"}` | Dismiss |
| `{"t":"notif_reply","id","text"}` | Inline reply |
| `{"t":"dnd_set","mode"}` | Set interruption filter; replies `mode`, or an error when something else holds DND |
| `{"t":"ringer_set","mode":"normal\|vibrate\|silent"}` | Set ringer |
| `{"t":"sms_threads","limit"}` | Conversations |
| `{"t":"sms_messages","thread","limit"}` | Messages in a thread |
| `{"t":"sms_send","address","text"}` | Send SMS |
| `{"t":"media_list","limit","offset"}` | Photo/video index |
| `{"t":"media_get","id","thumb"}` | Header + JPEG, or the original; an error when the original is over one frame |
| `{"t":"camera_start","facing","width","height","fps"}` | Start H.264 stream |
| `{"t":"camera_stop"}` | Stop it |
| `{"t":"hotspot_panel"}` | Open tethering settings |
| `{"t":"hotspot_start","ssid","passphrase","band"}` | Start hotspot; replies with AP details and `addresses` |
| `{"t":"net_addresses"}` | Every IPv4 address the phone has |
| `{"t":"app_list"}` | Launchable apps |
| `{"t":"app_launch","package"}` | Open an app on the phone |

## Camera

`camera_start` → `{"t":"camera_started","codec":"h264","sps_pps":"<base64>"}`,
then `{"t":"camera_frame","pts","key","binary":true}` + Annex-B data per frame,
piped into ffmpeg → v4l2loopback. `{"t":"camera_stopped"}` when the phone's
camera stops by itself, so the desktop stops its decoder.

## Clipboard

| Message | Direction | Meaning |
| --- | --- | --- |
| `{"t":"clipboard","text"}` | phone → desktop | Clipboard changed |
| `{"t":"clipboard_set","text"}` | desktop → phone | Set clipboard |
| `{"t":"clipboard_get","latest"}` | desktop → phone | Read once; with `latest`, the newest of the phone and other computers, answered with `text` and `from` |
| `{"t":"clipboard_query","req"}` | phone → desktop | Ask for this computer's clipboard |
| `{"t":"clipboard_state","rid","text","copiedAt"}` | desktop → phone | Answer; `copiedAt` is ms since the epoch |

A computer's `clipboard_set` is relayed to the other connected computers, not back to the sender.

Background apps can't read the clipboard, so the first available route is used:

1. **Shizuku:** read via the shell uid, polled every 2 s while the screen is on.
2. **Accessibility service:** survives reboots. On a Copy tap or "copied"
   message it briefly adds an invisible focusable window and reads. Cap
   `clipboard_accessibility`.
3. **adb:** the desktop runs a helper from the APK as the shell user and gets
   change callbacks:

   ```
   adb shell -T 'CLASSPATH=$(pm path dev.tessera.companion | head -n 1 | cut -d: -f2) \
       exec app_process / dev.tessera.companion.shell.ClipboardHelper'
   ```

   JSON lines: `{"t":"clip","text"}` out; `{"t":"set","text"}`, `{"t":"get"}` in;
   `{"t":"ready","listening"}` on start.

The clipboard reads empty while the phone is locked; pending changes are reread
after unlock. Cap `clipboard` covers routes 1 and 2.

## Calls

| Message | Direction | Meaning |
| --- | --- | --- |
| `{"t":"call","state","name","detail","canControl"}` | phone → desktop | `ringing`, `active` or `idle` |
| `{"t":"call_state"}` | desktop → phone | Current state |
| `{"t":"call_answer"}` | desktop → phone | Answer |
| `{"t":"call_end"}` | desktop → phone | Hang up or decline |
| `{"t":"call_dial","number"}` | desktop → phone | Dial (opens dialer without `CALL_PHONE`) |
| `{"t":"calls_recent","limit"}` | desktop → phone | Call log |

Caller identity comes from the dialer's notification, since telephony
callbacks no longer include the number. Caps: `calls`, `call_control`.

## Hotspot handover

Joining the hotspot drops the old network, and the phone's new address can't be
guessed (interface names and subnets vary). So `hotspot_start` replies, once the
tether interface exists, with every address:

```json
{"addresses": [{"interface": "ap0", "address": "192.168.43.1", "tether": true},
               {"interface": "wlan0", "address": "192.168.100.13", "tether": false}]}
```

The desktop probes them all in parallel and keeps the first that connects.
IPv4 only.

## Finding the phone

In order: last working address, mDNS, default gateway, then (from the second
attempt) a sweep of local subnets on the companion port. Off-subnet addresses
are tried last. A listener is only a candidate: the pinned certificate decides,
and the token is never sent before the fingerprint matches.
