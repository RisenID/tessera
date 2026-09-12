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
//
// The JDK, the SDK and Gradle itself are all under ~/android, and the build
// artefacts are the same kind of thing: large, machine-specific, and worthless
// to anyone cloning this. Keeping them there leaves the source tree small
// enough to read at a glance and removes any need to gitignore build products.
//
// TESSERA_BUILD_DIR overrides the location, which is what the build script
// uses to keep Gradle's own caches out of the tree as well.
val buildRoot = file(
    System.getenv("TESSERA_BUILD_DIR")
        ?: "${System.getProperty("user.home")}/android/build/tessera"
)

gradle.beforeProject {
    layout.buildDirectory.set(File(buildRoot, "modules/${project.path.replace(':', '/').trim('/').ifEmpty { "root" }}"))
}
