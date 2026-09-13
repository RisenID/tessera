%global appid dev.tessera.Tessera

# Resolve the site-packages path without requiring python3-devel just to build
# a pure-Python package. When the usual Python RPM macros are present they win.
# base must be pinned to /usr: Fedora patches sysconfig so that an unprefixed
# install resolves to /usr/local, which is the wrong place for packaged files.
%{!?python3_sitelib: %global python3_sitelib %(%{__python3} -c "import sysconfig; print(sysconfig.get_path('purelib', vars={'base': '/usr', 'platbase': '/usr'}))")}
%{!?__python3: %global __python3 /usr/bin/python3}

Name:           tessera
Version:        1.10.0
Release:        22%{?dist}
Summary:        Android phone companion: notifications, messages, photos, screen and webcam

License:        GPL-3.0-only
URL:            https://github.com/risen/tessera
Source0:        %{name}-%{version}.tar.gz

BuildArch:      noarch
BuildRequires:  python3
BuildRequires:  desktop-file-utils

# The desktop half is pure Python; everything it drives is a separate tool.
Requires:       python3 >= 3.11
Requires:       python3-pyside6
# adb, used for screen mirroring and as a fallback transport.
Requires:       android-tools
# Joining the phone's hotspot.
Requires:       NetworkManager
# avahi-browse, used to discover the phone on the local network.
Requires:       avahi-tools
# pkexec, used once to load the virtual-camera kernel module.
Requires:       polkit
# gdbus, which raises the desktop notifications. Not a stylistic choice: the
# specification's replaces_id is an unsigned 32-bit integer and PySide6 sends
# every Python int as a signed one, so QtDBus cannot make the call at all.
# glib2 is present on any system with a notification server; naming it means a
# stripped one fails at install rather than falling back to a tray balloon.
Requires:       glib2

# Weak dependencies: each unlocks one feature, and several live in RPM Fusion
# or a COPR, so a missing one must not block installation. The app detects each
# at runtime and explains what to install.
Recommends:     ffmpeg
Recommends:     v4l2loopback
Recommends:     scrcpy
# bluetoothctl and mpris-proxy, for calls and music over Bluetooth.
Recommends:     bluez
# pactl, for switching between the music and call profiles. It lives in
# pulseaudio-utils even on a PipeWire system.
Recommends:     pulseaudio-utils

# pw-dump and pw-link, for finding the node the phone's audio arrives on and
# connecting it to the speakers. Received Bluetooth audio is not a source, so
# pactl alone cannot see it.
Recommends:     pipewire-utils

# Building the LDAC decoder with tessera-ldac-decoder. Only suggested: it is an
# opt-in step, and nothing else in the package needs a compiler.
Suggests:       gcc
Suggests:       libldac-devel
Suggests:       bluez-libs-devel

%description
Tessera connects an Android phone to a Linux desktop.

It mirrors notifications as they arrive and lets you reply to them, surfaces
one-time passcodes for copying with one click, reads and sends SMS, browses the
phone's photo library, mirrors the screen or an individual app in its own
window, presents a phone camera as an ordinary webcam, keeps Do Not Disturb in
step between phone and desktop, and starts the phone's hotspot.

Most features need the Tessera companion app on the phone, which pushes events
rather than being polled. Screen control additionally needs adb, because
injecting input requires a permission Android does not grant ordinary apps.

%prep
%autosetup -n %{name}-%{version}

%build
# Nothing to compile here: the application is pure Python. Byte-compilation
# happens during %%install so the package does not depend on the byte-compile
# build-root policy being available.

%install
install -d %{buildroot}%{python3_sitelib}/tessera
cp -a tessera/. %{buildroot}%{python3_sitelib}/tessera/

# Byte-compile with the build root stripped from the recorded paths, so
# tracebacks point at the installed location rather than the build tree.
%{__python3} -m compileall -q -s %{buildroot} %{buildroot}%{python3_sitelib}/tessera

install -d %{buildroot}%{_bindir}
cat > %{buildroot}%{_bindir}/tessera <<'EOF'
#!/usr/bin/python3
import sys

from tessera.app import main

if __name__ == "__main__":
    sys.exit(main())
EOF
chmod 0755 %{buildroot}%{_bindir}/tessera

