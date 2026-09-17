package dev.risenid.tessera.features

import android.content.Context
import android.content.ContextWrapper
import android.os.IBinder
import android.util.Log
import org.lsposed.hiddenapibypass.HiddenApiBypass
import rikka.shizuku.ShizukuBinderWrapper
import rikka.shizuku.SystemServiceHelper
import java.lang.reflect.Proxy
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executor
import java.util.concurrent.TimeUnit
import java.util.function.Supplier

/** Starts and stops the Wi-Fi hotspot with shell-level privileges. */
object TetheringController {

    private const val TAG = "TesseraTether"

    /** TetheringManager.TETHERING_WIFI */
    private const val TETHERING_WIFI = 0

    /** SoftApConfiguration.SECURITY_TYPE_* */
    private const val SECURITY_WPA2_PSK = 1
    private const val SECURITY_WPA3_SAE = 3

    /** SoftApConfiguration.BAND_* */
    private const val BAND_2GHZ = 1
    private const val BAND_5GHZ = 2
    private const val BAND_6GHZ = 4

    private const val AWAIT_SECONDS = 25L

    /**
     * The tethering service validates the caller package against the calling
     * uid, and the calling uid here is shell. Presenting our own package name
     * would be rejected, so the manager is built on a context that reports the
     * shell package instead.
     */
    private class ShellContext(base: Context) : ContextWrapper(base) {
        override fun getOpPackageName(): String = "com.android.shell"
        override fun getPackageName(): String = "com.android.shell"
        override fun getAttributionTag(): String? = null
    }

    fun available(): Boolean = PrivilegedShell.hasPermission()

    /** Turns the hotspot on. Returns null on success, or a message to show. */
    fun start(
        context: Context,
        ssid: String,
        passphrase: String,
        band: String,
    ): String? {
        if (!available()) return "Shizuku is not running on the phone."

        return runCatching {
            val manager = tetheringManager(context)
            val latch = CountDownLatch(1)
            val failure = arrayOfNulls<String>(1)
            val callback = startCallback(latch, failure)
            val executor = Executor { it.run() }

            val useCustomConfig = ssid.isNotBlank() && passphrase.length >= 8
            if (useCustomConfig) {
                // Write the configuration into the platform's soft-AP store first.
                val stored = storeSoftApConfig(context, ssid, passphrase, band)

                if (stored) {
                    HiddenApiBypass.invoke(
                        manager.javaClass, manager, "startTethering",
                        TETHERING_WIFI, executor, callback,
                    )
                } else {
                    val request = tetheringRequest(ssid, passphrase, band)
                    HiddenApiBypass.invoke(
                        manager.javaClass, manager, "startTethering",
                        request, executor, callback,
                    )
                }
            } else {
                HiddenApiBypass.invoke(
                    manager.javaClass, manager, "startTethering",
                    TETHERING_WIFI, executor, callback,
                )
            }

            if (!latch.await(AWAIT_SECONDS, TimeUnit.SECONDS)) {
                return "The phone did not report back within ${AWAIT_SECONDS}s."
            }
            failure[0]
        }.getOrElse { error ->
            Log.w(TAG, "startTethering failed", error)
            describe(error)
        }
    }

    fun stop(context: Context): String? {
        if (!available()) return "Shizuku is not running on the phone."
        return runCatching {
            val manager = tetheringManager(context)
            HiddenApiBypass.invoke(manager.javaClass, manager, "stopTethering", TETHERING_WIFI)
            null
        }.getOrElse { error ->
            Log.w(TAG, "stopTethering failed", error)
            describe(error)
        }
    }

    // -- plumbing ------------------------------------------------------------

    private fun tetheringManager(context: Context): Any {
        // The binder is fetched through Shizuku so that the call arrives at the
        // system with the shell uid, which is the whole point.
        val binder: IBinder = ShizukuBinderWrapper(SystemServiceHelper.getSystemService("tethering"))
        val supplier = Supplier { binder }

        val managerClass = Class.forName("android.net.TetheringManager")
        return HiddenApiBypass.newInstance(
            managerClass,
            ShellContext(context.applicationContext),
            supplier,
        )
    }

