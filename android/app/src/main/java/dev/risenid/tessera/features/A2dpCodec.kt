package dev.risenid.tessera.features

import android.Manifest
import android.annotation.SuppressLint
import android.bluetooth.BluetoothA2dp
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothManager
import android.bluetooth.BluetoothProfile
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.os.Build
import android.util.Log
import org.json.JSONObject

/** The codec this phone sends Bluetooth music with. */
object A2dpCodec {

    private const val TAG = "TesseraCodec"
    private const val CODEC_CHANGED = "android.bluetooth.a2dp.profile.action.CODEC_CONFIG_CHANGED"

    private val NAMES = mapOf(0 to "SBC", 1 to "AAC", 2 to "aptX", 3 to "aptX HD", 4 to "LDAC", 5 to "LC3", 6 to "Opus")
    private val RATES = mapOf(1 to 44100, 2 to 48000, 4 to 88200, 8 to 96000, 16 to 176400, 32 to 192000)
    private val BITS = mapOf(1 to 16, 2 to 24, 4 to 32)

    @Volatile
    private var proxy: BluetoothA2dp? = null
    private var receiver: BroadcastReceiver? = null

    fun allowed(context: Context): Boolean =
        Build.VERSION.SDK_INT < Build.VERSION_CODES.S ||
            context.checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT) ==
            PackageManager.PERMISSION_GRANTED

    /** Watches the codec; [changed] runs whenever it or playback changes. */
    @Synchronized
    fun start(context: Context, changed: () -> Unit) {
        if (proxy != null || receiver != null || !allowed(context)) return
        val adapter = context.getSystemService(BluetoothManager::class.java)?.adapter ?: return
        runCatching {
            adapter.getProfileProxy(
                context,
                object : BluetoothProfile.ServiceListener {
                    override fun onServiceConnected(profile: Int, service: BluetoothProfile) {
                        proxy = service as? BluetoothA2dp
                        changed()
                    }

                    override fun onServiceDisconnected(profile: Int) {
                        proxy = null
                    }
                },
                BluetoothProfile.A2DP,
            )
            val watcher = object : BroadcastReceiver() {
                override fun onReceive(context: Context, intent: Intent) = changed()
            }
            val filter = IntentFilter(CODEC_CHANGED).apply {
                addAction(BluetoothA2dp.ACTION_PLAYING_STATE_CHANGED)
            }
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                context.registerReceiver(watcher, filter, Context.RECEIVER_EXPORTED)
            } else {
                context.registerReceiver(watcher, filter)
            }
            receiver = watcher
        }.onFailure { Log.w(TAG, "cannot watch the A2DP codec", it) }
    }

    @Synchronized
    fun stop(context: Context) {
        receiver?.let { runCatching { context.unregisterReceiver(it) } }
        receiver = null
        proxy?.let { a2dp ->
            runCatching {
                context.getSystemService(BluetoothManager::class.java)?.adapter
                    ?.closeProfileProxy(BluetoothProfile.A2DP, a2dp)
            }
        }
        proxy = null
    }

    /** codec, rate, bits and device for whatever is playing, or null. */
    @SuppressLint("MissingPermission")
    fun describe(): JSONObject? {
        val a2dp = proxy ?: return null
        return runCatching {
            val devices = a2dp.connectedDevices
            val device = devices.firstOrNull { a2dp.isA2dpPlaying(it) } ?: devices.firstOrNull()
                ?: return null
            // getCodecStatus is hidden before Android 13; reflection reaches both.
            val status = BluetoothA2dp::class.java
                .getMethod("getCodecStatus", BluetoothDevice::class.java)
                .invoke(a2dp, device) ?: return null
            val config = status.javaClass.getMethod("getCodecConfig").invoke(status) ?: return null
            fun number(getter: String) =
                (config.javaClass.getMethod(getter).invoke(config) as? Number)?.toInt() ?: -1
            // What the other end accepts: shows whether better than SBC is possible.
            val offered = org.json.JSONArray()
            (status.javaClass.getMethod("getCodecsSelectableCapabilities").invoke(status) as? List<*>)
                ?.forEach { capability ->
                    val type = capability?.javaClass?.getMethod("getCodecType")?.invoke(capability) as? Number
                    NAMES[type?.toInt()]?.let { if (!offered.toString().contains("\"$it\"")) offered.put(it) }
                }
            JSONObject()
                .put("offered", offered)
                .put("codec", NAMES[number("getCodecType")] ?: "")
                .put("rate", RATES[number("getSampleRate")] ?: 0)
                .put("bits", BITS[number("getBitsPerSample")] ?: 0)
                .put("device", device.name.orEmpty())
        }.onFailure { Log.d(TAG, "no codec status", it) }.getOrNull()
    }
}
