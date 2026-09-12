#!/usr/bin/env bash
#
# Teach this computer to receive LDAC.
#
# PipeWire can already decode LDAC: spa/plugins/bluez5/a2dp-codec-ldac.c has
# the whole path behind ENABLE_LDAC_DEC, switched on at build time only when a
# library providing ldacBT_decode() exists. Sony released the encoder and
# nothing else, so no distribution has ever shipped one -- which is why
# Fedora's PipeWire offers LDAC to headphones and cannot accept it from a
# phone.
#
# This script supplies the missing library from libldacdec (a clean-room
# decoder, vendored as a submodule), rebuilds just that one codec plugin
# against the exact PipeWire release installed here, and puts both somewhere
# WirePlumber will look first. Nothing owned by the package manager is touched
# and nothing is installed system-wide: --uninstall removes it all.
#
#   scripts/build-ldac-decoder.sh              build, install, restart WirePlumber
#   scripts/build-ldac-decoder.sh --uninstall  put everything back
#   scripts/build-ldac-decoder.sh --check      report what is currently in place

set -euo pipefail

here="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
root="$(cd "$here/.." && pwd)"

# The two pieces of source this builds from sit in the checkout when the script
# is run from a clone, and beside the script when it is run from the installed
# package as /usr/bin/tessera-ldac-decoder.
for candidate in "$root/third_party/libldacdec" "$here/libldacdec"; do
    [[ -f $candidate/libldacdec.c ]] && { ldacdec_src="$candidate"; break; }
done
for candidate in "$root/native/ldac-decoder" "$here"; do
    [[ -f $candidate/ldacBT_dec.c ]] && { shim_src="$candidate"; break; }
done
prefix="${TESSERA_LDAC_PREFIX:-$HOME/.local/lib64/tessera}"
plugin_dir="$prefix/spa-0.2/bluez5"
dropin="$HOME/.config/systemd/user/wireplumber.service.d/50-tessera-ldac.conf"
cache="${XDG_CACHE_HOME:-$HOME/.cache}/tessera"

# Where PipeWire's own SPA plugins live.
#
# Not a constant. /usr/lib64/spa-0.2 is right on Fedora x86_64 and wrong
# elsewhere -- /usr/lib/spa-0.2 on 32-bit and on distributions that do not
# split lib64, /usr/lib/<triplet>/spa-0.2 on Debian and Ubuntu. Worse, a
# machine with 32-bit PipeWire libraries installed alongside has *both*, and
# picking the wrong one puts a plugin of the wrong architecture ahead of the
# right one, which the loader then refuses.
#
# The running session manager is the ground truth: whatever directory it has
# libspa-bluez5.so mapped from is the directory this plugin has to join.
system_plugins() {
    local pid dir

    for pid in $(pgrep -u "$(id -u)" -x wireplumber 2>/dev/null); do
        dir="$(sed -n 's#.* \(/.*\)/spa-0\.2/bluez5/libspa-bluez5\.so$#\1/spa-0.2#p' \
               "/proc/$pid/maps" 2>/dev/null | head -1)"
        [[ -n $dir ]] && { printf '%s\n' "$dir"; return; }
    done

    # Not running yet: ask the pipewire binary which libpipewire it uses, and
    # read the plugin directory compiled into it.
    local lib
    lib="$(ldd "$(command -v pipewire 2>/dev/null)" 2>/dev/null |
           sed -n 's#.*=> \(/.*libpipewire-0\.3\.so[^ ]*\).*#\1#p' | head -1)"
    if [[ -n $lib ]]; then
        dir="$(strings "$lib" 2>/dev/null | grep -m1 -E '^/.*/spa-0\.2$')"
        [[ -n $dir && -d $dir/bluez5 ]] && { printf '%s\n' "$dir"; return; }
    fi

    for dir in "/usr/lib64/spa-0.2" "/usr/lib/$(uname -m)-linux-gnu/spa-0.2" \
               "/usr/lib/spa-0.2" "/usr/local/lib64/spa-0.2" "/usr/local/lib/spa-0.2"; do
        [[ -f $dir/bluez5/libspa-codec-bluez5-ldac.so ]] && { printf '%s\n' "$dir"; return; }
    done
}

# Colour only for a terminal. Tessera runs this script itself and shows the
# output in a log pane, where escape codes would appear as literal rubbish.
if [[ -t 1 ]]; then bold=$'\033[1m'; red=$'\033[31m'; off=$'\033[0m'
else bold=''; red=''; off=''; fi

