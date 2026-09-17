package dev.tessera.companion.features

import android.Manifest
import android.bluetooth.BluetoothManager
import android.bluetooth.le.AdvertiseCallback
import android.bluetooth.le.AdvertiseData
import android.bluetooth.le.AdvertiseSettings
import android.bluetooth.le.BluetoothLeAdvertiser
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import android.os.ParcelUuid
import android.util.Log
import java.util.UUID

/**
 * A Bluetooth LE beacon the computer watches for signal strength, to lock
 * itself when this phone walks away. Nothing but a fixed UUID and the first
 * bytes of the device id are advertised.
 */
object Beacon {

    private const val TAG = "TesseraBeacon"
    val SERVICE: UUID = UUID.fromString("7e55e7a0-5e1f-4d3a-9c2b-a1b2c3d40001")

    private var advertiser: BluetoothLeAdvertiser? = null
    private var users = 0

    fun allowed(context: Context): Boolean =
        Build.VERSION.SDK_INT < Build.VERSION_CODES.S ||
            context.checkSelfPermission(Manifest.permission.BLUETOOTH_ADVERTISE) == PackageManager.PERMISSION_GRANTED

    fun supported(context: Context): Boolean =
        context.getSystemService(BluetoothManager::class.java)?.adapter?.isMultipleAdvertisementSupported == true

    /** Starts advertising, or keeps advertising, for one more computer. Null when it worked. */
    @Synchronized
    fun start(context: Context, deviceId: String): String? {
        if (!allowed(context)) return "The phone has not allowed Tessera to advertise over Bluetooth."
        val adapter = context.getSystemService(BluetoothManager::class.java)?.adapter
            ?: return "This phone has no Bluetooth."
        if (!adapter.isEnabled) return "Bluetooth is off on the phone."
        users++
        if (advertiser != null) return null
        val le = adapter.bluetoothLeAdvertiser ?: run { users--; return "This phone cannot advertise over Bluetooth LE." }
        val settings = AdvertiseSettings.Builder()
            .setAdvertiseMode(AdvertiseSettings.ADVERTISE_MODE_BALANCED)
            .setTxPowerLevel(AdvertiseSettings.ADVERTISE_TX_POWER_MEDIUM)
            .setConnectable(false)
            .setTimeout(0)
            .build()
        val uuid = ParcelUuid(SERVICE)
        val data = AdvertiseData.Builder().addServiceUuid(uuid).setIncludeDeviceName(false).build()
        val tag = deviceId.take(8).toByteArray(Charsets.US_ASCII)
        val response = AdvertiseData.Builder().addServiceData(uuid, tag).build()
        return try {
            le.startAdvertising(settings, data, response, callback)
            advertiser = le
            null
        } catch (e: SecurityException) {
            users--
            "The phone refused to advertise: ${e.message}"
        }
    }

    @Synchronized
    fun stop() {
        users = (users - 1).coerceAtLeast(0)
        if (users > 0) return
        runCatching { advertiser?.stopAdvertising(callback) }
        advertiser = null
    }

    private val callback = object : AdvertiseCallback() {
        override fun onStartSuccess(settingsInEffect: AdvertiseSettings?) {
            Log.i(TAG, "advertising")
        }

        override fun onStartFailure(errorCode: Int) {
            Log.w(TAG, "advertising failed: $errorCode")
            synchronized(this@Beacon) { advertiser = null; users = 0 }
        }
    }
}
