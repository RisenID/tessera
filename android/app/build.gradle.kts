import java.security.KeyStore

plugins {
    // Kotlin support is built into AGP 9; applying the Kotlin plugin here is
    // an error.
    id("com.android.application")
}

// The two halves ship as one thing, so they carry one version number.
val specFile = layout.projectDirectory.file("../../packaging/tessera.spec")
val tesseraVersion: String = run {
    val text = providers.fileContents(specFile).asText.orNull
        ?: throw GradleException("cannot read ${specFile.asFile}, which holds the version")
    Regex("""(?m)^Version:\s*(\S+)""").find(text)?.groupValues?.get(1)
        ?: throw GradleException("no Version: line in ${specFile.asFile}")
}

// Android wants one integer that only ever grows, so the three parts are
// packed with room for 999 of each. 1.10.0 becomes 1_010_000.
val tesseraVersionCode: Int = tesseraVersion.split(".").let { parts ->
    fun part(index: Int) = parts.getOrNull(index)?.takeWhile { it.isDigit() }?.toIntOrNull() ?: 0
    part(0) * 1_000_000 + part(1) * 1_000 + part(2)
}

/** The keystore's only (or first) key alias, so it need not be configured. */
fun firstAlias(keystore: File, password: String): String {
    val store = listOf("PKCS12", "JKS").firstNotNullOfOrNull { type ->
        runCatching {
            KeyStore.getInstance(type).apply {
                keystore.inputStream().use { load(it, password.toCharArray()) }
            }
        }.getOrNull()
    } ?: throw GradleException("cannot open $keystore: wrong password or format")
    return store.aliases().toList().firstOrNull { store.isKeyEntry(it) }
        ?: throw GradleException("$keystore holds no signing key")
}

android {
    namespace = "dev.tessera.companion"
    compileSdk = 37        // Android 17

    defaultConfig {
        applicationId = "dev.tessera.companion"
        minSdk = 29        // Android 10: MediaStore and Camera2 behave consistently from here

        // Deliberately 36, not 37, while compiling against 37.
        targetSdk = 36

        versionCode = tesseraVersionCode
        versionName = tesseraVersion
    }

    // Release signing from ~/.gradle/gradle.properties: TESSERA_KEYSTORE and
    // TESSERA_KEYSTORE_PASSWORD, optionally TESSERA_KEY_ALIAS and TESSERA_KEY_PASSWORD.
    val keystorePath = providers.gradleProperty("TESSERA_KEYSTORE").orNull
    val keystorePassword = providers.gradleProperty("TESSERA_KEYSTORE_PASSWORD").orNull
    if (keystorePath != null && keystorePassword != null) {
        val keystoreFile = file(keystorePath)
        signingConfigs {
            create("release") {
                storeFile = keystoreFile
                storePassword = keystorePassword
                keyAlias = providers.gradleProperty("TESSERA_KEY_ALIAS").orNull
                    ?: firstAlias(keystoreFile, keystorePassword)
                keyPassword = providers.gradleProperty("TESSERA_KEY_PASSWORD").orNull
                    ?: keystorePassword
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            signingConfigs.findByName("release")?.let { signingConfig = it }
        }
    }

    packaging {
        resources {
            // MINA SSHD's jars each carry the same Maven metadata. None of it
            // is read at runtime.
            excludes += setOf(
                "META-INF/DEPENDENCIES",
                "META-INF/LICENSE*",
                "META-INF/NOTICE*",
            )
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    buildFeatures {
        // AGP 9 disables these by default; we bind views in MainActivity.
        viewBinding = true
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.19.0")
    implementation("androidx.appcompat:appcompat:1.7.0")
    // Material 3, for dynamic colour (Monet) and the M3 component set.
    implementation("com.google.android.material:material:1.14.0")
    // enableEdgeToEdge(); at targetSdk 35+ the system draws edge-to-edge whether
    // the app asks or not, so insets must be handled explicitly.
    implementation("androidx.activity:activity-ktx:1.13.0")
    implementation("androidx.constraintlayout:constraintlayout:2.2.0")
    implementation("androidx.lifecycle:lifecycle-service:2.8.7")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.9.0")

    // Shizuku: lets the app run commands as the shell user after the user starts the Shizuku
    // service once (via adb or wireless debugging).
    implementation("dev.rikka.shizuku:api:13.1.5")
    implementation("dev.rikka.shizuku:provider:13.1.5")

    // Reflecting on @SystemApi framework classes (TetheringManager, SoftApConfiguration) is
    // blocked by the non-SDK interface restrictions from Android 9 onwards; this lifts that
    // for our process only.
    implementation("org.lsposed.hiddenapibypass:hiddenapibypass:6.1")
    // The phone's storage on the desktop: an SFTP server the desktop mounts with sshfs.
    implementation("org.apache.sshd:sshd-core:2.15.0")
    implementation("org.apache.sshd:sshd-sftp:2.15.0")
}
