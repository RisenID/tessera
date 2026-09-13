package dev.tessera.companion.features

import android.content.Context
import android.os.Build
import android.telephony.PhoneStateListener
import android.telephony.TelephonyCallback
import android.telephony.TelephonyManager
import android.util.Log
import dev.tessera.companion.Bus
import org.json.JSONObject
import java.util.concurrent.Executors

/** Watches the phone's call state and announces it to connected desktops. */
object CallMonitor {

    private const val TAG = "TesseraCalls"

    private var users = 0
    private var callback: Any? = null
    private var legacy: PhoneStateListener? = null
    private val executor = Executors.newSingleThreadExecutor { runnable ->
        Thread(runnable, "tessera-calls")
    }

    @Volatile
    private var lastState = "idle"

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
    fun snapshot(context: Context): JSONObject = describe(lastState, context)

    private fun register(context: Context) {
        val telephony = context.getSystemService(TelephonyManager::class.java) ?: return

        runCatching {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                val listener = object : TelephonyCallback(), TelephonyCallback.CallStateListener {
                    override fun onCallStateChanged(state: Int) = publish(state, context)
                }
                telephony.registerTelephonyCallback(executor, listener)
                callback = listener
            } else {
                @Suppress("DEPRECATION")
                val listener = object : PhoneStateListener() {
                    @Deprecated("Superseded by TelephonyCallback on API 31+")
                    override fun onCallStateChanged(state: Int, phoneNumber: String?) =
                        publish(state, context)
                }
                @Suppress("DEPRECATION")
                telephony.listen(listener, PhoneStateListener.LISTEN_CALL_STATE)
                legacy = listener
            }
            Log.i(TAG, "watching call state")
        }.onFailure { Log.w(TAG, "could not watch call state", it) }
    }

    private fun unregister(context: Context) {
        val telephony = context.getSystemService(TelephonyManager::class.java) ?: return
        runCatching {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                (callback as? TelephonyCallback)?.let(telephony::unregisterTelephonyCallback)
            } else {
                @Suppress("DEPRECATION")
                legacy?.let { telephony.listen(it, PhoneStateListener.LISTEN_NONE) }
            }
        }
        callback = null
        legacy = null
        Log.i(TAG, "stopped watching call state")
    }

    private fun publish(state: Int, context: Context) {
        val name = when (state) {
            TelephonyManager.CALL_STATE_RINGING -> "ringing"
            TelephonyManager.CALL_STATE_OFFHOOK -> "active"
            else -> "idle"
        }
        if (name == lastState) return
        lastState = name
        Bus.publish(describe(name, context))
    }

    private fun describe(state: String, context: Context): JSONObject {
        val message = JSONObject().put("t", "call").put("state", state)
        if (state != "idle") {
            // The dialer's notification is the only source of the caller's
            // identity while a call is in progress.
            NotificationBridge.instance?.callerHint()?.let { (title, text) ->
                message.put("name", title).put("detail", text)
            }
        }
        message.put("canControl", CallsRepository.canControl(context))
        return message
    }
}
