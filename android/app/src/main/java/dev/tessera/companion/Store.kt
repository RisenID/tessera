package dev.tessera.companion

import android.content.Context
import android.content.SharedPreferences
import java.security.SecureRandom

/**
 * Paired desktops and the server's identity.
 *
 * A desktop is identified by a token it presents on every connection. Tokens
 * are minted during pairing and can be revoked individually, so losing a laptop
 * does not mean re-pairing everything.
 */
class Store(context: Context) {

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

    var displayName: String
        get() = prefs.getString(KEY_NAME, null) ?: android.os.Build.MODEL
        set(value) = prefs.edit().putString(KEY_NAME, value).apply()

    fun tokens(): Set<String> = prefs.getStringSet(KEY_TOKENS, emptySet()) ?: emptySet()

    fun isKnown(token: String): Boolean = token.isNotEmpty() && tokens().contains(token)

    fun addToken(): String {
        val token = randomHex(32)
        prefs.edit().putStringSet(KEY_TOKENS, tokens() + token).apply()
        return token
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
