package dev.tessera.companion

import android.content.Context
import android.content.SharedPreferences
import java.security.SecureRandom

/** Paired desktops and the server's identity. */
class Store(context: Context) {

    private val appContext = context.applicationContext

    private val prefs: SharedPreferences =
        context.getSharedPreferences("tessera", Context.MODE_PRIVATE)

    var port: Int
        get() = prefs.getInt(KEY_PORT, DEFAULT_PORT)
        set(value) = prefs.edit().putInt(KEY_PORT, value).apply()

    /** Stable id for this phone, generated once. */
    val deviceId: String
        get() {
            prefs.getString(KEY_DEVICE_ID, null)?.let { return it }
            val generated = randomHex(16)
            prefs.edit().putString(KEY_DEVICE_ID, generated).apply()
            return generated
        }

    /** What this phone calls itself. */
    var displayName: String
        get() = prefs.getString(KEY_NAME, null) ?: deviceName()
        set(value) = prefs.edit().putString(KEY_NAME, value).apply()

    private fun deviceName(): String {
        val resolver = appContext.contentResolver
        // Settings.Global.DEVICE_NAME is where "Ruchit's S25" lives; the Bluetooth name is the same
        // string on most phones and the fallback where it is not set.
        val named = runCatching {
            android.provider.Settings.Global.getString(resolver, "device_name")
        }.getOrNull()
        val bluetooth = runCatching {
            android.provider.Settings.Secure.getString(resolver, "bluetooth_name")
        }.getOrNull()
        return named?.takeIf { it.isNotBlank() }
            ?: bluetooth?.takeIf { it.isNotBlank() }
            ?: android.os.Build.MODEL
    }

    fun tokens(): Set<String> = prefs.getStringSet(KEY_TOKENS, emptySet()) ?: emptySet()

    fun isKnown(token: String): Boolean = token.isNotEmpty() && tokens().contains(token)

    data class Computer(val token: String, val name: String, val lastSeen: Long)

    /** Paired computers, most recently connected first. */
    fun computers(): List<Computer> = tokens().map { token ->
        Computer(
            token,
            prefs.getString(KEY_COMPUTER_NAME + token, null) ?: "Unnamed computer",
            prefs.getLong(KEY_COMPUTER_SEEN + token, 0L),
        )
    }.sortedByDescending { it.lastSeen }

    fun addToken(name: String = ""): String {
        val token = randomHex(32)
        prefs.edit().putStringSet(KEY_TOKENS, tokens() + token).apply()
        noteComputer(token, name)
        return token
    }

    /** Records a computer's name and that it just connected. */
    fun noteComputer(token: String, name: String) {
        val edit = prefs.edit().putLong(KEY_COMPUTER_SEEN + token, System.currentTimeMillis())
        if (name.isNotBlank()) edit.putString(KEY_COMPUTER_NAME + token, name)
        edit.apply()
    }

    fun revoke(token: String) {
        prefs.edit()
            .putStringSet(KEY_TOKENS, tokens() - token)
            .remove(KEY_COMPUTER_NAME + token)
            .remove(KEY_COMPUTER_SEEN + token)
            .apply()
    }

    fun revokeAll() {
        prefs.edit().remove(KEY_TOKENS).apply()
    }

    val pairedCount: Int
        get() = tokens().size

    companion object {
        const val DEFAULT_PORT = 8765
        private const val KEY_PORT = "port"
        private const val KEY_DEVICE_ID = "device_id"
        private const val KEY_NAME = "display_name"
        private const val KEY_TOKENS = "tokens"
        private const val KEY_COMPUTER_NAME = "computer_name_"
        private const val KEY_COMPUTER_SEEN = "computer_seen_"

        private val random = SecureRandom()

        fun randomHex(bytes: Int): String {
            val buffer = ByteArray(bytes)
            random.nextBytes(buffer)
            return buffer.joinToString("") { "%02x".format(it) }
        }

        /** Six digits, shown on the phone and typed on the desktop. */
        fun pairingCode(): String = "%06d".format(random.nextInt(1_000_000))
    }
}
