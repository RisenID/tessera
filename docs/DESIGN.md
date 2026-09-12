# Design notes

Why the interface looks and behaves as it does. This is the detail that used
to sit in the window as paragraphs of explanation; the app now says the short
version and this says the rest.

## Layout

Phone Link's arrangement, drawn with the desktop's own widgets.

A **device panel** down the left (`ui/panel.py`) and a **tab strip** across
the top of the content area (`ui/main_window.py`). Navigation lives in exactly
one place: the strip. Buttons that only opened a page were removed from the
overview when the tabs arrived, and the panel holds switches, readings and
media rather than a second set of links.

### The device panel

Top to bottom: the phone and its model, a row of complications, the link pill
with a refresh next to it, the switches as squares, battery, the notification
feed, what is playing, and the last status line.

Everything in it is about the phone, and all of it is visible on every tab --
which is the point of Phone Link's panel. The feed is why the overview has no
notifications tile any more: two copies of the same list drifted apart.

The rail **grows with the window** -- 23.5% of its width, between 300 and 480
pixels -- and what is drawn inside scales with it: icons, switches, the phone
tile, the feed avatars and the name. A fixed rail is either cramped on a small
screen or a column of 16-pixel icons on a large one. The scale is quantised to
one decimal place, because re-laying out on every pixel of a window drag would
rebuild the feed dozens of times over for no visible difference.

### The switches

Six squares, reflowing to however many fit across the rail: Do Not Disturb,
the ringer, clipboard sharing, ring the phone, the hotspot, and the phone's
camera as a webcam. Each hides itself when its feature is switched off.

- **Do Not Disturb** is the roundel (`process-stop`), not a crossed-out bell.
  A bell with a line through it reads as "notifications off", which is a
  different switch.
- **The ringer** shows the mode the phone is in and steps normal -> vibrate ->
  silent. All three, though only two were asked for: a button that can put the
  phone on silent has to be able to take it off again.
- **Ring the phone** is a bell, because the ringer square beside it shows a
  phone while the phone is on vibrate, and two phone glyphs side by side read
  as one control.
- **The hotspot** and **the camera** are actions with state, so they report
  what is happening rather than what was clicked: the hotspot tile follows
  `Hub.hotspot_joined`, the camera tile follows `Hub.camera_running`.

Which squares are showing is read from a flag on each button rather than from
`isHidden()`: before the window is first shown every child reports itself
hidden, and filtering on that left half the tiles out of the grid for good.

A hotspot is not an instant thing -- the phone brings up the AP, this computer
joins it, then the phone has to be found again on the new network -- so the
tile calls `HotspotPage.quick_toggle()` and the window brings that page
forward to report on it, rather than reimplementing the sequence in the panel.

**Complications** are the small readings in one line: Bluetooth, Wi-Fi,
cellular, ringer, battery. Each hides itself when the phone has not reported
it, so the strip never shows a value that is only a guess -- and they clear
when the phone disconnects, because a stale battery reading is worse than
none.

They are drawn with the desktop's own status icons, stepped by level the way
a status bar does it: `network-wireless-60`, `network-mobile-80-5g`,
`battery-020-charging`. Breeze draws these in steps of twenty (signal) and ten
(battery). The icons are monochrome and made for one background, so
`tinted_icon` repaints each in the colour of the text beside it; at 16px on a
dark panel the originals were invisible.

Battery detail comes from the phone in one `status` frame: charging state and
supply, time to full, current, temperature, and health when it is not good.

The feed holds the newest eight with a button to the full page beside the
count, and a line under it saying how many more are there. A rail that grows
without limit is not a glance.

### The tab strip

Five tabs -- Overview, Calls, Messages, Photos, Apps -- then **More** for the
device features (Notifications, Screen, Webcam, Audio, Do Not Disturb,
Hotspot) and the gear for Settings. Ten worded tabs in one row was unreadable.
What earns a tab is the phone's *content*; everything else is a device feature.

Apps is a tab rather than the bottom half of the Screen page, which is where
the launcher used to live: it is the one thing up there people open repeatedly,
and Phone Link puts it in the same place. Screen is now only the mirror.

A page opened from More takes the strip's last tab, so the selected tab always
names the page on screen. Selecting a page that has no tab of its own is worse
than it sounds: `QTabBar` clamps `setCurrentIndex(-1)` while tabs exist, so an
earlier attempt showed Settings with the Hotspot tab lit.

Two Qt details worth knowing, both found by looking at the rendered pixels:

