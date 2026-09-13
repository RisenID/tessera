#!/usr/bin/env bash
# 

set -euo pipefail

root="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"

bindir="${XDG_BIN_HOME:-$HOME/.local/bin}"
datadir="${XDG_DATA_HOME:-$HOME/.local/share}"
configdir="${XDG_CONFIG_HOME:-$HOME/.config}"

launcher="$bindir/tessera"
desktop="$datadir/applications/dev.tessera.Tessera.desktop"
icon="$datadir/icons/hicolor/scalable/apps/dev.tessera.Tessera.svg"
wireplumber="$configdir/wireplumber/wireplumber.conf.d/51-tessera-bluez.conf"

if [[ -t 1 ]]; then bold=$'\033[1m'; red=$'\033[31m'; off=$'\033[0m'
else bold=''; red=''; off=''; fi
say() { printf '\n%s%s%s\n' "$bold" "$*" "$off"; }
die() { printf '%serror:%s %s\n' "$red" "$off" "$*" >&2; exit 1; }

check() {
    printf 'launcher    : %s\n' "$([[ -f $launcher ]] && echo "$launcher" || echo 'not installed')"
    printf 'menu entry  : %s\n' "$([[ -f $desktop ]] && echo "$desktop" || echo 'not installed')"
    printf 'icon        : %s\n' "$([[ -f $icon ]] && echo "$icon" || echo 'not installed')"
    printf 'audio config: %s\n' "$([[ -f $wireplumber ]] && echo "$wireplumber" || echo 'not installed')"
    printf 'on PATH     : %s\n' "$(command -v tessera || echo 'no -- add '"$bindir"' to PATH')"
}

uninstall() {
    say 'Removing Tessera'
    # The WirePlumber file is left alone on purpose: Tessera rewrites it from
    # the audio quality setting, so it is the user's configuration by now
    # rather than ours to delete.
    rm -f "$launcher" "$desktop" "$icon"
    command -v update-desktop-database >/dev/null &&
        update-desktop-database "$datadir/applications" 2>/dev/null || true
    printf 'Done. %s was left in place -- delete it by hand if you want the\n' "$wireplumber"
    printf 'stock Bluetooth audio configuration back.\n'
}

case "${1:-}" in
    --check) check; exit 0 ;;
    --uninstall) uninstall; exit 0 ;;
    '') ;;
    *) die "unknown option $1" ;;
esac

# -- prerequisites ------------------------------------------------------------

command -v python3 >/dev/null || die 'python3 is not installed.'
python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' ||
    die "Tessera needs Python 3.11 or newer; this is $(python3 -V 2>&1)."

python3 -c 'import PySide6.QtWidgets' 2>/dev/null || {
    advice="$(python3 -c "
import sys; sys.path.insert(0, '$root')
from tessera.core import packages
print(packages.advice('pyside6'))
" 2>/dev/null || echo 'Install PySide6 with your package manager.')"
    die "PySide6 is not installed. $advice"
}

# -- install ------------------------------------------------------------------

say 'Installing for this user'
mkdir -p "$bindir" "$(dirname "$desktop")" "$(dirname "$icon")" "$(dirname "$wireplumber")"

cat > "$launcher" <<LAUNCHER
#!/usr/bin/env bash
# Written by scripts/install-user.sh. Runs Tessera from the checkout it was
# installed from, so a git pull is all an update takes.
exec python3 -m tessera "\$@"
LAUNCHER
# PYTHONPATH rather than a cd: the app must not care what directory it is in.
sed -i "2i export PYTHONPATH=\"$root\${PYTHONPATH:+:\$PYTHONPATH}\"" "$launcher"
chmod 0755 "$launcher"

sed "s|^Exec=tessera\$|Exec=$launcher|" \
    "$root/packaging/dev.tessera.Tessera.desktop" > "$desktop"
install -m0644 "$root/packaging/icons/dev.tessera.Tessera.svg" "$icon"

# WirePlumber needs the receive-side Bluetooth roles enabled before a
# phone can stream here.
if [[ -f $wireplumber ]]; then
    printf 'Bluetooth audio config already present, left as it is.\n'
else
    install -m0644 "$root/packaging/51-tessera-bluez.conf" "$wireplumber"
fi

command -v update-desktop-database >/dev/null &&
    update-desktop-database "$datadir/applications" 2>/dev/null || true

say 'Result'
check

case ":$PATH:" in
    *":$bindir:"*) ;;
    *) printf '\n%s is not on your PATH. Add it, or start Tessera from the\n' "$bindir"
       printf 'application menu, which uses the full path.\n' ;;
esac
