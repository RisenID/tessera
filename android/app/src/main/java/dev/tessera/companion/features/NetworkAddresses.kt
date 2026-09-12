package dev.tessera.companion.features

import android.util.Log
import org.json.JSONArray
import org.json.JSONObject
import java.net.Inet4Address
import java.net.NetworkInterface

/**
 * Every address this phone can currently be reached on.
 *
 * The desktop needs this to survive joining the phone's hotspot. Doing that
 * tears down the network the two were talking over, and the phone's address on
 * the new one is not knowable in advance: the tether interface is named
 * differently on every vendor ROM (ap0, swlan0, wlan1), its subnet is a
 * per-device choice, and mDNS is frequently not carried across a soft AP at
 * all. Guessing the gateway gets it right often enough to be misleading and
 * wrong often enough to strand the connection.
 *
 * So the phone says where it is, while the two can still talk, and the desktop
 * tries each one after it lands. The list is short and the addresses are the
 * phone's own, so there is nothing to leak: they are private-range addresses
 * for networks the desktop is about to be on.
 */
object NetworkAddresses {

    private const val TAG = "TesseraNet"

    /** Interfaces that never lead anywhere useful. */
    private val IGNORED = listOf("lo", "dummy", "sit", "tun", "rmnet_ims")

    /**
     * A snapshot, newest-looking interfaces first.
     *
     * Ordered so the desktop tries the likeliest address first: a soft AP is
     * always IPv4 in a private range, and the interface carrying it appears
     * only when tethering is on, so it sorts ahead of the station Wi-Fi and
     * mobile data addresses that were already there.
     */
    fun list(): JSONArray {
        val entries = mutableListOf<JSONObject>()
        runCatching {
            for (face in NetworkInterface.getNetworkInterfaces()?.toList().orEmpty()) {
                val name = face.name.orEmpty()
                if (!face.isUp || face.isLoopback) continue
                if (IGNORED.any { name.startsWith(it) }) continue

                for (address in face.inetAddresses?.toList().orEmpty()) {
                    if (address.isLoopbackAddress || address.isLinkLocalAddress) continue
                    // IPv6 is skipped rather than sent: reaching a phone over a
                    // soft AP on IPv6 needs a scope id the desktop cannot infer
                    // from the address alone, so it would only ever be noise.
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

    /**
     * Whether an interface name is one a soft AP or USB tether uses.
     *
     * Only a hint for ordering -- the desktop tries every address regardless,
     * so a ROM that names its tether interface something unheard of costs a
     * couple of seconds rather than the connection.
     */
    private fun looksLikeTether(name: String): Boolean =
        name.startsWith("ap") || name.startsWith("swlan") || name.startsWith("wlan1") ||
            name.startsWith("rndis") || name.startsWith("usb") || name.startsWith("bt-pan")

    /**
     * Whether an address looks like a running Wi-Fi hotspot.
     *
     * This is how the phone answers "is the hotspot on?" when it has no
     * privilege to ask properly: `cmd wifi is-softap-enabled` needs the shell
     * uid, so without Shizuku there is no supported call that says. A soft AP
     * does leave an unmistakable trace, though -- an interface that only
     * exists while tethering is on, holding the gateway address of its own
     * subnet.
     *
     * Both halves are needed. The name alone misses ROMs that run the AP on
     * wlan0, and the address alone would catch a phone on a network where it
     * happens to hold .1. USB and Bluetooth tethering are deliberately
     * excluded: there is no Wi-Fi network for the desktop to join.
     */
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

    /**
     * The list once tethering has had a chance to bring its interface up.
     *
     * Starting the hotspot returns before the interface exists, and the reply
     * carrying these addresses is the last thing the desktop hears before it
     * leaves the shared network -- so waiting a moment here is the difference
     * between handing over the address that matters and handing over a list
     * that is missing it.
     */
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
