package dev.risenid.tessera.features

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import dev.risenid.tessera.MainActivity
import dev.risenid.tessera.R
import java.util.concurrent.atomic.AtomicInteger

/** Notifications the computer asks this phone to show: "the build finished". */
object DesktopMessages {

    private const val CHANNEL = "tessera-from-computer"
    private val next = AtomicInteger(1000)

    /** Posts one. False when the phone has not allowed notifications. */
    fun post(context: Context, title: String, text: String, from: String): Boolean {
        val manager = context.getSystemService(NotificationManager::class.java) ?: return false
        if (!manager.areNotificationsEnabled()) return false
        if (manager.getNotificationChannel(CHANNEL) == null) {
            manager.createNotificationChannel(
                NotificationChannel(
                    CHANNEL,
                    context.getString(R.string.channel_computer),
                    NotificationManager.IMPORTANCE_DEFAULT,
                ).apply { description = context.getString(R.string.channel_computer_description) }
            )
        }
        val open = PendingIntent.getActivity(
            context, 0, Intent(context, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val notification = Notification.Builder(context, CHANNEL)
            .setContentTitle(title.ifBlank { from.ifBlank { context.getString(R.string.computer_fallback) } })
            .setContentText(text)
            .setStyle(Notification.BigTextStyle().bigText(text))
            .setSubText(if (title.isBlank()) "" else from)
            .setSmallIcon(R.drawable.ic_stat_tessera)
            .setContentIntent(open)
            .setAutoCancel(true)
            .build()
        return runCatching { manager.notify(next.getAndIncrement(), notification); true }
            .getOrDefault(false)
    }
}
