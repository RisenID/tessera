package dev.risenid.tessera.features

import android.util.Log
import org.json.JSONArray
import org.json.JSONObject
import java.net.Inet4Address
import java.net.NetworkInterface

/** Every address this phone can currently be reached on. */
object NetworkAddresses {

    private const val TAG = "TesseraNet"

    /** Interfaces that never lead anywhere useful. */
    private val IGNORED = listOf("lo", "dummy", "sit", "tun", "rmnet_ims")

    /** A snapshot, newest-looking interfaces first. */
    fun list(): JSONArray {
        val entries = mutableListOf<JSONObject>()
        runCatching {
            for (face in NetworkInterface.getNetworkInterfaces()?.toList().orEmpty()) {
                val name = face.name.orEmpty()
                if (!face.isUp || face.isLoopback) continue
                if (IGNORED.any { name.startsWith(it) }) continue

                for (address in face.inetAddresses?.toList().orEmpty()) {
                    if (address.isLoopbackAddress || address.isLinkLocalAddress) continue
                    // IPv6 is skipped rather than sent: reaching a phone over a soft AP on IPv6
                    // needs a scope id the desktop cannot infer from the address alone, so it would
                    // only ever be noise.
                    if (address !is Inet4Address) continue
                    val host = address.hostAddress.orEmpty()
                    entries += JSONObject()
                        .put("interface", name)
                        .put("address", host)
                        .put("tether", looksLikeTether(name))
                        .put("softAp", isSoftAp(name, host))
                }
            }
        }.onFailure { Log.w(TAG, "could not list interfaces", it) }

        entries.sortByDescending { it.optBoolean("tether") }
        return JSONArray(entries)
    }

    /** Whether an interface name is one a soft AP or USB tether uses. */
    private fun looksLikeTether(name: String): Boolean =
        name.startsWith("ap") || name.startsWith("swlan") || name.startsWith("wlan1") ||
            name.startsWith("rndis") || name.startsWith("usb") || name.startsWith("bt-pan")

    /** Whether an address looks like a running Wi-Fi hotspot. */
    private fun isSoftAp(name: String, address: String): Boolean {
        if (name.startsWith("rndis") || name.startsWith("usb") || name.startsWith("bt-pan")) {
            return false
        }
        val wifiApName = name.startsWith("ap") || name.startsWith("swlan") || name.startsWith("wlan1")
        return wifiApName || address.endsWith(".1")
    }

    /** Whether a Wi-Fi hotspot is running right now. */
    fun hasSoftAp(): Boolean {
        val addresses = list()
        return (0 until addresses.length()).any {
            addresses.optJSONObject(it)?.optBoolean("softAp") == true
        }
    }

    /** The list once tethering has had a chance to bring its interface up. */
    fun listAfterTethering(waitMillis: Long = 6_000): JSONArray {
        val deadline = System.currentTimeMillis() + waitMillis
        var best = list()
        while (System.currentTimeMillis() < deadline) {
            if ((0 until best.length()).any { best.optJSONObject(it)?.optBoolean("tether") == true }) {
                return best
            }
            Thread.sleep(400)
            best = list()
        }
        return best
    }
}
