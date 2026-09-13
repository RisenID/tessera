pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}
dependencyResolutionManagement {
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "TesseraCompanion"
include(":app")

// Build output lives with the toolchain, not in the checkout.
val buildRoot = file(
    System.getenv("TESSERA_BUILD_DIR")
        ?: "${System.getProperty("user.home")}/android/build/tessera"
)

gradle.beforeProject {
    layout.buildDirectory.set(File(buildRoot, "modules/${project.path.replace(':', '/').trim('/').ifEmpty { "root" }}"))
}