say() { printf '\n%s%s%s\n' "$bold" "$*" "$off"; }
die() { printf '%serror:%s %s\n' "$red" "$off" "$*" >&2; exit 1; }

# -- what is in place right now ----------------------------------------------

# The endpoints belong to WirePlumber's D-Bus name, not to any object under
# org.bluez, so they cannot be listed from BlueZ's object manager. bluetoothd
# logs each one as it is registered, and that log is the readable record of
# what this computer is currently willing to receive.
endpoints() {
    # Every WirePlumber restart registers a fresh set under a new D-Bus name,
    # so the endpoints that count are the ones belonging to the most recent
    # sender. Reading the earlier sets as well would report codecs that were
    # withdrawn hours ago.
    local log sender
    log="$(journalctl -b -t bluetoothd --no-pager 2>/dev/null |
           sed -n 's/.*Endpoint registered: sender=\([^ ]*\) path=\(.*\)/\1 \2/p')"
    sender="$(printf '%s\n' "$log" | tail -1 | cut -d' ' -f1)"
    [[ -n $sender ]] || return 0
    printf '%s\n' "$log" | awk -v s="$sender" '$1 == s { print $2 }' | sort -u
}

check() {
    printf 'decoder library : %s\n' \
        "$([[ -f $prefix/libldacBT_dec.so.2 ]] && echo "$prefix/libldacBT_dec.so.2" || echo 'not installed')"
    printf 'codec plugin    : %s\n' \
        "$([[ -f $plugin_dir/libspa-codec-bluez5-ldac.so ]] && echo "$plugin_dir/libspa-codec-bluez5-ldac.so" || echo 'not installed')"
    printf 'wireplumber     : %s\n' \
        "$([[ -f $dropin ]] && echo 'searching Tessera plugins first' || echo 'stock plugin path')"
    printf 'endpoints offered to phones:\n'
    endpoints | sed 's/^/  /'
    if endpoints | grep -q 'A2DPSink/ldac'; then
        printf '\nLDAC can be received.\n'
    elif [[ -z $(endpoints) ]]; then
        # No record either way. bluetoothd is the only thing that logs these,
        # and a volatile journal or a differently named unit leaves nothing to
        # read -- which is not the same as LDAC being unavailable.
        printf '\nCannot tell: bluetoothd has logged no endpoint registrations\n'
        printf 'this boot. Reconnect the phone, or restart WirePlumber, and\n'
        printf 'run this again.\n'
    else
        printf '\nNo A2DPSink/ldac endpoint. If the files above are in place, the\n'
        printf 'codec list is the other half: Tessera writes it from the Bluetooth\n'
        printf 'audio quality setting, and it must include ldac.\n'
    fi
}

uninstall() {
    say 'Removing the LDAC decoder'
    rm -f "$prefix/libldacBT_dec.so.2" \
          "$plugin_dir/libspa-codec-bluez5-ldac.so" \
          "$dropin"
    rmdir -p "$plugin_dir" 2>/dev/null || true
    rmdir -p "$(dirname "$dropin")" 2>/dev/null || true
    systemctl --user daemon-reload
    systemctl --user restart wireplumber
    printf 'Done. WirePlumber is back on the stock plugins.\n'
}

case "${1:-}" in
    --check) check; exit 0 ;;
    --uninstall) uninstall; exit 0 ;;
    '') ;;
    *) die "unknown option $1" ;;
esac

# -- prerequisites ------------------------------------------------------------

# Asking the compiler beats looking in /usr/include: it accounts for CPATH, a
# local build in /usr/local, and layouts other than Fedora's.
have_header() { echo "#include <$1>" | gcc -E -x c - >/dev/null 2>&1; }

