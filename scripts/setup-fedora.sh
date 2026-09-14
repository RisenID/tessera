#!/usr/bin/env bash
# 

set -euo pipefail

BOLD=$'\e[1m'; DIM=$'\e[2m'; GREEN=$'\e[32m'; YELLOW=$'\e[33m'; RED=$'\e[31m'; RESET=$'\e[0m'

step()  { printf '\n%s==> %s%s\n' "$BOLD" "$1" "$RESET"; }
ok()    { printf '%s  ✓ %s%s\n' "$GREEN" "$1" "$RESET"; }
warn()  { printf '%s  ! %s%s\n' "$YELLOW" "$1" "$RESET"; }
fail()  { printf '%s  ✗ %s%s\n' "$RED" "$1" "$RESET"; }
note()  { printf '%s    %s%s\n' "$DIM" "$1" "$RESET"; }

have() { command -v "$1" >/dev/null 2>&1; }

if [[ ${EUID} -eq 0 ]]; then
    fail "Run this as your normal user; it will ask for sudo when it needs to."
    exit 1
fi

# ---------------------------------------------------------------- packages ---

step "Checking Fedora packages"

# python3-pyside6 : the desktop app's toolkit
# android-tools   : adb, for screen control and as a fallback transport
# ffmpeg          : turns the phone's video stream into a virtual webcam
# python3-pyside6, android-tools and the rest are RPM dependencies of the
# tessera package; only the extras are handled here.
PACKAGES=(ffmpeg)
MISSING=()
for package in "${PACKAGES[@]}"; do
    if rpm -q "$package" >/dev/null 2>&1; then
        ok "$package"
    else
        MISSING+=("$package")
    fi
done

if (( ${#MISSING[@]} )); then
    note "Installing: ${MISSING[*]}"
    sudo dnf install -y "${MISSING[@]}"
    ok "installed ${MISSING[*]}"
fi

# ------------------------------------------------------------ v4l2loopback ---

step "Setting up the virtual camera (v4l2loopback)"

KERNEL="$(uname -r)"
if [[ ! -d "/usr/src/kernels/${KERNEL}" ]]; then
    warn "No kernel headers for ${KERNEL}"
    note "The module is built against your running kernel, so headers must match it."
    note "Install the -devel package for your kernel flavour, for example:"
    note "  sudo dnf install kernel-devel-${KERNEL}"
    note "Custom kernels (CachyOS, XanMod) use their own -devel package name."
else
    ok "kernel headers present for ${KERNEL}"
fi

if modinfo v4l2loopback >/dev/null 2>&1; then
    ok "v4l2loopback is available"
else
    if ! rpm -q rpmfusion-free-release >/dev/null 2>&1; then
        note "Enabling RPM Fusion (free), which packages v4l2loopback"
        sudo dnf install -y \
            "https://mirrors.rpmfusion.org/free/fedora/rpmfusion-free-release-$(rpm -E %fedora).noarch.rpm"
    fi
    note "Installing akmod-v4l2loopback and building it for your kernel"
    sudo dnf install -y akmod-v4l2loopback
    sudo akmods --kernels "${KERNEL}" || warn "akmods reported a problem; see /var/cache/akmods"
fi

# exclusive_caps=1 is what makes browsers and Zoom accept the device.
if [[ ! -f /etc/modprobe.d/tessera-v4l2loopback.conf ]]; then
    note "Writing /etc/modprobe.d/tessera-v4l2loopback.conf"
    sudo tee /etc/modprobe.d/tessera-v4l2loopback.conf >/dev/null <<'CONF'
# Tessera virtual camera.
# exclusive_caps=1 makes Firefox, Chrome and Zoom recognise the device.
options v4l2loopback devices=1 exclusive_caps=1 card_label="Tessera Camera"
CONF
    ok "module options written"
else
    ok "module options already configured"
fi

if lsmod | grep -q '^v4l2loopback'; then
    ok "v4l2loopback is loaded"
else
    note "Loading the module (the app can also do this on demand)"
    sudo modprobe v4l2loopback || warn "Could not load it now; a reboot may be needed after the akmod build."
fi

# ------------------------------------------------------------------ scrcpy ---

step "Checking scrcpy (screen mirroring and per-app windows)"

if have scrcpy; then
    ok "scrcpy $(scrcpy --version 2>/dev/null | head -1 | awk '{print $2}')"
else
    if sudo dnf install -y scrcpy 2>/dev/null; then
        ok "installed scrcpy from the Fedora repositories"
    else
        warn "scrcpy is not in your enabled repositories"
        note "Screen mirroring and single-app windows need scrcpy 3.0 or newer."
        note "Everything else - notifications, messages, photos, passcodes,"
        note "Do Not Disturb, hotspot and the webcam - works without it."
        echo
        note "Official builds: https://github.com/Genymobile/scrcpy/releases"
        note "Install one manually, then re-run this script to verify."
    fi
fi

# -------------------------------------------------------------------- adb ----

step "Checking adb access to your phone"

if have adb; then
    if [[ -n "$(adb devices | awk 'NR>1 && $2=="device"')" ]]; then
        ok "a phone is authorised over adb"
    else
        warn "No authorised phone over adb"
        note "Only needed for screen control and as a fallback. Enable USB"
        note "debugging in Developer options, plug in, and accept the prompt."
    fi
fi

# ------------------------------------------------------------------- done ----

step "Done"
echo
note "Launch Tessera from the application menu, or run: tessera"
note "Then open Settings and pair the companion app on your phone."
echo
