# Toolchain for building the companion app.
#
#   source scripts/android-env.sh
#
# Everything the Android build needs lives under ~/android and nothing lives in
# the checkout: the JDK, the SDK, Gradle itself and all build output. Fedora 44
# ships only JDK 25, which the Android Gradle Plugin rejects, so JDK 21 sits
# beside the SDK rather than replacing the system Java.

export ANDROID_SDK_ROOT="${ANDROID_SDK_ROOT:-$HOME/android/Sdk}"
export ANDROID_HOME="$ANDROID_SDK_ROOT"
export JAVA_HOME="${JAVA_HOME_ANDROID:-$HOME/android/jdk}"
export GRADLE_HOME="$HOME/android/gradle"

# Where Gradle writes: build products, its project cache, and Kotlin's session
# files. Overridable so a second checkout does not fight this one.
export TESSERA_BUILD_DIR="${TESSERA_BUILD_DIR:-$HOME/android/build/tessera}"

# SDK tools ahead of the system ones so `adb` matches the platform tools the
# build used; Gradle ahead of any distribution package.
export PATH="$JAVA_HOME/bin:$GRADLE_HOME/bin:$ANDROID_HOME/platform-tools:$ANDROID_HOME/cmdline-tools/latest/bin:$PATH"

if [ -n "${BASH_VERSION:-}${ZSH_VERSION:-}" ]; then
    printf 'Android toolchain ready: JDK %s, Gradle %s, SDK %s\n' \
        "$("$JAVA_HOME/bin/java" -version 2>&1 | head -1 | cut -d'"' -f2)" \
        "$("$GRADLE_HOME/bin/gradle" --version 2>/dev/null | awk '/^Gradle /{print $2; exit}')" \
        "$ANDROID_HOME"
fi
