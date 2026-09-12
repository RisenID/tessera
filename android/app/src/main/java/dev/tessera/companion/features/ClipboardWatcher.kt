package dev.tessera.companion.features

import android.util.Log
import dev.tessera.companion.Bus
import org.json.JSONObject
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledExecutorService
import java.util.concurrent.TimeUnit

/**
 * Publishes clipboard changes to connected desktops.
 *
 * Android gives a background app no callback for clipboard changes -- the
 * listener only fires for an app allowed to read the clipboard in the first
 * place -- so the value is polled. To keep that honest about battery, polling
 * runs only while a desktop is actually subscribed, and reading the clipboard
 * is a single cheap binder call.
 */
object ClipboardWatcher {

    private const val TAG = "TesseraClipboard"
    private const val INTERVAL_SECONDS = 2L

    private var executor: ScheduledExecutorService? = null
    private var users = 0

    /** Last value seen, so a change is only announced once. */
    @Volatile
    private var lastSeen: String? = null

    @Synchronized
    fun addUser() {
        users++
        if (executor == null && ClipboardBridge.available()) {
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
    fun removeUser() {
        users = (users - 1).coerceAtLeast(0)
        if (users == 0) {
            executor?.shutdownNow()
            executor = null
            Log.i(TAG, "stopped watching the clipboard")
        }
    }

    /** Records a value the desktop just sent, so it is not echoed straight back. */
    fun note(text: String) {
        lastSeen = text
    }

    private fun poll() {
        val current = runCatching { ClipboardBridge.read() }.getOrNull() ?: return
        if (current == lastSeen || current.isEmpty()) return
        lastSeen = current
        Bus.publish(JSONObject().put("t", "clipboard").put("text", current))
    }
}
