# Design notes

Why the interface looks and behaves as it does. This is the detail that used
to sit in the window as paragraphs of explanation; the app now says the short
version and this says the rest.

## Layout

Phone Link's arrangement, drawn with the desktop's own widgets.

A **device panel** down the left: which phone, whether it is connected, its
battery, its switches, and what it is playing. A **tab strip** across the top
of the content area for the pages. Settings is the gear at the end of the
strip.

Navigation lives in exactly one place. Buttons that only opened a page were
removed from the overview when the tabs arrived, and the device panel holds
switches and media rather than a second set of links. Settings is a real tab
rather than a page with no tab, because QTabBar cannot hold "nothing
selected" while tabs exist -- the first attempt showed Settings while the
strip pointed at Hotspot.

Media and the quick toggles belong to the shell, not to the overview, so they
are visible on every tab -- which is the point of Phone Link's panel.

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
| `#Sidebar`, `#Nav` | A navigation rail Qt has no concept of |
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
