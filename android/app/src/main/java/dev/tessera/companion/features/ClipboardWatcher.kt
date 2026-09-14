package dev.tessera.companion.features

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.PowerManager
import android.util.Log
import dev.tessera.companion.Bus
import org.json.JSONObject
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledExecutorService
import java.util.concurrent.TimeUnit

/** Publishes clipboard changes to connected desktops. */
object ClipboardWatcher {

    private const val TAG = "TesseraClipboard"
    private const val INTERVAL_SECONDS = 2L

    private var executor: ScheduledExecutorService? = null
    private var users = 0

    /** Last value seen, so a change is only announced once. */
    @Volatile
    private var lastSeen: String? = null

    /** When the clipboard last changed, as far as Tessera saw. 0 if unknown. */
    @Volatile
    var changedAt: Long = 0L
        private set

    /** Whether a desktop wants clipboard changes at all. */
    val watching: Boolean
        get() = users > 0

    @Synchronized
    fun addUser(context: Context? = null) {
        users++
        if (context != null) watchScreen(context)
        // Polling is for Shizuku only.
        if (executor == null && ClipboardBridge.viaShizuku()) {
            lastSeen = ClipboardBridge.read()
            executor = Executors.newSingleThreadScheduledExecutor { runnable ->
                Thread(runnable, "tessera-clipboard")
            }.also {
                it.scheduleWithFixedDelay(
                    ::poll, INTERVAL_SECONDS, INTERVAL_SECONDS, TimeUnit.SECONDS
                )
                Log.i(TAG, "watching the clipboard")
            }
        }
    }

    @Synchronized
    fun removeUser(context: Context? = null) {
        users = (users - 1).coerceAtLeast(0)
        if (users == 0) {
            executor?.shutdownNow()
            executor = null
            if (context != null) unwatchScreen(context)
            Log.i(TAG, "stopped watching the clipboard")
        }
    }

    /** Whether the poll should do anything at all right now. */
    @Volatile
    private var screenOn = true

    private var receiver: BroadcastReceiver? = null

    private fun watchScreen(context: Context) {
        if (receiver != null) return
        val manager = context.getSystemService(PowerManager::class.java)
        screenOn = manager?.isInteractive ?: true
        val listener = object : BroadcastReceiver() {
            override fun onReceive(context: Context?, intent: Intent?) {
                when (intent?.action) {
                    Intent.ACTION_SCREEN_ON -> {
                        screenOn = true
                        // Something may have been copied while we were not
                        // looking -- on the lock screen, or by another app.
                        poll()
                    }
                    Intent.ACTION_SCREEN_OFF -> screenOn = false
                }
            }
        }
        val filter = IntentFilter().apply {
            addAction(Intent.ACTION_SCREEN_ON)
            addAction(Intent.ACTION_SCREEN_OFF)
        }
        runCatching { context.applicationContext.registerReceiver(listener, filter) }
            .onSuccess { receiver = listener }
            .onFailure { Log.w(TAG, "could not watch the screen state", it) }
    }

    private fun unwatchScreen(context: Context) {
        val listener = receiver ?: return
        receiver = null
        runCatching { context.applicationContext.unregisterReceiver(listener) }
    }

    /** Records a value so it is not announced; [changed] when it is a real new copy. */
    fun note(text: String, changed: Boolean = true) {
        lastSeen = text
        if (changed) changedAt = System.currentTimeMillis()
    }

    /** Reads once, now, and announces a change. Blocks; not on the main thread. */
    fun checkNow() {
        if (!watching) return
        val current = runCatching { ClipboardBridge.read() }.getOrNull() ?: return
        if (current == lastSeen || current.isEmpty()) return
        lastSeen = current
        changedAt = System.currentTimeMillis()
        Bus.publish(JSONObject().put("t", "clipboard").put("text", current))
    }

    private fun poll() {
        // The screen-on receiver calls this too, and must not take focus.
        if (!screenOn || !ClipboardBridge.viaShizuku()) return
        val current = runCatching { ClipboardBridge.read() }.getOrNull() ?: return
        if (current == lastSeen || current.isEmpty()) return
        lastSeen = current
        changedAt = System.currentTimeMillis()
        Bus.publish(JSONObject().put("t", "clipboard").put("text", current))
    }
}
