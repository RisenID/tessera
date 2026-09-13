#!/usr/bin/env bash
# Build the companion APK and install it on an attached phone.
#   ./scripts/build-companion.sh            debug build
#   ./scripts/build-companion.sh release    signed release build

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
# shellcheck source=/dev/null
source scripts/android-env.sh

mkdir -p "$TESSERA_BUILD_DIR"

VERSION="$(sed -n 's/^Version:[[:space:]]*//p' packaging/tessera.spec)"
VARIANT=debug
TASK="${1:-assembleDebug}"
if [[ ${1:-} == release ]]; then
    VARIANT=release
    TASK=assembleRelease
    if ! grep -qE '^\s*TESSERA_KEYSTORE\s*=' "$HOME/.gradle/gradle.properties" 2>/dev/null; then
        printf 'No TESSERA_KEYSTORE in ~/.gradle/gradle.properties; cannot sign a release.\n' >&2
        exit 1
    fi
fi
printf 'Building the companion app (%s) at version %s\n\n' "$VARIANT" "$VERSION"

cd android
gradle \
    --project-cache-dir "$TESSERA_BUILD_DIR/project-cache" \
    -Pkotlin.project.persistent.dir="$TESSERA_BUILD_DIR/kotlin" \
    "$TASK"

APK="$TESSERA_BUILD_DIR/modules/app/outputs/apk/$VARIANT/app-$VARIANT.apk"
if [[ ! -f "$APK" ]]; then
    printf '\nNo APK at %s -- check the Gradle output above.\n' "$APK" >&2
    exit 1
fi
printf '\nBuilt %s (%s), version %s\n' "$APK" "$(du -h "$APK" | cut -f1)" "$VERSION"

if [[ $VARIANT == release ]]; then
    APKSIGNER="$(ls -d "$ANDROID_HOME"/build-tools/*/apksigner 2>/dev/null | sort -V | tail -1)"
    if [[ -z $APKSIGNER ]]; then
        printf 'apksigner not found; skipping signature check.\n' >&2
    else
        SIGNATURE="$("$APKSIGNER" verify --print-certs "$APK" 2>&1 || true)"
        if ! grep -q '^Verifies' <<<"$SIGNATURE" && ! grep -qE 'Signer.*certificate DN' <<<"$SIGNATURE"; then
            printf 'The APK is not signed:\n%s\n' "$SIGNATURE" >&2
            exit 1
        fi
        grep -E 'Signer.*certificate (DN|SHA-256)' <<<"$SIGNATURE"
    fi
fi

# Prefer USB when the phone is attached both ways.
SERIAL="$(adb devices | awk 'NR>1 && $2=="device" {print $1}' |
          sort -t. -k1,1 | grep -v '\.' | head -1 || true)"
[[ -z $SERIAL ]] && SERIAL="$(adb devices | awk 'NR>1 && $2=="device" {print $1; exit}')"

if [[ -z $SERIAL ]]; then
    printf 'No phone attached over adb. Copy the APK across and install it manually.\n'
    exit 0
fi

printf 'Installing on %s...\n' "$SERIAL"
if ! OUT="$(adb -s "$SERIAL" install -r "$APK" 2>&1)"; then
    printf '%s\n' "$OUT" >&2
    if grep -q UPDATE_INCOMPATIBLE <<<"$OUT"; then
        printf '\nThe installed app has a different signature. Uninstall it first (this clears\n' >&2
        printf 'pairing and permissions): adb -s %s uninstall dev.tessera.companion\n' "$SERIAL" >&2
    fi
    exit 1
fi
printf 'Installed version %s\n' \
    "$(adb -s "$SERIAL" shell dumpsys package dev.tessera.companion |
       sed -n 's/.*versionName=//p' | head -1 | tr -d '\r')"
