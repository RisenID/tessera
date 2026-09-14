package dev.tessera.companion.features

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.media.AudioManager
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.wifi.WifiManager
import android.os.BatteryManager
import android.os.Build
import android.telephony.PhoneStateListener
import android.telephony.SignalStrength
import android.telephony.TelephonyCallback
import android.telephony.TelephonyManager
import android.util.Log
import dev.tessera.companion.Bus
import org.json.JSONObject
import java.util.concurrent.Executors

/** Battery, network and ringer state, for the desktop's device panel. */
object PhoneStatus {

    private const val TAG = "TesseraStatus"

    private var users = 0
    private var receiver: BroadcastReceiver? = null
    private var netCallback: ConnectivityManager.NetworkCallback? = null
    private var telephonyCallback: Any? = null
    private var legacySignal: PhoneStateListener? = null
    private val executor = Executors.newSingleThreadExecutor { runnable ->
        Thread(runnable, "tessera-status")
    }

    @Volatile private var battery: JSONObject? = null
    @Volatile private var cellLevel = -1
    @Volatile private var last = ""

    @Synchronized
    fun addUser(context: Context) {
        users++
        if (users == 1) register(context.applicationContext)
    }

    @Synchronized
    fun removeUser(context: Context) {
        users = (users - 1).coerceAtLeast(0)
        if (users == 0) unregister(context.applicationContext)
    }

    /** The state right now, for a desktop that has just connected. */
    fun snapshot(context: Context): JSONObject {
        val app = context.applicationContext
        if (battery == null) readBattery(app)
        return JSONObject()
            .put("t", "status")
            .put("battery", battery ?: JSONObject())
            .put("wifi", wifi(app))
            .put("cell", cell(app))
            .put("ringer", ringer(app))
            .put("volume", mediaVolume(app))
    }

    /** Sets the ringer to "normal", "vibrate" or "silent". */
    fun setRinger(context: Context, mode: String): Boolean {
        val value = when (mode) {
            "normal" -> AudioManager.RINGER_MODE_NORMAL
            "vibrate" -> AudioManager.RINGER_MODE_VIBRATE
            "silent" -> AudioManager.RINGER_MODE_SILENT
            else -> return false
        }
        val audio = context.getSystemService(AudioManager::class.java) ?: return false
        return runCatching {
            audio.ringerMode = value
            audio.ringerMode == value
        }.onFailure { Log.w(TAG, "could not set the ringer", it) }.getOrDefault(false)
    }

    /** Media volume, 0 to 100. Bluetooth audio on the desktop follows it. */
    fun setMediaVolume(context: Context, percent: Int): Boolean {
        if (percent !in 0..100) return false
        val audio = context.getSystemService(AudioManager::class.java) ?: return false
        return runCatching {
            val max = audio.getStreamMaxVolume(AudioManager.STREAM_MUSIC).coerceAtLeast(1)
            audio.setStreamVolume(AudioManager.STREAM_MUSIC, (percent * max + 50) / 100, 0)
            true
        }.onFailure { Log.w(TAG, "could not set the media volume", it) }.getOrDefault(false)
    }

    // -- registration --------------------------------------------------------

    private fun register(context: Context) {
        val filter = IntentFilter().apply {
            addAction(Intent.ACTION_BATTERY_CHANGED)
            addAction(AudioManager.RINGER_MODE_CHANGED_ACTION)
        }
        val listener = object : BroadcastReceiver() {
            override fun onReceive(from: Context?, intent: Intent?) {
                if (intent?.action == Intent.ACTION_BATTERY_CHANGED) {
                    battery = describeBattery(context, intent)
                }
                publish(context)
            }
        }
        // ACTION_BATTERY_CHANGED is sticky, so this returns the current level
        // without waiting for the next change.
        runCatching {
            val sticky = context.registerReceiver(listener, filter)
            receiver = listener
            if (sticky != null) battery = describeBattery(context, sticky)
        }.onFailure { Log.w(TAG, "could not watch battery", it) }

        watchNetwork(context)
        watchSignal(context)
        Log.i(TAG, "watching phone status")
    }

