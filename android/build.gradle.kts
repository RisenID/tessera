plugins {
    // AGP 9.4 is the first line that compiles against API 37 (Android 17).
    // It needs Gradle 9.6+ and JDK 17+, and brings its own Kotlin support --
    // the separate org.jetbrains.kotlin.android plugin is rejected from 9.0.
    id("com.android.application") version "9.4.0" apply false
}
