#!/usr/bin/env bash
# 

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

# Whether this session is managed by systemd.
has_systemd() { command -v systemctl >/dev/null && [[ -d /run/systemd/system ]]; }

restart_session_manager() {
    if has_systemd; then
        systemctl --user daemon-reload
        systemctl --user restart wireplumber
        return
    fi
    printf '\nThis session is not managed by systemd, so nothing was restarted\n'
    printf 'and no drop-in was written. Put this in the environment WirePlumber\n'
    printf 'starts with, then restart it however your system does that:\n\n'
    printf '  SPA_PLUGIN_DIR=%s/spa-0.2:%s\n\n' "$prefix" "$plugins"
}
die() { printf '%serror:%s %s\n' "$red" "$off" "$*" >&2; exit 1; }

# -- what is in place right now ----------------------------------------------

# The endpoints belong to WirePlumber's D-Bus name, not to any object under
# org.bluez, so they cannot be listed from BlueZ's object manager.
endpoints() {
    # Every WirePlumber restart registers a fresh set under a new D-Bus
    # name, so the endpoints that count are the ones belonging to the
    # most recent sender.
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
        # No record either way.
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
    if has_systemd; then
        systemctl --user daemon-reload
        systemctl --user restart wireplumber
        printf 'Done. WirePlumber is back on the stock plugins.\n'
    else
        printf 'Done. Remove SPA_PLUGIN_DIR from WirePlumber'"'"'s environment and\n'
        printf 'restart it to go back to the stock plugins.\n'
    fi
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

# Package names differ by distribution, and advice that does not work is
# barely better than none.
os_family() {
    local id like
    id="$(. /etc/os-release 2>/dev/null && printf '%s' "${ID:-}")"
    like="$(. /etc/os-release 2>/dev/null && printf '%s' "${ID_LIKE:-}")"
    # Globs, not space-delimited words: openSUSE's ID is
    # "opensuse-tumbleweed", which a " opensuse " match misses entirely --
    # and the binary fallback below would then pick whatever manager happened
    # to be installed, which is how a test on this machine produced dnf.
    case " $id $like " in
        *fedora*|*rhel*|*centos*|*almalinux*|*rocky*) printf dnf; return ;;
        *debian*|*ubuntu*|*mint*)                     printf apt; return ;;
        *arch*)                                       printf pacman; return ;;
        *suse*)                                       printf zypper; return ;;
        *alpine*)                                     printf apk; return ;;
        *void*)                                       printf xbps; return ;;
        *gentoo*)                                     printf emerge; return ;;
        *solus*)                                      printf eopkg; return ;;
    esac
    # Not listed: whichever manager is actually installed.
    local candidate
    for candidate in dnf apt pacman zypper apk xbps-install; do
        command -v "$candidate" >/dev/null && { printf '%s' "${candidate%%-*}"; return; }
    done
}

# Package names differ by distribution, and advice that does not work is
# barely better than none.
package_for() {
    case "$(os_family):$1" in
        dnf:gcc|zypper:gcc|xbps:gcc|emerge:gcc|eopkg:gcc) printf gcc ;;
        apt:gcc)                        printf build-essential ;;
        pacman:gcc)                     printf base-devel ;;
        apk:gcc)                        printf build-base ;;
        *:curl)                         printf curl ;;
        apt:ldac)                       printf libldacbt-enc-dev ;;
        pacman:ldac)                    printf libldac ;;
        dnf:ldac|zypper:ldac|xbps:ldac) printf libldac-devel ;;
        dnf:bluez)                      printf bluez-libs-devel ;;
        apt:bluez)                      printf libbluetooth-dev ;;
        pacman:bluez)                   printf bluez-libs ;;
        zypper:bluez|xbps:bluez)        printf bluez-devel ;;
        apk:bluez)                      printf bluez-dev ;;
    esac
}

describe() {
    case "$1" in
        gcc)   printf 'a C compiler' ;;
        curl)  printf 'curl' ;;
        ldac)  printf "Sony's LDAC encoder headers" ;;
        bluez) printf 'the BlueZ development headers' ;;
        *)     printf '%s' "$1" ;;
    esac
}

install_line() {
    case "$(os_family)" in
        dnf)    printf 'sudo dnf install %s' "$*" ;;
        apt)    printf 'sudo apt install %s' "$*" ;;
        pacman) printf 'sudo pacman -S %s' "$*" ;;
        zypper) printf 'sudo zypper install %s' "$*" ;;
        apk)    printf 'sudo apk add %s' "$*" ;;
        xbps)   printf 'sudo xbps-install -S %s' "$*" ;;
        emerge) printf 'sudo emerge %s' "$*" ;;
        eopkg)  printf 'sudo eopkg install %s' "$*" ;;
        *)      printf 'install %s with your package manager' "$*" ;;
    esac
}

need=()
command -v gcc >/dev/null || need+=(gcc)
command -v curl >/dev/null || need+=(curl)
command -v gcc >/dev/null && {
    have_header ldacBT.h || need+=(ldac)
    have_header bluetooth/bluetooth.h || need+=(bluez)
}

if (( ${#need[@]} )); then
    named=() unnamed=()
    for item in "${need[@]}"; do
        package="$(package_for "$item")"
        if [[ -n $package ]]; then named+=("$package"); else unnamed+=("$(describe "$item")"); fi
    done
    printf 'Missing build dependencies.\n\n' >&2
    (( ${#named[@]} )) && printf '  %s\n\n' "$(install_line "${named[@]}")" >&2
    for item in "${unnamed[@]}"; do
        printf '  You also need %s, which has no known package on this system.\n' "$item" >&2
    done
    (( ${#unnamed[@]} )) && printf '\n' >&2
    exit 1
fi

[[ -n ${ldacdec_src:-} ]] ||
    die "no libldacdec sources -- in a clone, run: git submodule update --init third_party/libldacdec"
[[ -n ${shim_src:-} ]] || die "no ldacBT_dec.c beside this script"

version="$(pipewire --version 2>/dev/null | awk '/Linked with libpipewire/{print $4; exit}')"
[[ -n $version ]] || die 'could not determine the running PipeWire version'

# The plugin is loaded by libspa-bluez5.so, which refuses any codec plugin
# built against a different SPA_VERSION_BLUEZ5_CODEC_MEDIA.
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
if ! has_systemd; then
    plugins="${plugins:-$(system_plugins)}"
    restart_session_manager
    say 'Result'
    check
    exit 0
fi

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

restart_session_manager
sleep 3

say 'Result'
check
