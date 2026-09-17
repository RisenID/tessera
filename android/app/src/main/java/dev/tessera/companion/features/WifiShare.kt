package dev.tessera.companion.features

import android.content.Context
import android.content.Intent
import android.net.wifi.WifiManager
import android.net.wifi.WifiNetworkSuggestion
import android.os.Build
import android.provider.Settings

/** A Wi-Fi network the computer knows, offered to this phone to save. */
object WifiShare {

    /** Asks the phone to save the network. Null when the request went out. */
    fun add(context: Context, ssid: String, password: String, security: String): String? {
        if (ssid.isBlank()) return "The network has no name."
        val builder = WifiNetworkSuggestion.Builder().setSsid(ssid)
        when (security) {
            "open" -> Unit
            "wpa3" -> if (password.length >= 8) builder.setWpa3Passphrase(password) else return "WPA3 needs a password of at least 8 characters."
            "wpa2" -> if (password.length >= 8) builder.setWpa2Passphrase(password) else return "WPA2 needs a password of at least 8 characters."
            else -> return "Only open, WPA2 and WPA3 networks can be sent to the phone."
        }
        val suggestion = builder.build()

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            // The system's own "save this network?" sheet, which stores it as if
            // the user had typed it in.
            val intent = Intent(Settings.ACTION_WIFI_ADD_NETWORKS)
                .putParcelableArrayListExtra(Settings.EXTRA_WIFI_NETWORK_LIST, arrayListOf(suggestion))
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            return try {
                context.startActivity(intent)
                null
            } catch (e: Exception) {
                "The phone could not show the save dialog: ${e.message}"
            }
        }
        // Android 10: a suggestion the phone joins when it sees the network,
        // after a one-time notification asking whether that is all right.
        val manager = context.applicationContext.getSystemService(WifiManager::class.java)
            ?: return "No Wi-Fi service on the phone."
        return when (manager.addNetworkSuggestions(listOf(suggestion))) {
            WifiManager.STATUS_NETWORK_SUGGESTIONS_SUCCESS,
            WifiManager.STATUS_NETWORK_SUGGESTIONS_ERROR_ADD_DUPLICATE -> null
            else -> "The phone refused the network suggestion."
        }
    }
}
