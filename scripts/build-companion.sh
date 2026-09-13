#!/usr/bin/env bash
# 

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
# shellcheck source=/dev/null
source scripts/android-env.sh

mkdir -p "$TESSERA_BUILD_DIR"

# Reported here as well as stamped into the APK, because the number the build
# picked up is the one thing worth checking before installing it on a phone.
VERSION="$(sed -n 's/^Version:[[:space:]]*//p' packaging/tessera.spec)"
printf 'Building the companion app at version %s\n\n' "$VERSION"

cd android
gradle \
    --project-cache-dir "$TESSERA_BUILD_DIR/project-cache" \
    -Pkotlin.project.persistent.dir="$TESSERA_BUILD_DIR/kotlin" \
    "${1:-assembleDebug}"

APK="$TESSERA_BUILD_DIR/modules/app/outputs/apk/debug/app-debug.apk"
if [[ -f "$APK" ]]; then
    printf '\nBuilt %s (%s), version %s\n' \
        "$APK" "$(du -h "$APK" | cut -f1)" "$VERSION"
    # A phone reachable over both USB and wireless debugging shows up twice,
    # and adb refuses to guess between them.
    SERIAL="$(adb devices | awk 'NR>1 && $2=="device" {print $1}' |
              sort -t. -k1,1 | grep -v '\.' | head -1)"
    [[ -z $SERIAL ]] && SERIAL="$(adb devices | awk 'NR>1 && $2=="device" {print $1; exit}')"

    if [[ -n $SERIAL ]]; then
        printf 'Installing on %s...\n' "$SERIAL"
        adb -s "$SERIAL" install -r "$APK"
        printf 'Installed version %s\n' \
            "$(adb -s "$SERIAL" shell dumpsys package dev.tessera.companion |
               sed -n 's/.*versionName=//p' | head -1 | tr -d '\r')"
    else
        printf 'No phone attached over adb. Copy the APK across and install it manually.\n'
    fi
else
    printf '\nNo APK at %s -- check the Gradle output above.\n' "$APK" >&2
    exit 1
fi