desktop-file-install \
    --dir=%{buildroot}%{_datadir}/applications \
    packaging/%{appid}.desktop

install -Dm0644 packaging/%{appid}.metainfo.xml \
    %{buildroot}%{_metainfodir}/%{appid}.metainfo.xml

install -Dm0644 packaging/icons/%{appid}.svg \
    %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/%{appid}.svg

# Virtual-camera module options. Placed here rather than written at runtime so
# that uninstalling the package removes it again.
# WirePlumber needs the receive-side Bluetooth roles enabled before a phone
# can stream to this computer; without them the only profile offered is the
# one that sends audio the other way.
install -Dm0644 packaging/51-tessera-bluez.conf \
    %{buildroot}%{_datadir}/wireplumber/wireplumber.conf.d/51-tessera-bluez.conf

install -Dm0644 packaging/tessera-v4l2loopback.conf \
    %{buildroot}%{_prefix}/lib/modprobe.d/tessera-v4l2loopback.conf

# The LDAC decoder is shipped as source because it has to be compiled against
# whichever PipeWire release the machine is running -- libspa-bluez5.so refuses
# a codec plugin built against a different ABI version. tessera-ldac-decoder
# does that, installs the result under the user's home, and can undo it.
install -d %{buildroot}%{_datadir}/tessera/ldac-decoder
install -m0644 native/ldac-decoder/ldacBT_dec.c native/ldac-decoder/README.md \
    %{buildroot}%{_datadir}/tessera/ldac-decoder/
