package dev.tessera.companion.features

import android.app.Notification
import android.app.RemoteInput
import android.content.Intent
import android.os.Bundle
import android.provider.Telephony
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import android.util.Log
import dev.tessera.companion.Bus
import dev.tessera.companion.TesseraService
import dev.tessera.companion.Store
import org.json.JSONObject

/**
 * Mirrors notifications to the desktop and carries dismissals and replies back.
 *
 * Requires notification listener access, granted once by the user in Settings.
 * The platform pushes every event, so the app costs nothing while idle -- there
 * is no wakelock, no timer and no polling.
 */
class NotificationBridge : NotificationListenerService() {

    override fun onListenerConnected() {
        super.onListenerConnected()
        instance = this
        Log.i(TAG, "notification listener connected")

        // The platform binds this listener on boot, after an update and after
        // the process is reclaimed, which makes it a far more dependable
        // trigger than BOOT_COMPLETED for getting the link back up. Without
        // this the desktop reconnects forever to a port nothing is listening on
        // until someone opens the app by hand.
        if (Store(this).pairedCount > 0) {
            runCatching { TesseraService.start(this) }
                .onFailure { Log.w(TAG, "could not start the service", it) }
        }
        // A freshly connected desktop wants the current state, not just future
        // changes, so republish everything already on screen.
        Bus.publish(JSONObject().put("t", "dnd").put("mode", DndController.toName(currentInterruptionFilter)))
    }

    override fun onListenerDisconnected() {
        instance = null
        super.onListenerDisconnected()
    }

    override fun onNotificationPosted(sbn: StatusBarNotification?) {
        val notification = sbn ?: return
        if (shouldSkip(notification)) return
        Bus.publish(describe(notification))
    }

    override fun onNotificationRemoved(sbn: StatusBarNotification?) {
        val key = sbn?.key ?: return
        Bus.publish(JSONObject().put("t", "notification_removed").put("id", key))
    }

    override fun onInterruptionFilterChanged(interruptionFilter: Int) {
        // The event that replaces adb polling for Do Not Disturb.
        Bus.publish(
            JSONObject()
                .put("t", "dnd")
                .put("mode", DndController.toName(interruptionFilter))
        )
    }

    /**
     * Who is calling, taken from the dialer's own notification.
     *
     * From Android 12 the telephony callbacks no longer carry the number, and
     * the call log has no entry until the call ends -- so during a ringing call
     * the notification the dialer posts is the only reliable source of the
     * caller's identity, and it needs no extra permission.
     */
    fun callerHint(): Pair<String, String>? = runCatching {
        activeNotifications.orEmpty()
            .firstOrNull { it.notification?.category == Notification.CATEGORY_CALL }
            ?.let { sbn ->
                val extras = sbn.notification.extras
                val title = extras.getCharSequence(Notification.EXTRA_TITLE)?.toString().orEmpty()
                val text = extras.getCharSequence(Notification.EXTRA_TEXT)?.toString().orEmpty()
                title to text
            }
    }.getOrNull()

    /** Everything currently in the shade, for a desktop that just connected. */
    fun snapshot(): List<JSONObject> = runCatching {
        activeNotifications.orEmpty().filterNot(::shouldSkip).map(::describe)
    }.getOrDefault(emptyList())

    fun dismiss(key: String) {
        runCatching { cancelNotification(key) }
            .onFailure { Log.d(TAG, "could not dismiss $key: ${it.message}") }
    }

    /**
     * Sends an inline reply.
     *
     * Returns false when the notification has no reply action, which the
     * desktop turns into a visible message rather than a silent no-op.
     */
    fun reply(key: String, text: String): Boolean {
        val sbn = activeNotifications.orEmpty().firstOrNull { it.key == key } ?: return false
        val action = replyAction(sbn.notification) ?: return false
        val remoteInputs = action.remoteInputs ?: return false

        val bundle = Bundle()
        for (input in remoteInputs) {
            bundle.putCharSequence(input.resultKey, text)
        }

        val intent = Intent()
        RemoteInput.addResultsToIntent(remoteInputs, intent, bundle)

        return runCatching {
            action.actionIntent.send(this, 0, intent)
            true
        }.onFailure { Log.w(TAG, "reply to $key failed: ${it.message}") }.getOrDefault(false)
    }

    /**
     * Whether a notification is a text message arriving.
     *
     * The desktop uses this to refresh the Messages tab the moment one lands,
     * instead of polling the SMS provider on a timer. The test is made here
     * because only the phone can make it correctly: the default SMS app is a
     * per-device setting, so any list of package names kept on the desktop
     * would be a guess that breaks the first time someone switches from
     * Samsung Messages to Google Messages.
     *
     * Both halves matter. The package check keeps chat apps out -- they post
     * CATEGORY_MESSAGE too, and none of them appear in the SMS database. The
     * category check keeps the messaging app's own housekeeping notifications
     * ("Sending failed", backup reminders) from triggering a reload.
     */
    private fun isTextMessage(sbn: StatusBarNotification): Boolean {
        val fromSmsApp = sbn.packageName == Telephony.Sms.getDefaultSmsPackage(this)
        return fromSmsApp && sbn.notification.category == Notification.CATEGORY_MESSAGE
    }

    private fun replyAction(notification: Notification): Notification.Action? =
        notification.actions?.firstOrNull { action ->
            action.remoteInputs?.any { it.allowFreeFormInput } == true
        }

    private fun shouldSkip(sbn: StatusBarNotification): Boolean {
        val notification = sbn.notification ?: return true
        // Ongoing group summaries and our own service notification are noise.
        if (sbn.packageName == packageName) return true
        if (notification.flags and Notification.FLAG_GROUP_SUMMARY != 0) return true
        return false
    }

    private fun describe(sbn: StatusBarNotification): JSONObject {
        val extras = sbn.notification.extras
        val title = extras.getCharSequence(Notification.EXTRA_TITLE)?.toString().orEmpty()
        // Big text carries the full body when the collapsed form is truncated,
        // which matters for passcodes that sit at the end of a long message.
        val text = (
            extras.getCharSequence(Notification.EXTRA_BIG_TEXT)
                ?: extras.getCharSequence(Notification.EXTRA_TEXT)
            )?.toString().orEmpty()

        return JSONObject()
            .put("t", "notification")
            .put("id", sbn.key)
            .put("package", sbn.packageName)
            .put("app", AppNames.label(this, sbn.packageName))
            .put("title", title)
            .put("text", text)
            .put("sub", extras.getCharSequence(Notification.EXTRA_SUB_TEXT)?.toString().orEmpty())
            .put("time", sbn.postTime)
            .put("ongoing", sbn.isOngoing)
            .put("clearable", sbn.isClearable)
            .put("repliable", replyAction(sbn.notification) != null)
            .put("sms", isTextMessage(sbn))
            .put("icon", sbn.packageName)
    }

    companion object {
        private const val TAG = "TesseraNotif"

        /** Set while the platform has the listener bound; null otherwise. */
        @Volatile
        var instance: NotificationBridge? = null
            private set

        val isConnected: Boolean
            get() = instance != null
    }
}
