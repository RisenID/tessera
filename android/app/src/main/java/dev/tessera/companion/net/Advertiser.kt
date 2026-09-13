package dev.tessera.companion.net

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkRequest
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.os.Handler
import android.os.Looper
import android.util.Log

/**
 * Announces the phone on the local network so the desktop finds it without the
 * user typing an IP address.
 */
class Advertiser(private val context: Context) {

    private var manager: NsdManager? = null
    private var listener: NsdManager.RegistrationListener? = null
    private var connectivity: ConnectivityManager? = null
    private var networks: ConnectivityManager.NetworkCallback? = null

    /** What to advertise, kept so a re-registration needs no arguments. */
    private var settings: Settings? = null

    private val handler = Handler(Looper.getMainLooper())

    private data class Settings(val port: Int, val deviceId: String, val displayName: String)

    fun start(port: Int, deviceId: String, displayName: String) {
        stop()
        settings = Settings(port, deviceId, displayName)
        watchNetworks()
        register()
    }

    fun stop() {
        settings = null
        handler.removeCallbacksAndMessages(null)
        unwatchNetworks()
        unregister()
    }

    // -- registration --------------------------------------------------------

    private fun register() {
        val current = settings ?: return
        val nsd = context.getSystemService(NsdManager::class.java) ?: return

        val info = NsdServiceInfo().apply {
            serviceName = current.displayName.ifBlank { android.os.Build.MODEL }
            serviceType = SERVICE_TYPE
            setPort(current.port)
            setAttribute("id", current.deviceId)
            setAttribute("model", android.os.Build.MODEL)
            setAttribute("v", "1")
        }

        val registration = object : NsdManager.RegistrationListener {
            override fun onServiceRegistered(info: NsdServiceInfo) {
                Log.i(TAG, "advertising as ${info.serviceName}")
            }

            override fun onRegistrationFailed(info: NsdServiceInfo, errorCode: Int) {
                // Usually transient -- registering while the platform is still tearing the previous
                // one down, or before the new network has an address.
                Log.w(TAG, "mDNS registration failed ($errorCode); retrying")
                scheduleRetry()
            }

            override fun onServiceUnregistered(info: NsdServiceInfo) {
                Log.d(TAG, "stopped advertising")
            }

            override fun onUnregistrationFailed(info: NsdServiceInfo, errorCode: Int) {
                Log.d(TAG, "mDNS unregistration failed ($errorCode)")
            }
        }

        runCatching { nsd.registerService(info, NsdManager.PROTOCOL_DNS_SD, registration) }
            .onSuccess {
                manager = nsd
                listener = registration
            }
            .onFailure {
                Log.w(TAG, "could not advertise: ${it.message}")
                scheduleRetry()
            }
    }

    private fun unregister() {
        val nsd = manager
        val registration = listener
        if (nsd != null && registration != null) {
            runCatching { nsd.unregisterService(registration) }
        }
        manager = null
        listener = null
    }

    /** Re-register, after letting the platform settle. */
    private fun scheduleRetry() {
        if (settings == null) return
        handler.removeCallbacksAndMessages(null)
        handler.postDelayed({
            if (settings == null) return@postDelayed
            unregister()
            handler.postDelayed({ register() }, SETTLE_MS)
        }, RETRY_MS)
    }

    // -- following the network ----------------------------------------------

    private fun watchNetworks() {
        val service = context.getSystemService(ConnectivityManager::class.java) ?: return
        val callback = object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(network: Network) = readvertise("network available")
            override fun onLost(network: Network) = readvertise("network lost")
        }
        runCatching {
            service.registerNetworkCallback(NetworkRequest.Builder().build(), callback)
        }.onSuccess {
            connectivity = service
            networks = callback
        }.onFailure { Log.w(TAG, "could not watch for network changes", it) }
    }

    private fun unwatchNetworks() {
        val service = connectivity
        val callback = networks
        if (service != null && callback != null) {
            runCatching { service.unregisterNetworkCallback(callback) }
        }
        connectivity = null
        networks = null
    }

    private fun readvertise(reason: String) {
        if (settings == null) return
        Log.i(TAG, "re-advertising: $reason")
        scheduleRetry()
    }

    companion object {
        private const val TAG = "TesseraNsd"
        const val SERVICE_TYPE = "_tessera._tcp"

        /** Coalesces the burst of callbacks a single network change produces. */
        private const val RETRY_MS = 1_500L
        private const val SETTLE_MS = 1_000L
    }
}
