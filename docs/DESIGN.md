# Design notes

## Layout

Phone Link's arrangement: a device panel on the left (`ui/panel.py`) and a tab
strip across the top (`ui/main_window.py`). Navigation lives only in the strip;
the panel holds the phone's state, switches, the notification feed and media.

### Device panel

- Resizable in a `QSplitter` (280–720 px). Contents scale with width, quantised
  to 0.1 so dragging doesn't rebuild the feed every pixel.
- Width is saved separately for windowed (`panel.width`) and maximised
  (`panel.width_fullscreen`).
- Switches are squares chosen in Settings (`config.panel.tiles`). `TILES` in
  `ui/panel.py` is the catalogue; each needs an entry, a handler in
  `_tile_clicked` and a `TILE_LABELS` label (checked by a test).
- Stateful switches (hotspot, camera, mirror, audio) show real state from the
  hub, not the last click. The ones with multi-step flows hand off to their page.
- Tile visibility uses a flag, not `isHidden()`, which is true for every child
  before the window is first shown.
- Complications (Bluetooth, Wi-Fi, cellular, ringer, battery) hide when the
  phone hasn't reported them and clear on disconnect.
- Icons prefer `-symbolic` names. `tinted_icon` recolours line art only and
  leaves full-colour pictures alone.
- The feed shows the newest eight.

### Tab strip

Overview, Calls, Messages, Photos, Apps, then **More** for device features and
a gear for Settings. A page opened from More takes the last tab so the selected
tab always matches the page (`QTabBar` won't accept index -1).

The tab bar needs `addWidget(tabs, 1)` or it collapses beside a stretch, and
the selected-tab underline is painted by `PageTabs` because QSS on a bar
polished before the stylesheet exists is ignored.

## Theme

Colours come from `QApplication.palette()`, so the desktop accent is used.
Standard controls are left to the platform style; only app-specific surfaces
are styled:

| | |
| --- | --- |
| `#Sidebar`, `#PhoneTile`, `#Quick`, `#FeedRow` | Device panel |
| `#TabStrip`, `#Tabs`, `#Strip` | Tab strip |
| `#Card`, `#CardFlat` | Page blocks |
| `#Title`, `#SectionTitle`, `#Subtitle`, `#Muted` | Label roles |
| `#Primary`, `#Danger`, `#Ghost`, `#Copy` | Meaningful buttons |

Cards are a shade lighter than the window; Breeze's `Base` is darker and made
cards look sunken.

## One-time passcodes

`OtpCard` (`ui/widgets.py`) shows up in the sidebar, the Notifications page and
on the message carrying the code. Codes are parsed from notifications, not the
SMS database (Android 17 withholds OTP SMS from apps targeting API 37).
`core/otp.py` scores candidates by surrounding words and shows nothing below a
threshold.

## Platform

`core/platform.py` is the only place that checks the OS. It lists impossible
features with a reason; pages for those are replaced by `UnavailablePage`, and
their tabs, switches and settings are hidden. D-Bus backends import through
`backends/dbus.py`, which substitutes a disconnected bus when QtDBus is absent.

`TESSERA_PLATFORM` overrides `platform.NAME` for testing; `platform.REAL` stays
the real kernel. `scripts/check-platform.py` relies on it.

## Settings

No Save button: each control writes the config 350 ms after the last change.
`_loading` and blocked signals keep the initial fill from counting as edits.

## Bluetooth audio

A2DP (music) and HFP (calls) can't run together, so one page covers both.
Connecting never moves audio; streaming starts only on request. The PipeWire
card is switched to the A2DP profile only then, because switching it asks BlueZ
to connect A2DP, and a connected A2DP sink is where the phone sends its music.
"Play on the phone again" releases the profile and refuses it for 20 s. "Best codec"
offers one codec plus SBC from the list the phone publishes, because phones
don't pick the best when offered everything. LDAC: see
`native/ldac-decoder/README.md`.

## Hotspot

Started through Shizuku's shell uid, or the tethering panel without it. Joining
drops the shared network, so the phone sends all its addresses first and the
desktop probes them afterwards (see `PROTOCOL.md`).

## Polling

Page timers stop while the page is hidden.
