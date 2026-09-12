package dev.tessera.companion.features

import android.app.NotificationManager
import android.content.Context
import android.service.notification.NotificationListenerService

/**
 * Reads and writes the phone's interruption filter (Do Not Disturb).
 *
 * Nothing here polls. The filter is read once on connect and thereafter the
 * platform calls [NotificationBridge.onInterruptionFilterChanged] whenever it
 * changes, which is the whole reason for using a companion app instead of
 * querying `settings get global zen_mode` over adb on a timer.
 */
object DndController {

    /** Protocol names, deliberately matching the desktop's vocabulary. */
    const val OFF = "off"
    const val PRIORITY = "priority"
    const val ALARMS = "alarms"
    const val NONE = "none"

    fun toName(filter: Int): String = when (filter) {
        NotificationManager.INTERRUPTION_FILTER_PRIORITY -> PRIORITY
        NotificationManager.INTERRUPTION_FILTER_ALARMS -> ALARMS
        NotificationManager.INTERRUPTION_FILTER_NONE -> NONE
        else -> OFF
    }

    fun toFilter(name: String): Int = when (name) {
        PRIORITY -> NotificationManager.INTERRUPTION_FILTER_PRIORITY
        ALARMS -> NotificationManager.INTERRUPTION_FILTER_ALARMS
        NONE -> NotificationManager.INTERRUPTION_FILTER_NONE
        else -> NotificationManager.INTERRUPTION_FILTER_ALL
    }

    fun current(context: Context): String {
        val manager = context.getSystemService(NotificationManager::class.java)
            ?: return OFF
        // Reading the filter needs notification policy access, which the user
        // grants on the setup screen.
        if (!manager.isNotificationPolicyAccessGranted) return OFF
        return toName(manager.currentInterruptionFilter)
    }

    fun canControl(context: Context): Boolean {
        val manager = context.getSystemService(NotificationManager::class.java) ?: return false
        return manager.isNotificationPolicyAccessGranted
    }

    /**
     * Applies a new filter.
     *
     * Prefers the notification listener, which is allowed to change the filter
     * without separate policy access; falls back to NotificationManager when
     * the listener is not connected.
     */
    fun apply(context: Context, name: String): Boolean {
        val filter = toFilter(name)

        NotificationBridge.instance?.let { listener ->
            return runCatching {
                listener.requestInterruptionFilter(filter)
                true
            }.getOrDefault(false)
        }

        val manager = context.getSystemService(NotificationManager::class.java) ?: return false
        if (!manager.isNotificationPolicyAccessGranted) return false
        return runCatching {
            manager.setInterruptionFilter(filter)
            true
        }.getOrDefault(false)
    }
}