install -d %{buildroot}%{_datadir}/tessera/ldac-decoder/libldacdec
install -m0644 third_party/libldacdec/*.c third_party/libldacdec/*.h \
    third_party/libldacdec/LICENSE \
    %{buildroot}%{_datadir}/tessera/ldac-decoder/libldacdec/
install -m0755 scripts/build-ldac-decoder.sh \
    %{buildroot}%{_datadir}/tessera/ldac-decoder/build-ldac-decoder.sh
ln -s ../share/tessera/ldac-decoder/build-ldac-decoder.sh \
    %{buildroot}%{_bindir}/tessera-ldac-decoder

%check
desktop-file-validate %{buildroot}%{_datadir}/applications/%{appid}.desktop
# Import every module against the installed tree, so a packaging mistake fails
# the build rather than the first launch.
PYTHONPATH=%{buildroot}%{python3_sitelib} QT_QPA_PLATFORM=offscreen \
    %{python3} -c "\
import importlib, pkgutil, tessera; \
[importlib.import_module(m.name) for m in pkgutil.walk_packages(tessera.__path__, 'tessera.')]; \
print('all modules import')"

%files
%license LICENSE
%doc README.md docs/PROTOCOL.md docs/WINDOWS.md
%doc docs/PHONE_AUDIO_AND_CAMERA.md docs/FEATURE_REVIEW.md
%{python3_sitelib}/tessera/
%{_bindir}/tessera
%{_datadir}/applications/%{appid}.desktop
%{_metainfodir}/%{appid}.metainfo.xml
%{_datadir}/icons/hicolor/scalable/apps/%{appid}.svg
%{_prefix}/lib/modprobe.d/tessera-v4l2loopback.conf
%{_datadir}/wireplumber/wireplumber.conf.d/51-tessera-bluez.conf
%{_datadir}/tessera/
%{_bindir}/tessera-ldac-decoder

%changelog
* Sun Sep 13 2026 Tessera contributors - 1.10.0-22
- Files, both ways, over the connection the app already has. Drop them on the
  window or pick them on the new Share page; on the phone, share anything to
  Tessera from any app and it lands in this computer's download folder. No
  pairing beyond what is already there, and nothing leaves the local network.
- Neither end ever holds a whole file in memory: 256 kB chunks, paced against
  what the socket has actually put on the network, written to a temporary name
  and renamed only when the last chunk lands. An interrupted transfer leaves
  nothing behind that could be mistaken for a complete file.
- A send is finished when the phone says it saved the file, not when the last
  bytes reach the socket. The difference is a whole watermark of data, and
  reporting the earlier moment meant claiming success for files that never
  arrived.
- Measured against the phone rather than assumed: 24 MB each way, byte for
  byte, at the speed of the link -- which on the network it was tested on is
  what adb itself manages over the same Wi-Fi.

* Sun Sep 13 2026 Tessera contributors - 1.10.0-21
- Bluetooth connects on its own now, shortly after the app starts and again if
  the link drops. Connecting is still not playing: it brings up the hands-free
  profile only, so the media profile is never claimed and nothing moves off the
  phone's own headphones. Audio moves when the button asks, and only then.
- A Bluetooth button sits in the sidebar next to the connection pill and the
  refresh button, saying what pressing it would do and colouring itself by
  whether the phone is connected. A phone disconnected there stays
  disconnected -- the automatic connect follows the buttons rather than
  fighting them.
- Bluetooth is the route the app leads with: it moves the sound rather than
  copying it, and it is the only one that can carry a call. Playing over the
  companion link is now the fallback for a computer with no Bluetooth radio,
  switched on in Settings -- and already on where this computer cannot do
  Bluetooth audio at all, since there it is the only route there is.
- The Audio page is ordered to match, and a new check covers the connection:
  that it never brings up the media profile, that a deliberate disconnect is
  not undone, and that an automatic attempt which fails stays out of the way.

* Sun Sep 13 2026 Tessera contributors - 1.10.0-20
- The phone can be kept quiet while its audio plays here. What the link carries
  is a copy, so both were playing the same track a fraction of a second apart;
  a box on the Audio card silences the phone for as long as the stream runs and
  Android puts its volume back afterwards -- including if this app dies while
  streaming. It is ticked by default and takes effect the moment it is pressed,
  mid-stream. The capture is taken before the phone's volume stage, measured on
  an S25, so silencing the phone costs this side nothing.
- The Audio page scrolls. With four cards it was taller than the window, and a
  layout with no room shrinks widgets rather than refusing: wrapped text
  collapsed to one clipped line and the buttons lost half their height.
- A check can no longer write the real configuration. Building a default
  `Config` and touching anything that saves wrote a blank configuration over
  the user's, pairing included; every check now runs against a throwaway
  directory.

* Sun Sep 13 2026 Tessera contributors - 1.10.0-19
- The phone's audio can now play here over the companion link instead of
  Bluetooth. The phone sends a copy of what it is playing, so it keeps playing
  there and its own headphones are untouched -- nothing in this route is
  capable of taking them, which the A2DP route always was. No pairing, no
  profile switch, and it works on Windows too.
- Android asks before capturing playback, and makes that consent single-use, so
  the first version asked on the phone every time. Settings now offers to grant
  the projection app operation once, through Shizuku, over the link: after that
  the audio starts in about a second with nothing to do on the phone.
- Both audio routes are kept. A setting decides which one the sidebar's switch
  uses: whichever works, always the link, or always Bluetooth. They are not
  equivalent -- only Bluetooth can carry a call.
- Notifications repeated on this desktop now go to the desktop's own
  notification server, so they carry a Dismiss action and, where the server
  supports it, a reply box in the popup itself. The tray remains the route
  where there is no session bus.
- The phone is published as an MPRIS player, so its track appears in the
  desktop's media applet and the keyboard's media keys control it -- with no
  Bluetooth involved.
- Ringing the phone no longer needs KDE Connect: the companion app rings it on
  the alarm stream, so a silenced phone still answers, and puts the volume back
  afterwards.
- adb is reconnected automatically when it drops, using the address the
  companion app is already talking to. Screen mirroring and the app launcher
  stopped working after every reboot until now.
- The phone no longer polls its clipboard while its screen is off.

* Sat Sep 12 2026 Tessera contributors - 1.10.0-18
- Tessera now builds and runs on Windows. One module decides the platform --
  where files go, which features can work -- and everything else asks it, so
  this RPM is unaffected beyond the new files it carries.
- Windows keeps: pairing, notifications with real toasts, passcodes, messages,
  calls, photos, apps, screen mirroring, clipboard, the hotspot (joined with
  netsh rather than NetworkManager), battery and signal, ringer control, and
  start-at-login through the registry.
- Windows cannot do three things, which are hidden with the reason rather than
  left to fail: Bluetooth audio into the computer (Windows has no sink
  profile), the phone as a webcam (needs a signed driver), and setting Focus
  Assist (no API), so Do Not Disturb there silences Tessera's own popups only.
- Desktop popups are now implemented rather than merely configurable: each
  notification is repeated through the tray, which is a Qt toast on Windows and
  libnotify on Linux, and a silenced phone silences them.
- The app draws its own icons where there is no icon theme, which is Windows;
  the application icon is drawn from the same code at build time.
- packaging/windows/ has the PyInstaller spec and an Inno Setup installer;
  scripts/build-windows.ps1 builds all three artefacts, and
  scripts/check-platform.py exercises the Windows paths from a Linux checkout.

* Sat Sep 12 2026 Tessera contributors - 1.10.0-17
- The webcam and mirror switches are outlines again rather than solid blocks.
  Recolouring an icon to match the text beside it turns line art the right
  colour and a picture into its own silhouette, which is what camera-photo and
  smartphone were: pictures. Icons are now taken from the "-symbolic" line-art
  version where the theme has one, and an icon that is a picture is left in
  the theme's own colours instead of being flattened.
- The ringer switch is one family throughout -- muted, low and high speaker --
  because Breeze's vibrate and ringing icons are full-colour device pictures
  with no line-art version, and they disappeared into a dark panel.

* Sat Sep 12 2026 Tessera contributors - 1.10.0-16
- Settings apply as they are made, and the Save button is gone. It used to sit
  inside the "Screen and windows" card at the bottom of a scrolling page, so
  anything changed in Startup, Sidebar or Features appeared to take and was
  thrown away at the next launch. Nothing was being written at all.
- The sidebar's quick switches therefore work: ticking one shows it
  immediately, unticking hides it, and both survive a restart.
- A setting committed elsewhere no longer snaps the sidebar back to its saved
  width, so a width you have just dragged past is left alone.
- A switch the user has not chosen is parented to the panel rather than left
  parentless, so enabling one cannot flash a stray window of its own.
- The webcam switch is drawn as a photo camera.

* Sat Sep 12 2026 Tessera contributors - 1.10.0-15
- The sidebar is resizable: drag its edge to anywhere between 280 and 720
  pixels. What is drawn inside scales with the width it is given, up to a
  ceiling, so a wider rail means bigger icons rather than only more space.
  The width is no longer tied to the window's, which resized the rail under
  you whenever the window changed.
- Two widths are remembered, one for a window and one for a maximised or full
  screen one, so dragging in one mode leaves the other alone. Settings shows
  both and they can be typed in directly.
- Settings lists the sidebar's quick switches and each can be turned on or
  off. Two more are on offer: mirror the phone's screen, and play the phone's
  audio here.
- The webcam switch is drawn as a camcorder. The old icon read as a briefcase
  at that size.
- Passcodes can be copied from the sidebar: the newest one sits above the
  notification feed with a copy button, and any feed row carrying a code gets
  one of its own. The overview's passcode card is gone, since the sidebar
  shows it on every page.

* Sat Sep 12 2026 Tessera contributors - 1.10.0-14
- Apps is a tab of its own in the strip rather than the bottom half of the
  Screen page, which is now only the mirror. It has its own feature switch.
- Six switches in the device panel, reflowing to however many fit: Do Not
  Disturb, the ringer, clipboard sharing, ring the phone, the hotspot, and the
  phone's camera as a webcam.
- The ringer switch steps the phone between normal, vibrate and silent, and
  shows which mode it is in. The companion app takes a "ringer_set" command
  for it; silencing needs the same notification access Do Not Disturb uses,
  and says so when it has not been granted.
- One click on the hotspot switch starts the phone's hotspot and joins it,
  bringing the Hotspot page forward to report on the sequence.
- Do Not Disturb is drawn as the Do Not Disturb roundel rather than a
  crossed-out bell, which reads as "notifications off".
- The panel grows with the window -- 23.5% of its width, between 300 and 480
  pixels -- and its icons, switches and avatars scale with it. A fixed rail
  left 16-pixel icons on a large screen.
- The notification feed holds the newest eight, with a button to the full page
  and a line saying how many more are there, rather than growing without limit.
- Page titles stay at the top of the page. A header row grew into whatever
  height the page had spare, so a page showing an empty state had its title
  and subtitle drifting apart in the middle of the window.

* Sat Sep 12 2026 Tessera contributors - 1.10.0-13
- The device panel now shows the phone's complications in one line -- Bluetooth,
  Wi-Fi, cellular signal and network type, ringer mode, battery -- drawn with
  the desktop's own status icons, stepped by level the way a status bar does it.
  Each hides itself when the phone has not reported it, and all of them clear
  when the phone disconnects.
- Battery detail below the bar: charging state and supply, time to full,
  current, temperature, and health when it is not good. The companion app grew
  a "status" frame to report it, along with signal and ringer; it is pushed on
  change, so nothing is polled.
- The notification feed moved into the panel, visible from every tab. The
  overview's notifications tile is gone: two copies of the list drifted apart.
- The top strip is down to four tabs -- Overview, Calls, Messages, Photos --
  with More for the device features and the gear for settings. A page opened
  from More takes the last tab, so the selected tab always names the page on
  screen.
- The selected tab is underlined in the accent colour, painted rather than
  styled: a QTabBar polished before the application stylesheet exists never
  picked up a border on its tabs.
- Small monochrome icons are repainted in the colour of the text beside them,
  which is what makes them legible at 16px on either a light or a dark theme.

* Sat Sep 12 2026 Tessera contributors - 1.10.0-12
- Laid out like Phone Link: a device panel down the left -- which phone, its
  connection, its battery, its switches, what it is playing -- and a tab strip
  across the top for the pages, instead of a navigation list in the sidebar.
- The quick toggles and the now-playing block moved from the overview into
  that panel, so they are in reach from every tab rather than only one.
- Navigation is in one place again. The overview's buttons that only opened
  another page are gone, since the tabs cover that.
- Settings is the gear at the end of the tab strip.

* Sat Sep 12 2026 Tessera contributors - 1.10.0-11
- Settings has a Startup section: start Tessera at login, and start it
  minimised to the tray. The minimised setting existed in the config file
  and was never exposed anywhere.
- Autostart is an XDG entry in ~/.config/autostart, so every desktop reads it
  and lists it in its own autostart settings -- visible and undoable there as
  well as here. An entry a desktop has disabled rather than deleted reads as
  off, which is how those lists turn things off.
- Starting minimised shows the window anyway when the desktop has no system
  tray, so the app cannot end up running with no way to reach it.

* Sat Sep 12 2026 Tessera contributors - 1.10.0-10
- The interface follows the desktop instead of imposing its own look. Colours
  and the accent come from the platform palette, icons from the icon theme,
  font sizes from the desktop font. Buttons, checkboxes, combo boxes, text
  fields and scrollbars are drawn by the platform style -- the old stylesheet
  restyled all of them, and its checkbox rule dropped the checkmark, so a
  checked box was a blue square indistinguishable from an unchecked one.
- Cards are a shade lighter than the window rather than taking Breeze's view
  colour, which is darker and made them look like holes in the page.
- One-time passcodes are copyable wherever they appear, not only on the
  notifications page: the overview shows the newest one with a Copy button,
  and a message carrying a code gets a copy button inside the chain.
- Notifications gained the Refresh every other data page already had, and a
  phone number on the calls page can be copied like any other text.
- The quick row no longer mixes toggles and actions indistinguishably --
  toggles are checkable and get the platform's checked state, and the two
  groups are separated.
- In-app explanations are one or two sentences. The reasoning moved to
  docs/DESIGN.md, where it can be read rather than crowding the window.

* Sat Sep 12 2026 Tessera contributors - 1.10.0-9
- Install advice now names the right package and the right command for the
  distribution it is running on. It said "sudo dnf install android-tools" and
  "run scripts/setup-fedora.sh" everywhere, which is wrong on Debian, Arch,
  openSUSE and the rest -- and advice that does not work is barely better
  than none. See tessera/core/packages.py; only the Fedora names are verified
  against a real system, and a package it cannot name is described in words
  rather than invented.
- Do Not Disturb reaches desktops other than Plasma. Inhibit/UnInhibit is a
  Plasma extension to the notification specification, not part of it, so on
  GNOME the old check passed -- the service is registered -- and silencing
  then did nothing at all. Tessera now probes for the method rather than the
  service, and falls back to GNOME's, Cinnamon's or XFCE's own switch, or to
  dunstctl. A desktop with none of those says so instead of pretending.
- Those desktops announce no change when the user flips their own Do Not
  Disturb, so that direction is polled for them. Plasma still gets it free.
- scripts/install-user.sh installs Tessera for one user on any distribution:
  a launcher, a menu entry, the icon and the audio configuration, all inside
  $HOME, with no root and no packaging. Not pip -- Fedora and Debian both
  mark the system interpreter as externally managed.
- The LDAC decoder no longer assumes systemd. Without it, it prints the one
  environment variable to set rather than writing a unit override nothing
  will read, and the codec list falls back to applying at next start.

* Sat Sep 12 2026 Tessera contributors - 1.10.0-8
- Bluetooth no longer assumes the adapter is hci0. It is on most machines and
  is not on plenty of others -- plug in a USB Bluetooth dongle, or have had
  one plugged in once, and the built-in radio can be hci1. The assumption did
  not fail loudly: profile requests went to an object that did not exist and
  the transport and codec lookups found nothing, so music never arrived and
  nothing said why. The device's object path is now read from BlueZ.
- The LDAC decoder finds PipeWire's plugin directory instead of assuming
  /usr/lib64/spa-0.2. That path is wrong on 32-bit, on distributions that do
  not split lib64, and on Debian's multiarch layout -- and a machine with
  32-bit PipeWire libraries installed alongside has both, where guessing puts
  a plugin of the wrong architecture ahead of the right one. It is read from
  the running session manager, which is the only authority on the answer.
- A missing pipewire-utils now says so, and names the package, instead of
  surfacing as the bare words "pw-link: not found".
- Checking the decoder distinguishes "LDAC is unavailable" from "bluetoothd
  has logged nothing this boot, so there is no way to tell".

* Sat Sep 12 2026 Tessera contributors - 1.10.0-7
- The phone's codec list was being lost every time the app started. It is a
  list, and the settings loader silently dropped any field whose type was a
  parameterised generic -- so each launch re-advertised the wide codec set,
  restarted the audio service, then learned the codecs again, wrote the narrow
  set and restarted it a second time. Two interruptions to this computer's
  sound per launch, and a window in between where the phone could negotiate
  aptX instead of LDAC. Launching is now silent.
- Pages stop polling while they are off screen. The Audio page was reading
  Bluetooth and PipeWire state every four seconds whether or not anyone was
  looking at it, and carried on while the window was minimised: several
  thousand process spawns and tens of megabytes of parsed JSON per hour for a
  page nobody could see.
- Finding the phone takes one query instead of four. It asks about the known
  address rather than enumerating every paired device and reading each one.
- The periodic Bluetooth check asks BlueZ once for what it needs, instead of
  twice for overlapping halves of the same answer, and only reads PipeWire's
  node list when BlueZ says audio is actually on the wire -- that read is a
  fifth of a megabyte, and it was happening every fifteen seconds.
- Do Not Disturb stops polling over adb while the companion app is reporting
  the phone's state, which it does as it changes. The poll is the battery
  cost the companion app exists to avoid.
- Waiting for the phone to start playing now gives up after five minutes
  instead of polling for as long as the app stays open.
- A background task that finishes after the app has shut down no longer ends
  the process with a traceback about a deleted signal.

* Fri Sep 11 2026 Tessera contributors - 1.10.0-6
- Tessera no longer keeps trying the hotspot address after the hotspot is
  off. An address on a subnet this computer is not on now goes last rather
  than first, so a stale one costs nothing instead of a full connect timeout
  on every cycle. It is demoted rather than dropped, because a routed network
  or a VPN can make such an address perfectly reachable.
- When the remembered address is stale and mDNS is silent, the local subnet
  is swept for anything listening on the companion port -- 253 addresses in
  under two seconds. This is what survives the phone being given a different
  address by DHCP. Nothing is trusted for answering: the phone is whichever
  address presents the certificate pinned at pairing, and the pairing token
  is never sent to anything that fails that check, because the TLS handshake
  ends first.
- A certificate that does not match at a guessed address is now skipped
  quietly. It used to stop reconnection altogether and warn about
  impersonation, which would have been the wrong answer for any other machine
  that happens to listen on that port.
- The phone re-advertises itself over mDNS whenever the network changes. The
  registration was made once, at startup, and Android quietly drops it on a
  network change -- so after a hotspot session the phone was still listening
  and no longer findable, which is how the stale address came to be used in
  the first place.

* Fri Sep 11 2026 Tessera contributors - 1.10.0-5
- A hotspot switched on by hand now gets joined. Without Shizuku the phone
  will not take the start command, and the old behaviour -- open the
  tethering panel, then ask the user to press Start again -- only opened the
  panel a second time. Tessera now waits for the hotspot to appear and joins
  it by itself, and skips the panel entirely when it is already on.
- The phone can report its own hotspot without any privilege, which is what
  makes that possible: "cmd wifi is-softap-enabled" needs the shell uid and
  so is always unknown on exactly the phones that need the panel, but a soft
  AP leaves an interface holding the gateway address of its own subnet.
- Building the companion app installs it over USB when the phone is also
  reachable by wireless debugging, instead of refusing to choose.

* Fri Sep 11 2026 Tessera contributors - 1.10.0-4
- Joining the phone's hotspot no longer drops the companion link. The phone
  now reports every address it can be reached on, sent while both ends are
  still on the same network and after waiting for the tether interface to
  appear; the desktop probes them all once it has joined and reconnects on
  whichever answers. Guessing the default gateway was right often enough to
  look reliable and wrong in exactly the cases that stranded the link.
- Where there is no companion app, the same list is read over adb.

* Fri Sep 11 2026 Tessera contributors - 1.10.0-3
- "Best the phone offers" now actually picks the best one. Offered every
  codec, a phone does not choose the best available -- it applies its own
  ranking, and a Galaxy S25 settles on aptX with LDAC sitting right there.
  So the choice is made on this side: Tessera reads the codec list the phone
  publishes over Bluetooth when it connects, works out the best both ends can
  manage, and offers that one plus SBC. Selecting LDAC by hand is no longer
  needed.
- The phone's codec list is remembered, because BlueZ only publishes it while
  the phone is connected and an offer that swung between wide and narrow on
  every launch would restart the audio service each time.
- The companion APK now carries the same version number as this package,
  read from the Version field above rather than kept in a second place by
  hand. The APK tracks the version only, not this release number.

* Fri Sep 11 2026 Tessera contributors - 1.10.0-2
- LDAC setup no longer needs a terminal. Settings has a Set up LDAC button
  that runs the build and shows its output as it goes, offers to install the
  build packages through polkit when they are missing, and rewrites the codec
  list afterwards so the phone is actually offered LDAC -- installing the
  decoder alone changes nothing until that happens. Remove reverses it, and
  Rebuild is there for after a PipeWire update.

* Fri Sep 11 2026 Tessera contributors - 1.10.0-1
- LDAC can now be received. PipeWire's LDAC plugin has always contained a
  complete decode path, compiled out for want of a library exporting
  ldacBT_decode; tessera-ldac-decoder supplies one built on libldacdec,
  rebuilds that one codec plugin against the running PipeWire, and installs
  both under the user's home. The phone can then negotiate 909 kbit/s at up
  to 96 kHz instead of aptX at 44.1. --uninstall reverses it.
- The Bluetooth quality setting offers LDAC only when the decoder is present,
  and says how to add one when it is not. Offering a codec this computer
  cannot decode would have the phone send audio into silence.
- Messages refresh themselves the moment a text arrives, instead of waiting
  for the Refresh button. The phone decides what counts as a text -- the
  notification must come from whichever app is that phone's default SMS app
  and be categorised as a message -- so chat apps do not trigger a reload.
- Opening a conversation now shows its newest message rather than its oldest,
  and a refresh leaves the selected conversation and the scroll position
  alone unless you were already at the end.
- The Audio page reports codec, sample rate and bit rate, and no longer prints
  a bit depth. That number was the width PipeWire decoded into, not the
  audio's resolution, and read as a claim about quality that was not true.

* Fri Sep 11 2026 Tessera contributors - 1.9.1-1
- The Audio page reads what the audio is doing instead of which profile is
  selected, so it no longer announces playback the moment Bluetooth connects.
  It also names the codec, sample rate and bit depth actually in use.
- "Stop audio" is now "Play on the phone again", and is only offered while
  this computer holds the phone's media profile.
- "Play phone audio here" asks twice before giving up. Android drops the
  request when it is busy moving a playback session, which is why the button
  worked only sometimes, and waits for the profile to drop rather than
  guessing at a delay, which makes the switch quicker.
- Only the music stream is ever connected to the speakers. The hands-free
  stream carries a call at telephone quality and is left alone.
* Fri Sep 11 2026 Tessera contributors - 1.9.0-1
- The phone's audio now actually plays here. Parking the link used PipeWire's
  silent profile, which WirePlumber remembered and restored on every later
  connection, so the stream the phone offered had nothing willing to take it
  and was withdrawn after a few seconds. Playback is handed back by dropping
  the Bluetooth media profile instead, which is what Android acts on.
- Choosing this computer in the phone's own output picker is honoured rather
  than undone a few seconds later.
- "Play phone audio here" reconnects the media profile rather than asking for
  one already connected, which is the only form of the request Android moves
  playback for.
- A Bluetooth audio quality setting, choosing which codecs the phone is
  offered, with the codec actually negotiated shown while it plays.
- The phone reports why it is holding music back, so a phone that is ringing
  is no longer reported as a phone that was never selected.
* Fri Sep 11 2026 Tessera contributors - 1.8.0-1
- Connecting Bluetooth no longer moves the phone's audio: only the hands-free
  profile is connected, leaving playback where it was.
- Now playing comes from MediaSession through the companion app, so it works
  with no Bluetooth connected and without taking over the audio.
- Link the received audio stream to an output, which is what was missing when
  streaming produced no sound.

* Fri Sep 11 2026 Tessera contributors - 1.7.0-1
- Now playing works: metadata is read through busctl, because PySide6 cannot
  decode a D-Bus a{sv}, and the player is matched by device name, which is
  what bluez names its MPRIS service after.
- Park a Bluetooth link whenever it appears, not only when connected from
  here, so a phone connecting on its own no longer takes the audio path.
- Route received audio to a sink, so streaming it is audible rather than
  silently discarded.
- Match audio profiles by their description, so a phone offering only
  "audio-gateway" is understood.
- Ship the WirePlumber roles needed to receive audio at all.

* Fri Sep 11 2026 Tessera contributors - 1.6.0-1
- Open on an overview of tiles -- media, calls, notifications, messages and
  photos -- with quick switches, instead of a menu.
- Connecting Bluetooth no longer takes over the audio path; streaming starts
  only when asked, so an existing audio session is left alone.
- Use Tessera's own status bar icon on the phone rather than the Bluetooth one.

* Thu Sep 10 2026 Tessera contributors - 1.5.0-1
- Calls page: answer, decline and hang up from the computer, dial a number,
  and see recent calls. Call audio moves to the computer automatically when
  Bluetooth is connected.

* Thu Sep 10 2026 Tessera contributors - 1.4.0-1
- Webcam works over the companion app: the encoded stream from the phone is
  now fed to ffmpeg, codec configuration included.
- Identify the virtual camera by its driver rather than its label, so a device
  created under an older name is still recognised.
- A switch for every optional feature, which stops the work on the phone
  rather than only hiding the page.
- Readable conversations: sized bubbles, separated rows and elided previews.

* Thu Sep 10 2026 Tessera contributors - 1.3.0-1
- Reconnect button in the sidebar, which restarts the search for the phone.
- Group consecutive messages, stamp only the last of a run, and separate days,
  so neighbouring messages are told apart at a glance.

* Thu Sep 10 2026 Tessera contributors - 1.2.0-1
- Share the clipboard in both directions.
- Bluetooth page: take calls on the computer, play the phone's music through
  it, switch between the two profiles and see the current track.

* Thu Sep 10 2026 Tessera contributors - 1.1.0-1
- Reconnect by re-resolving the phone's address after a network change, so
  moving onto the phone's hotspot no longer drops the link.
- Detect a silently dead connection with a heartbeat, and time out a stale
  address instead of waiting for the kernel's TCP timeout.
- Enumerate camera resolutions, frame rates and hotspot bands from the device.
- Restart the hotspot when its SSID, passphrase or band changes.

* Thu Sep 10 2026 Tessera contributors - 1.0.0-1
- First packaged release.
