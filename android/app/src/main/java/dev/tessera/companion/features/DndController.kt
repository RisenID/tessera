package dev.tessera.companion.features

import android.app.NotificationManager
import android.content.Context
import android.service.notification.NotificationListenerService

/** Reads and writes the phone's interruption filter (Do Not Disturb). */
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

    /** Sets [name] and checks it took. Null when it did, else why not. */
    fun set(context: Context, name: String): String? {
        // Goes into a shell command, so only the four known words.
        if (name !in setOf(OFF, PRIORITY, ALARMS, NONE)) return "Unknown Do Not Disturb mode."
        val filter = toFilter(name)
        // Since Android 15 an app only switches its own mode; the shell switches the phone's.
        if (PrivilegedShell.hasPermission()) {
            PrivilegedShell.run("cmd notification set_dnd $name", 10_000)
            if (settled(context, filter)) return null
        }
        if (!apply(context, name)) {
            return "Do Not Disturb could not be changed. Grant notification access on the phone."
        }
        if (settled(context, filter)) return null
        return if (PrivilegedShell.hasPermission()) {
            "Something else on the phone is holding Do Not Disturb. Change it on the phone."
        } else {
            "Do Not Disturb was set by something other than Tessera. " +
                "Start Shizuku on the phone to let Tessera change it."
        }
    }

    private fun settled(context: Context, filter: Int): Boolean {
        val manager = context.getSystemService(NotificationManager::class.java) ?: return false
        repeat(10) {
            if (manager.currentInterruptionFilter == filter) return true
            Thread.sleep(100)
        }
        return false
    }

    /** Applies a new filter. */
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
