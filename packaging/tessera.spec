%global appid dev.tessera.Tessera

# Resolve the site-packages path without requiring python3-devel just to build
# a pure-Python package.
%{!?python3_sitelib: %global python3_sitelib %(%{__python3} -c "import sysconfig; print(sysconfig.get_path('purelib', vars={'base': '/usr', 'platbase': '/usr'}))")}
%{!?__python3: %global __python3 /usr/bin/python3}

Name:           tessera
Version:        1.10.0
Release:        27%{?dist}
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
# gdbus, which raises the desktop notifications.
Requires:       glib2

# Weak dependencies: each unlocks one feature, and several live in RPM Fusion
# or a COPR, so a missing one must not block installation.
Recommends:     ffmpeg
Recommends:     v4l2loopback
Recommends:     scrcpy
# The phone's storage as a folder: sshfs mounts the phone's SFTP server where
# every application can see it. GVfs is the fallback without it.
Recommends:     fuse-sshfs
# bluetoothctl and mpris-proxy, for calls and music over Bluetooth.
Recommends:     bluez
# pactl, for switching between the music and call profiles. It lives in
# pulseaudio-utils even on a PipeWire system.
Recommends:     pulseaudio-utils

# pw-dump and pw-link, for finding the node the phone's audio arrives on and
# connecting it to the speakers.
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
# Nothing to compile here: the application is pure Python.

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

# Virtual-camera module options.
install -Dm0644 packaging/51-tessera-bluez.conf \
    %{buildroot}%{_datadir}/wireplumber/wireplumber.conf.d/51-tessera-bluez.conf

install -Dm0644 packaging/tessera-v4l2loopback.conf \
    %{buildroot}%{_prefix}/lib/modprobe.d/tessera-v4l2loopback.conf

# The LDAC decoder is shipped as source because it has to be compiled against
# whichever PipeWire release the machine is running -- libspa-bluez5.so refuses
# a codec plugin built against a different ABI version.
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
* Mon Sep 14 2026 Ruchit M - 1.10.0-27
- Keep phone audio on the phone when Bluetooth connects, and resume paused music.
- Shorter comments and docs.

* Mon Sep 14 2026 Tessera contributors - 1.10.0-26
- Clipboard sharing without Shizuku, two ways.
- The Shizuku route no longer runs its two-second poll for the other routes.
- Windows: the build kept out Qt Multimedia, so the phone's audio over the link could not play; it is included now.

* Sun Sep 13 2026 Tessera contributors - 1.10.0-25
- The phone's storage as a folder.
- All files access, which Android 11 and later require for this, can be granted from the desktop through Shizuku, or on the phone as usual.
- File transfers have their own connection.

* Sun Sep 13 2026 Tessera contributors - 1.10.0-24
- The sidebar shows the phone's name -- the one its owner gave it -- with the model number on its tooltip rather than in place of it.
- Beside it is the phone itself: a phone-shaped tile showing the phone's wallpaper behind a bezel.

* Sun Sep 13 2026 Tessera contributors - 1.10.0-22
- Files, both ways, over the connection the app already has.
- Neither end ever holds a whole file in memory: 256 kB chunks, paced against what the socket has actually put on the network, written to a temporary name and renamed only when the last chunk lands.
- A send is finished when the phone says it saved the file, not when the last bytes reach the socket.

* Sun Sep 13 2026 Tessera contributors - 1.10.0-21
- Bluetooth connects on its own now, shortly after the app starts and again if the link drops.
- A Bluetooth button sits in the sidebar next to the connection pill and the refresh button, saying what pressing it would do and colouring itself by whether the phone is connected.
- Bluetooth is the route the app leads with: it moves the sound rather than copying it, and it is the only one that can carry a call.

* Sun Sep 13 2026 Tessera contributors - 1.10.0-20
- The phone can be kept quiet while its audio plays here.
- The Audio page scrolls.
- A check can no longer write the real configuration.

* Sun Sep 13 2026 Tessera contributors - 1.10.0-19
- The phone's audio can now play here over the companion link instead of Bluetooth.
- Android asks before capturing playback, and makes that consent single-use, so the first version asked on the phone every time.
- Both audio routes are kept.

* Sat Sep 12 2026 Tessera contributors - 1.10.0-18
- Tessera now builds and runs on Windows.
- Windows keeps: pairing, notifications with real toasts, passcodes, messages, calls, photos, apps, screen mirroring, clipboard, the hotspot (joined with netsh rather than NetworkManager), battery and signal, ringer control, and start-at-login through the registry.
- Windows cannot do three things, which are hidden with the reason rather than left to fail: Bluetooth audio into the computer (Windows has no sink profile), the phone as a webcam (needs a signed driver), and setting Focus Assist (no API), so Do Not Disturb there silences Tessera's own popups only.