    /** Saves the soft-AP configuration as the shell user. */
    private fun storeSoftApConfig(
        context: Context,
        ssid: String,
        passphrase: String,
        band: String,
    ): Boolean = runCatching {
        val binder: IBinder = ShizukuBinderWrapper(SystemServiceHelper.getSystemService("wifi"))
        val stubClass = Class.forName("android.net.wifi.IWifiManager\$Stub")
        val service = stubClass
            .getMethod("asInterface", IBinder::class.java)
            .invoke(null, binder)

        val wifiManagerClass = Class.forName("android.net.wifi.WifiManager")
        val wifiManager = HiddenApiBypass.newInstance(
            wifiManagerClass,
            ShellContext(context.applicationContext),
            service,
        )

        val applied = HiddenApiBypass.invoke(
            wifiManagerClass, wifiManager, "setSoftApConfiguration",
            softApConfig(ssid, passphrase, band),
        )
        (applied as? Boolean) == true
    }.getOrElse {
        Log.w(TAG, "could not store the soft AP configuration", it)
        false
    }

    /** Builds a SoftApConfiguration with the requested SSID, secret and band. */
    private fun softApConfig(ssid: String, passphrase: String, band: String): Any {
        val softApClass = Class.forName("android.net.wifi.SoftApConfiguration\$Builder")
        val builder = HiddenApiBypass.newInstance(softApClass)
        HiddenApiBypass.invoke(softApClass, builder, "setSsid", ssid)

        // 6 GHz mandates WPA3-SAE; asking for WPA2 there is rejected outright.
        val security = if (band == "6") SECURITY_WPA3_SAE else SECURITY_WPA2_PSK
        HiddenApiBypass.invoke(softApClass, builder, "setPassphrase", passphrase, security)
        HiddenApiBypass.invoke(
            softApClass, builder, "setBand",
            when (band) {
                "6" -> BAND_6GHZ
                "5" -> BAND_5GHZ
                else -> BAND_2GHZ
            },
        )
        return HiddenApiBypass.invoke(softApClass, builder, "build")!!
    }

    private fun tetheringRequest(ssid: String, passphrase: String, band: String): Any {
        val softApConfig = softApConfig(ssid, passphrase, band)

        val builderClass = Class.forName("android.net.TetheringManager\$TetheringRequest\$Builder")
        val builder = HiddenApiBypass.newInstance(builderClass, TETHERING_WIFI)
        HiddenApiBypass.invoke(builderClass, builder, "setSoftApConfiguration", softApConfig)
        return HiddenApiBypass.invoke(builderClass, builder, "build")!!
    }

    /**
     * StartTetheringCallback is an interface, so a dynamic proxy stands in for
     * it rather than compiling against a class the public SDK does not expose.
     */
    private fun startCallback(latch: CountDownLatch, failure: Array<String?>): Any {
        val callbackClass = Class.forName("android.net.TetheringManager\$StartTetheringCallback")
        return Proxy.newProxyInstance(
            callbackClass.classLoader,
            arrayOf(callbackClass),
        ) { _, method, args ->
            when (method.name) {
                "onTetheringStarted" -> {
                    failure[0] = null
                    latch.countDown()
                }

                "onTetheringFailed" -> {
                    val code = (args?.firstOrNull() as? Int) ?: -1
                    failure[0] = describeTetherError(code)
                    latch.countDown()
                }
            }
            null
        }
    }

    /** TetheringManager.TETHER_ERROR_* values worth explaining. */
    private fun describeTetherError(code: Int): String = when (code) {
        5 -> "The phone refused to enable tethering (TETHER_ERROR_UNAVAIL_IFACE)."
        11 -> "Your mobile plan does not permit tethering (entitlement check failed)."
        13 -> "Tethering is disallowed on this phone, often by a device admin."
        14 -> "Wi-Fi tethering could not start; another Wi-Fi mode may be active."
        else -> "The phone could not start the hotspot (error $code)."
    }

    private fun describe(error: Throwable): String {
        val cause = error.cause ?: error
        return when {
            cause is SecurityException ->
                "The phone rejected the request: ${cause.message}"
            cause is ClassNotFoundException || cause is NoSuchMethodException ->
                "This Android build does not expose the tethering API this way " +
                    "(${cause.message}). Use the tethering panel instead."
            else -> cause.message ?: "The hotspot could not be started."
        }
    }
}
