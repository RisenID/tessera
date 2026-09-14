package dev.tessera.companion.features

import android.content.Context
import android.content.pm.PackageManager
import android.util.Log
import rikka.shizuku.Shizuku
import java.io.BufferedReader

/** Runs shell commands with the shell UID, via Shizuku. */
object PrivilegedShell {

    private const val TAG = "TesseraShell"

    data class Output(val code: Int, val text: String) {
        val ok: Boolean get() = code == 0 && !text.contains("error", ignoreCase = true)
    }

    /** [value] as one single-quoted shell word. */
    fun quote(value: String): String = "'" + value.replace("'", "'\\''") + "'"

    /** True when the Shizuku service is running and reachable. */
    fun available(): Boolean = runCatching { Shizuku.pingBinder() }.getOrDefault(false)

    fun hasPermission(): Boolean = runCatching {
        if (!Shizuku.pingBinder()) return false
        // Pre-v11 Shizuku used a normal runtime permission instead of its own grant.
        if (Shizuku.isPreV11()) return false
        Shizuku.checkSelfPermission() == PackageManager.PERMISSION_GRANTED
    }.getOrDefault(false)

    fun requestPermission(requestCode: Int) {
        runCatching { Shizuku.requestPermission(requestCode) }
            .onFailure { Log.w(TAG, "permission request failed: ${it.message}") }
    }

    /** Executes *command* as the shell user. */
    fun run(command: String, timeoutMillis: Long = 30_000): Output {
        if (!hasPermission()) return Output(-1, "Shizuku is not available")

        return runCatching {
            val method = Shizuku::class.java.getDeclaredMethod(
                "newProcess", Array<String>::class.java, Array<String>::class.java, String::class.java
            ).apply { isAccessible = true }

            val process = method.invoke(
                null, arrayOf("sh", "-c", command), null, null
            ) ?: return Output(-1, "could not start a privileged shell")

            val processClass = process.javaClass
            val inputStream = processClass.getMethod("getInputStream")
                .invoke(process) as java.io.InputStream
            val errorStream = processClass.getMethod("getErrorStream")
                .invoke(process) as java.io.InputStream

            // Both streams at once, so a full stderr pipe cannot stall stdout.
            var stdout = ""
            var stderr = ""
            val readers = listOf(
                kotlin.concurrent.thread { stdout = inputStream.bufferedReader().use(BufferedReader::readText) },
                kotlin.concurrent.thread { stderr = errorStream.bufferedReader().use(BufferedReader::readText) },
            )

            val finished = runCatching {
                processClass.getMethod("waitForTimeout", Long::class.java, java.util.concurrent.TimeUnit::class.java)
                    .invoke(process, timeoutMillis, java.util.concurrent.TimeUnit.MILLISECONDS) as Boolean
            }.getOrDefault(true)
            if (!finished) {
                runCatching { processClass.getMethod("destroy").invoke(process) }
                readers.forEach { it.join(1_000) }
                return Output(-1, "timed out: $command")
            }
            readers.forEach { it.join(timeoutMillis) }

            val code = runCatching {
                processClass.getMethod("exitValue").invoke(process) as Int
            }.getOrDefault(0)

            Output(code, (stdout + stderr).trim())
        }.getOrElse { error ->
            Log.w(TAG, "privileged command failed", error)
            Output(-1, error.message ?: "privileged shell failed")
        }
    }
}

/** Hotspot control, best path first. */
object Hotspot {

    private const val TAG_HOTSPOT = "TesseraHotspot"

    enum class Mode { PRIVILEGED, PANEL_ONLY }

    /** Whether `cmd wifi` is actually usable, cached after the first probe. */
    @Volatile
    private var wifiCommandUsable: Boolean? = null

    private fun canDriveWifi(): Boolean {
        wifiCommandUsable?.let { return it }
        if (!PrivilegedShell.hasPermission()) return false
        val result = PrivilegedShell.run("cmd wifi is-softap-enabled")
        val usable = !result.text.contains("does not have access", ignoreCase = true) &&
            !result.text.contains("SecurityException", ignoreCase = true)
        wifiCommandUsable = usable
        return usable
    }

    /**
     * Privileged when either route works: the tethering binder (preferred) or,
     * on builds that still allow it, `cmd wifi`.
     */
    fun mode(): Mode =
        if (TetheringController.available() || canDriveWifi()) Mode.PRIVILEGED else Mode.PANEL_ONLY