* Sat Sep 12 2026 Tessera contributors - 1.10.0-17
- The webcam and mirror switches are outlines again rather than solid blocks.
- The ringer switch is one family throughout -- muted, low and high speaker -- because Breeze's vibrate and ringing icons are full-colour device pictures with no line-art version, and they disappeared into a dark panel.

* Sat Sep 12 2026 Tessera contributors - 1.10.0-16
- Settings apply as they are made, and the Save button is gone.
- The sidebar's quick switches therefore work: ticking one shows it immediately, unticking hides it, and both survive a restart.
- A setting committed elsewhere no longer snaps the sidebar back to its saved width, so a width you have just dragged past is left alone.

* Sat Sep 12 2026 Tessera contributors - 1.10.0-15
- The sidebar is resizable: drag its edge to anywhere between 280 and 720 pixels.
- Two widths are remembered, one for a window and one for a maximised or full screen one, so dragging in one mode leaves the other alone.
- Settings lists the sidebar's quick switches and each can be turned on or off.

* Sat Sep 12 2026 Tessera contributors - 1.10.0-14
- Apps is a tab of its own in the strip rather than the bottom half of the Screen page, which is now only the mirror.
- Six switches in the device panel, reflowing to however many fit: Do Not Disturb, the ringer, clipboard sharing, ring the phone, the hotspot, and the phone's camera as a webcam.
- The ringer switch steps the phone between normal, vibrate and silent, and shows which mode it is in.

* Sat Sep 12 2026 Tessera contributors - 1.10.0-13
- The device panel now shows the phone's complications in one line -- Bluetooth, Wi-Fi, cellular signal and network type, ringer mode, battery -- drawn with the desktop's own status icons, stepped by level the way a status bar does it.
- Battery detail below the bar: charging state and supply, time to full, current, temperature, and health when it is not good.
- The notification feed moved into the panel, visible from every tab.

* Sat Sep 12 2026 Tessera contributors - 1.10.0-12
- Laid out like Phone Link: a device panel down the left -- which phone, its connection, its battery, its switches, what it is playing -- and a tab strip across the top for the pages, instead of a navigation list in the sidebar.
- The quick toggles and the now-playing block moved from the overview into that panel, so they are in reach from every tab rather than only one.
- Navigation is in one place again.

* Sat Sep 12 2026 Tessera contributors - 1.10.0-11
- Settings has a Startup section: start Tessera at login, and start it minimised to the tray.
- Autostart is an XDG entry in ~/.config/autostart, so every desktop reads it and lists it in its own autostart settings -- visible and undoable there as well as here.
- Starting minimised shows the window anyway when the desktop has no system tray, so the app cannot end up running with no way to reach it.

* Sat Sep 12 2026 Tessera contributors - 1.10.0-10
- The interface follows the desktop instead of imposing its own look.
- Cards are a shade lighter than the window rather than taking Breeze's view colour, which is darker and made them look like holes in the page.
- One-time passcodes are copyable wherever they appear, not only on the notifications page: the overview shows the newest one with a Copy button, and a message carrying a code gets a copy button inside the chain.

* Sat Sep 12 2026 Tessera contributors - 1.10.0-9
- Install advice now names the right package and the right command for the distribution it is running on.
- Do Not Disturb reaches desktops other than Plasma.
- Those desktops announce no change when the user flips their own Do Not Disturb, so that direction is polled for them.

* Sat Sep 12 2026 Tessera contributors - 1.10.0-8
- Bluetooth no longer assumes the adapter is hci0.
- The LDAC decoder finds PipeWire's plugin directory instead of assuming /usr/lib64/spa-0.2.
- A missing pipewire-utils now says so, and names the package, instead of surfacing as the bare words "pw-link: not found".

* Sat Sep 12 2026 Tessera contributors - 1.10.0-7
- The phone's codec list was being lost every time the app started.
- Pages stop polling while they are off screen.
- Finding the phone takes one query instead of four.

* Fri Sep 11 2026 Tessera contributors - 1.10.0-6
- Tessera no longer keeps trying the hotspot address after the hotspot is off.
- When the remembered address is stale and mDNS is silent, the local subnet is swept for anything listening on the companion port -- 253 addresses in under two seconds.
- A certificate that does not match at a guessed address is now skipped quietly.