- The bar takes the strip's leftover width (`addWidget(tabs, 1)`). Beside a
  stretch it collapses to its scrollable minimum -- one elided tab and two
  arrows.
- The accent underline under the selected tab is painted by `PageTabs`, not
  QSS. A `QTabBar` polished before the application stylesheet exists never
  picks up a border on its tabs, and a layout can hand the bar fewer pixels
  than a tab is tall, so the underline is clamped to the widget.

## Following the desktop, not imposing on it

`tessera/ui/theme.py` builds its colours from `QApplication.palette()`. The
accent is the platform's highlight colour, so a KDE user who picks a different
accent gets it in Tessera too. Before this the app hardcoded an indigo that
matched nothing, and forced `font-family: Inter` over whatever font the user
had chosen.

Standard controls — buttons, checkboxes, combo boxes, text fields, scrollbars,
tooltips, progress bars — are left to the platform style to draw. Restyling
them is what made the app look like a web page dropped into a window, and the
old checkbox rule set `image: none` on the checked state, so a checked box was
a plain blue square indistinguishable from an unchecked one.

What is styled is what the platform has no opinion about:

| | |
| --- | --- |
| `#Sidebar`, `#PhoneTile`, `#Quick`, `#FeedRow` | The device panel, which Qt has no concept of |
| `#TabStrip`, `#Tabs`, `#Strip` | The tab strip and its two buttons |
| `#Card`, `#CardFlat` | The blocks pages are built from |
| `#Title`, `#SectionTitle`, `#Subtitle`, `#Muted` | Semantic label roles |
| `#Primary`, `#Danger`, `#Ghost`, `#Copy` | The four buttons that carry meaning |

Card surfaces are a shade *lighter* than the window. Breeze's `Base` colour is
darker than its `Window`, so cards drawn in it looked like holes punched in the
page rather than raised blocks.

Icons come from the desktop's icon theme by freedesktop name, with an emoji
fallback for a system with no usable theme. Sizes are points relative to
`QApplication.font()`, so the app respects the user's font size.

## One-time passcodes

A code is useful wherever you are reading it, so `OtpCard` lives in
`ui/widgets.py` and three surfaces use it:

* **Overview** — the newest code, copyable without navigating anywhere.
* **Notifications** — up to three recent codes, pinned above the list.
* **Messages** — a copy button on the message that carries the code, inside
  the chain, on incoming messages only.

Codes are read from notification text rather than the SMS database. That is
not only convenient: from Android 17, an app targeting API 37 has OTP-bearing
SMS withheld from the provider for three hours, while the notification the
messaging app posts is unaffected. `core/otp.py` scores candidates on the
words around them and shows nothing below the threshold, because a wrong code
pasted into a login form is worse than no code.

## Quick actions

The row on the overview mixes two kinds of thing, so they are drawn
differently and separated. Toggles are checkable buttons and get the
platform's own checked state; actions are plain buttons. Previously a toggle
that was off looked identical to a button that just does something.

## Bluetooth audio

Music (A2DP) and calls (HFP) cannot run at once — Bluetooth carries one at a
time — which is why one page covers both and switching is an explicit action.

Connecting moves no audio. Track details and call control travel on AVRCP's
control channel, which needs no audio stream, so pairing up never interrupts
what is playing on a pair of headphones. Streaming starts only when asked for.

Which codec is used is the phone's choice among what this computer offers, and
offered everything a phone does not choose the best one — a Galaxy S25 settles
on aptX with LDAC available. So "best the phone offers" narrows the offer to
one codec plus SBC, chosen from the list the phone publishes over Bluetooth
when it connects. Forcing a codec the phone cannot manage drops it to plain
SBC, which is worse than anything it would have picked itself.

LDAC needs a decoder that no distribution ships. See
`native/ldac-decoder/README.md`.

## The hotspot

Android only lets a privileged app switch tethering on, which is why Windows'
Instant Hotspot works: Samsung ships Link to Windows as a system app. Tessera
goes through Shizuku's shell uid to reach the tethering binder. Without
Shizuku it opens the tethering panel on the phone and waits for the hotspot to
appear, then joins on its own.

Joining destroys the network the two halves were talking over, so the phone
sends every address it can be reached on before the jump and the desktop
probes them afterwards. See `docs/PROTOCOL.md`.

## Polling

Pages stop their timers while off screen — a page nobody can see has nothing
to keep up to date, and a minimised window hides them all. The Audio page used
to read Bluetooth and PipeWire state every four seconds regardless, which came
to thousands of process spawns an hour for a page nobody was looking at.