    private fun unregister(context: Context) {
        receiver?.let { runCatching { context.unregisterReceiver(it) } }
        receiver = null

        val connectivity = context.getSystemService(ConnectivityManager::class.java)
        netCallback?.let { runCatching { connectivity?.unregisterNetworkCallback(it) } }
        netCallback = null

        val telephony = context.getSystemService(TelephonyManager::class.java)
        runCatching {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                (telephonyCallback as? TelephonyCallback)?.let(
                    telephony!!::unregisterTelephonyCallback
                )
            } else {
                @Suppress("DEPRECATION")
                legacySignal?.let { telephony?.listen(it, PhoneStateListener.LISTEN_NONE) }
            }
        }
        telephonyCallback = null
        legacySignal = null
        battery = null
        cellLevel = -1
        last = ""
        Log.i(TAG, "stopped watching phone status")
    }

    private fun watchNetwork(context: Context) {
        val connectivity = context.getSystemService(ConnectivityManager::class.java) ?: return
        val callback = object : ConnectivityManager.NetworkCallback() {
            override fun onCapabilitiesChanged(network: Network, caps: NetworkCapabilities) =
                publish(context)

            override fun onAvailable(network: Network) = publish(context)
            override fun onLost(network: Network) = publish(context)
        }
        runCatching {
            connectivity.registerDefaultNetworkCallback(callback)
            netCallback = callback
        }.onFailure { Log.w(TAG, "could not watch the network", it) }
    }

    private fun watchSignal(context: Context) {
        val telephony = context.getSystemService(TelephonyManager::class.java) ?: return
        runCatching {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                val listener = object : TelephonyCallback(),
                    TelephonyCallback.SignalStrengthsListener {
                    override fun onSignalStrengthsChanged(strength: SignalStrength) {
                        cellLevel = strength.level
                        publish(context)
                    }
                }
                telephony.registerTelephonyCallback(executor, listener)
                telephonyCallback = listener
            } else {
                @Suppress("DEPRECATION")
                val listener = object : PhoneStateListener() {
                    @Deprecated("Superseded by TelephonyCallback on API 31+")
                    override fun onSignalStrengthsChanged(strength: SignalStrength?) {
                        cellLevel = strength?.level ?: -1
                        publish(context)
                    }
                }
                @Suppress("DEPRECATION")
                telephony.listen(listener, PhoneStateListener.LISTEN_SIGNAL_STRENGTHS)
                legacySignal = listener
            }
        }.onFailure { Log.w(TAG, "could not watch signal strength", it) }
    }

    /** Publishes only when something actually changed: these sources are chatty. */
    private fun publish(context: Context) {
        if (!Bus.hasSubscribers) return
        val message = snapshot(context)
        val text = message.toString()
        if (text == last) return
        last = text
        Bus.publish(message)
    }

    // -- readers -------------------------------------------------------------

    private fun readBattery(context: Context) {
        runCatching {
            val sticky = context.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
            if (sticky != null) battery = describeBattery(context, sticky)
        }
    }

    private fun describeBattery(context: Context, intent: Intent): JSONObject {
        val level = intent.getIntExtra(BatteryManager.EXTRA_LEVEL, -1)
        val scale = intent.getIntExtra(BatteryManager.EXTRA_SCALE, 100).coerceAtLeast(1)
        val status = intent.getIntExtra(BatteryManager.EXTRA_STATUS, -1)
        val plugged = intent.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0)
        val tenths = intent.getIntExtra(BatteryManager.EXTRA_TEMPERATURE, 0)

        val body = JSONObject()
            .put("level", if (level < 0) -1 else level * 100 / scale)
            .put("charging", status == BatteryManager.BATTERY_STATUS_CHARGING ||
                status == BatteryManager.BATTERY_STATUS_FULL)
            .put(
                "status",
                when (status) {
                    BatteryManager.BATTERY_STATUS_CHARGING -> "charging"
                    BatteryManager.BATTERY_STATUS_FULL -> "full"
                    BatteryManager.BATTERY_STATUS_NOT_CHARGING -> "idle"
                    BatteryManager.BATTERY_STATUS_DISCHARGING -> "discharging"
                    else -> ""
                }
            )
            .put(
                "source",
                when (plugged) {
                    BatteryManager.BATTERY_PLUGGED_AC -> "ac"
                    BatteryManager.BATTERY_PLUGGED_USB -> "usb"
                    BatteryManager.BATTERY_PLUGGED_WIRELESS -> "wireless"
                    else -> ""
                }
            )
            .put(
                "health",
                when (intent.getIntExtra(BatteryManager.EXTRA_HEALTH, -1)) {
                    BatteryManager.BATTERY_HEALTH_GOOD -> "good"
                    BatteryManager.BATTERY_HEALTH_OVERHEAT -> "overheating"
                    BatteryManager.BATTERY_HEALTH_DEAD -> "dead"
                    BatteryManager.BATTERY_HEALTH_COLD -> "cold"
                    BatteryManager.BATTERY_HEALTH_OVER_VOLTAGE -> "over voltage"
                    else -> ""
                }
            )
        if (tenths != 0) body.put("temperature", tenths / 10.0)

        val manager = context.getSystemService(BatteryManager::class.java)
        if (manager != null) {
            // Current is in microamps and signed: negative while discharging on
            // most phones, positive on some. The desktop only shows magnitude.
            runCatching {
                manager.getIntProperty(BatteryManager.BATTERY_PROPERTY_CURRENT_NOW)
            }.getOrNull()?.takeIf { it != Int.MIN_VALUE && it != 0 }
                ?.let { body.put("current", it / 1000) }
            runCatching { manager.computeChargeTimeRemaining() }
                .getOrNull()?.takeIf { it > 0 }?.let { body.put("toFull", it) }
        }
        return body
    }

    private fun wifi(context: Context): JSONObject {
        val body = JSONObject().put("connected", false)
        val connectivity = context.getSystemService(ConnectivityManager::class.java)
            ?: return body
        val caps = runCatching {
            connectivity.getNetworkCapabilities(connectivity.activeNetwork)
        }.getOrNull() ?: return body
        if (!caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)) return body
        body.put("connected", true)

        val manager = context.getSystemService(WifiManager::class.java) ?: return body
        @Suppress("DEPRECATION")
        val rssi = runCatching { manager.connectionInfo?.rssi }.getOrNull() ?: return body
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            body.put("level", manager.calculateSignalLevel(rssi))
                .put("max", manager.maxSignalLevel)
        } else {
            @Suppress("DEPRECATION")
            body.put("level", WifiManager.calculateSignalLevel(rssi, 5)).put("max", 4)
        }
        return body
    }

    private fun cell(context: Context): JSONObject {
        val body = JSONObject()
        val telephony = context.getSystemService(TelephonyManager::class.java) ?: return body
        // Not every read is granted: network type needs READ_PHONE_STATE, which
        // the user may have refused.
        runCatching { telephony.networkOperatorName }.getOrNull()
            ?.takeIf { it.isNotBlank() }?.let { body.put("operator", it) }
        val level = if (cellLevel >= 0) cellLevel
        else runCatching { telephony.signalStrength?.level ?: -1 }.getOrDefault(-1)
        if (level >= 0) body.put("level", level).put("max", 4)
        runCatching { networkType(telephony.dataNetworkType) }.getOrNull()
            ?.takeIf { it.isNotEmpty() }?.let { body.put("type", it) }
        return body
    }

    private fun networkType(type: Int): String = when (type) {
        TelephonyManager.NETWORK_TYPE_NR -> "5G"
        TelephonyManager.NETWORK_TYPE_LTE -> "LTE"
        TelephonyManager.NETWORK_TYPE_HSPAP,
        TelephonyManager.NETWORK_TYPE_HSPA,
        TelephonyManager.NETWORK_TYPE_UMTS -> "3G"
        TelephonyManager.NETWORK_TYPE_EDGE,
        TelephonyManager.NETWORK_TYPE_GPRS -> "2G"
        else -> ""
    }

    private fun ringer(context: Context): String {
        val audio = context.getSystemService(AudioManager::class.java) ?: return ""
        return when (runCatching { audio.ringerMode }.getOrDefault(-1)) {
            AudioManager.RINGER_MODE_SILENT -> "silent"
            AudioManager.RINGER_MODE_VIBRATE -> "vibrate"
            AudioManager.RINGER_MODE_NORMAL -> "normal"
            else -> ""
        }
    }

    private fun mediaVolume(context: Context): Int {
        val audio = context.getSystemService(AudioManager::class.java) ?: return -1
        return runCatching {
            val max = audio.getStreamMaxVolume(AudioManager.STREAM_MUSIC).coerceAtLeast(1)
            audio.getStreamVolume(AudioManager.STREAM_MUSIC) * 100 / max
        }.getOrDefault(-1)
    }
}