* Fri Sep 11 2026 Tessera contributors - 1.10.0-5
- A hotspot switched on by hand now gets joined.
- The phone can report its own hotspot without any privilege, which is what makes that possible: "cmd wifi is-softap-enabled" needs the shell uid and so is always unknown on exactly the phones that need the panel, but a soft AP leaves an interface holding the gateway address of its own subnet.
- Building the companion app installs it over USB when the phone is also reachable by wireless debugging, instead of refusing to choose.

* Fri Sep 11 2026 Tessera contributors - 1.10.0-4
- Joining the phone's hotspot no longer drops the companion link.
- Where there is no companion app, the same list is read over adb.

* Fri Sep 11 2026 Tessera contributors - 1.10.0-3
- "Best the phone offers" now actually picks the best one.
- The phone's codec list is remembered, because BlueZ only publishes it while the phone is connected and an offer that swung between wide and narrow on every launch would restart the audio service each time.
- The companion APK now carries the same version number as this package, read from the Version field above rather than kept in a second place by hand.

* Fri Sep 11 2026 Tessera contributors - 1.10.0-2
- LDAC setup no longer needs a terminal.

* Fri Sep 11 2026 Tessera contributors - 1.10.0-1
- LDAC can now be received.
- The Bluetooth quality setting offers LDAC only when the decoder is present, and says how to add one when it is not.
- Messages refresh themselves the moment a text arrives, instead of waiting for the Refresh button.

* Fri Sep 11 2026 Tessera contributors - 1.9.1-1
- The Audio page reads what the audio is doing instead of which profile is selected, so it no longer announces playback the moment Bluetooth connects.
- "Stop audio" is now "Play on the phone again", and is only offered while this computer holds the phone's media profile.
- "Play phone audio here" asks twice before giving up.

* Fri Sep 11 2026 Tessera contributors - 1.9.0-1
- The phone's audio now actually plays here.
- Choosing this computer in the phone's own output picker is honoured rather than undone a few seconds later.
- "Play phone audio here" reconnects the media profile rather than asking for one already connected, which is the only form of the request Android moves playback for.

* Fri Sep 11 2026 Tessera contributors - 1.8.0-1
- Connecting Bluetooth no longer moves the phone's audio: only the hands-free profile is connected, leaving playback where it was.
- Now playing comes from MediaSession through the companion app, so it works with no Bluetooth connected and without taking over the audio.
- Link the received audio stream to an output, which is what was missing when streaming produced no sound.

* Fri Sep 11 2026 Tessera contributors - 1.7.0-1
- Now playing works: metadata is read through busctl, because PySide6 cannot decode a D-Bus a{sv}, and the player is matched by device name, which is what bluez names its MPRIS service after.
- Park a Bluetooth link whenever it appears, not only when connected from here, so a phone connecting on its own no longer takes the audio path.
- Route received audio to a sink, so streaming it is audible rather than silently discarded.

* Fri Sep 11 2026 Tessera contributors - 1.6.0-1
- Open on an overview of tiles -- media, calls, notifications, messages and photos -- with quick switches, instead of a menu.
- Connecting Bluetooth no longer takes over the audio path; streaming starts only when asked, so an existing audio session is left alone.
- Use Tessera's own status bar icon on the phone rather than the Bluetooth one.

* Thu Sep 10 2026 Tessera contributors - 1.5.0-1
- Calls page: answer, decline and hang up from the computer, dial a number, and see recent calls.

* Thu Sep 10 2026 Tessera contributors - 1.4.0-1
- Webcam works over the companion app: the encoded stream from the phone is now fed to ffmpeg, codec configuration included.
- Identify the virtual camera by its driver rather than its label, so a device created under an older name is still recognised.
- A switch for every optional feature, which stops the work on the phone rather than only hiding the page.

* Thu Sep 10 2026 Tessera contributors - 1.3.0-1
- Reconnect button in the sidebar, which restarts the search for the phone.
- Group consecutive messages, stamp only the last of a run, and separate days, so neighbouring messages are told apart at a glance.

* Thu Sep 10 2026 Tessera contributors - 1.2.0-1
- Share the clipboard in both directions.
- Bluetooth page: take calls on the computer, play the phone's music through it, switch between the two profiles and see the current track.

* Thu Sep 10 2026 Tessera contributors - 1.1.0-1
- Reconnect by re-resolving the phone's address after a network change, so moving onto the phone's hotspot no longer drops the link.
- Detect a silently dead connection with a heartbeat, and time out a stale address instead of waiting for the kernel's TCP timeout.
- Enumerate camera resolutions, frame rates and hotspot bands from the device.

* Thu Sep 10 2026 Tessera contributors - 1.0.0-1
- First packaged release.