    /** Turns the hotspot on. */
    fun start(context: android.content.Context, ssid: String, passphrase: String, band: String): String? {
        if (!PrivilegedShell.hasPermission()) return "Shizuku is not running on the phone."

        if (TetheringController.available()) {
            // A hotspot that is already running keeps the configuration it started with, so asking
            // for a different SSID, passphrase or band silently does nothing.
            val wantsCustom = ssid.isNotBlank() && passphrase.length >= 8
            if (wantsCustom && activeBand() != null) {
                android.util.Log.i(TAG_HOTSPOT, "restarting the hotspot to apply new settings")
                TetheringController.stop(context)
                Thread.sleep(1500)
            }

            val error = TetheringController.start(context, ssid, passphrase, band)
            if (error == null) return null
            if (!canDriveWifi()) return error
            android.util.Log.i("TesseraHotspot", "binder route failed ($error); trying cmd wifi")
        }

        if (!canDriveWifi()) {
            return "This phone refuses Wi-Fi commands to the Shizuku shell " +
                "(softap commands need root). Use the tethering panel instead."
        }

        val bandFlag = if (band == "5") "-b 5" else "-b 2"
        val attempts = buildList {
            if (ssid.isNotBlank() && passphrase.length >= 8) {
                add(
                    "cmd -w wifi start-softap ${PrivilegedShell.quote(ssid)} wpa2 " +
                        "${PrivilegedShell.quote(passphrase)} $bandFlag"
                )
            }
            add("cmd -w wifi start-softap")
        }

        val problems = mutableListOf<String>()
        for (command in attempts) {
            val result = PrivilegedShell.run(command)
            if (result.ok) return null
            problems += result.text.ifBlank { "no output" }
        }
        return "Could not start the hotspot: ${problems.joinToString("; ")}"
    }

    fun stop(context: android.content.Context): String? {
        if (!PrivilegedShell.hasPermission()) return "Shizuku is not running on the phone."

        if (TetheringController.available()) {
            val error = TetheringController.stop(context)
            if (error == null) return null
            if (!canDriveWifi()) return error
        }
        if (!canDriveWifi()) return "This phone refuses Wi-Fi commands to the Shizuku shell."
        val result = PrivilegedShell.run("cmd -w wifi stop-softap")
        return if (result.ok) null else result.text.ifBlank { "Could not stop the hotspot." }
    }

    /** Current hotspot SSID and passphrase, so the desktop can join it. */
    fun config(): Pair<String, String>? {
        if (!canDriveWifi()) return null
        val result = PrivilegedShell.run("cmd wifi get-softap-config")
        if (!result.ok) return null
        val ssid = Regex("SSID\\s*[:=]\\s*\"?([^\"\\n,]+)").find(result.text)?.groupValues?.get(1)
        val passphrase = Regex("(?:passphrase|preSharedKey)\\s*[:=]\\s*\"?([^\"\\n,]+)")
            .find(result.text)?.groupValues?.get(1)
        return ssid?.trim()?.takeIf { it.isNotEmpty() }?.let { it to passphrase?.trim().orEmpty() }
    }

    fun enabled(): Boolean? {
        if (!canDriveWifi()) return null
        val result = PrivilegedShell.run("cmd wifi is-softap-enabled")
        if (!result.ok) return null
        val lower = result.text.lowercase()
        return when {
            lower.contains("not enabled") || lower.contains("disabled") -> false
            lower.contains("enabled") || lower.contains("true") -> true
            else -> null
        }
    }

    /** The band the hotspot is actually running on, read back after starting. */
    fun activeBand(): Pair<String, Int>? {
        if (!PrivilegedShell.hasPermission()) return null

        // The AP publishes its channel a moment after tethering reports started, so a single read
        // straight after the callback usually finds an empty info map.
        var frequency = 0
        repeat(6) { attempt ->
            // Read the live info map rather than the first "frequency=" in the dump: the
            // dump also contains callback log lines carrying "frequency= 0" and stale values
            // from earlier sessions.
            val result = PrivilegedShell.run("dumpsys wifi | grep mCurrentSoftApInfoMap")
            frequency = Regex("frequency\\s*=\\s*(\\d+)")
                .findAll(result.text)
                .mapNotNull { it.groupValues[1].toIntOrNull() }
                .firstOrNull { it > 0 } ?: 0
            if (frequency > 0) return@repeat
            if (attempt < 5) Thread.sleep(700)
        }
        if (frequency <= 0) return null

        val band = when {
            frequency >= 5925 -> "6"
            frequency >= 4900 -> "5"
            else -> "2.4"
        }
        return band to frequency
    }

    /** Fallback: the settings panel, one tap on the phone. */
    fun openPanel(context: Context) {
        val intent = android.content.Intent().apply {
            component = android.content.ComponentName(
                "com.android.settings",
                "com.android.settings.TetherSettings",
            )
            flags = android.content.Intent.FLAG_ACTIVITY_NEW_TASK
        }
        runCatching { context.startActivity(intent) }.onFailure {
            // Samsung moves this activity around; fall back to wireless settings.
            runCatching {
                context.startActivity(
                    android.content.Intent(android.provider.Settings.ACTION_WIRELESS_SETTINGS)
                        .addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK)
                )
            }
        }
    }
}