missing=()
command -v gcc >/dev/null || missing+=(gcc)
command -v curl >/dev/null || missing+=(curl)
command -v gcc >/dev/null && {
    have_header ldacBT.h || missing+=(libldac-devel)
    have_header bluetooth/bluetooth.h || missing+=(bluez-libs-devel)
}
if (( ${#missing[@]} )); then
    printf 'Missing build dependencies. Install them with:\n\n  sudo dnf install %s\n\n' "${missing[*]}" >&2
    exit 1
fi

[[ -n ${ldacdec_src:-} ]] ||
    die "no libldacdec sources -- in a clone, run: git submodule update --init third_party/libldacdec"
[[ -n ${shim_src:-} ]] || die "no ldacBT_dec.c beside this script"

version="$(pipewire --version 2>/dev/null | awk '/Linked with libpipewire/{print $4; exit}')"
[[ -n $version ]] || die 'could not determine the running PipeWire version'

# The plugin is loaded by libspa-bluez5.so, which refuses any codec plugin
# built against a different SPA_VERSION_BLUEZ5_CODEC_MEDIA. Building from the
# matching release is what keeps that check happy across PipeWire upgrades --
# and why this script must be re-run after one.
src="$cache/pipewire-$version"
if [[ ! -d $src ]]; then
    say "Fetching the PipeWire $version sources"
    mkdir -p "$cache"
    curl -fsSL -o "$cache/pipewire-$version.tar.gz" \
        "https://gitlab.freedesktop.org/pipewire/pipewire/-/archive/$version/pipewire-$version.tar.gz"
    tar -xzf "$cache/pipewire-$version.tar.gz" -C "$cache"
    rm -f "$cache/pipewire-$version.tar.gz"
fi
[[ -f $src/spa/plugins/bluez5/a2dp-codec-ldac.c ]] ||
    die "the PipeWire $version sources do not look complete: $src"

# -- build --------------------------------------------------------------------

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
say "Building the LDAC decoder against PipeWire $version"

# libldacdec, plus the adapter that gives it Sony's decoder ABI.
for unit in libldacdec bit_allocation huffCodes bit_reader utility imdct spectrum; do
    gcc -O3 -std=gnu11 -fPIC -w -c "$ldacdec_src/$unit.c" \
        -I "$ldacdec_src" -o "$work/$unit.o"
done
gcc -O2 -std=gnu11 -Wall -Wextra -fPIC -c "$shim_src/ldacBT_dec.c" \
    -I "$ldacdec_src" -o "$work/ldacBT_dec.o"
gcc -shared -Wl,-soname,libldacBT_dec.so.2 -o "$work/libldacBT_dec.so.2" "$work"/*.o -lm

# PipeWire's own codec plugin, this time with the decode path compiled in.
#
# Meson would hand the plugin a generated config.h; none of the three files
# below reads anything out of it, so an empty one satisfies the include and
# saves configuring the whole tree to build one shared object.
#
# $ORIGIN/../.. resolves to $prefix, so the installed plugin finds the decoder
# beside it without anyone having to set LD_LIBRARY_PATH.
: > "$work/config.h"
gcc -O2 -std=gnu11 -fPIC -shared \
    -DCODEC_PLUGIN -DENABLE_LDAC_ABR -DENABLE_LDAC_DEC \
    -I "$work" -I "$src/spa/include" \
    "$src/spa/plugins/bluez5/a2dp-codec-ldac.c" \
    "$src/spa/plugins/bluez5/media-codecs.c" \
    -o "$work/libspa-codec-bluez5-ldac.so" \
    -L "$work" -l:libldacBT_dec.so.2 -lldacBT_enc -lldacBT_abr \
    -Wl,-rpath,'$ORIGIN/../..' || die 'the codec plugin did not build'

nm -D -u "$work/libspa-codec-bluez5-ldac.so" | grep -q ldacBT_decode ||
    die 'the plugin built without the decode path -- ENABLE_LDAC_DEC did not take'

# -- install ------------------------------------------------------------------

say 'Installing'
plugins="$(system_plugins)"
[[ -n $plugins ]] ||
    die "could not find PipeWire's SPA plugin directory -- is PipeWire installed?"
printf 'PipeWire plugins: %s\n' "$plugins"

mkdir -p "$plugin_dir" "$(dirname "$dropin")"
install -m 0755 "$work/libldacBT_dec.so.2" "$prefix/libldacBT_dec.so.2"
install -m 0755 "$work/libspa-codec-bluez5-ldac.so" "$plugin_dir/libspa-codec-bluez5-ldac.so"

# SPA_PLUGIN_DIR is a colon-separated search path and the first match wins, so
# naming Tessera's directory ahead of the system one replaces exactly one
# plugin and leaves the other thirteen alone.
cat > "$dropin" <<CONF
# Written by scripts/build-ldac-decoder.sh. Delete it, or run that script with
# --uninstall, to go back to the stock plugins.
#
# SPA_PLUGIN_DIR is searched left to right and the first hit wins: this puts
# Tessera's LDAC codec plugin -- the one built with decoding enabled -- ahead
# of Fedora's, and changes nothing else.
[Service]
Environment=SPA_PLUGIN_DIR=$prefix/spa-0.2:$plugins
CONF

systemctl --user daemon-reload
systemctl --user restart wireplumber
sleep 3

say 'Result'
check
