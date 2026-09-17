package dev.tessera.companion.features

import android.Manifest
import android.app.AlarmManager
import android.content.ActivityNotFoundException
import android.content.ContentUris
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.provider.AlarmClock
import android.provider.CalendarContract
import android.util.Log
import org.json.JSONArray
import org.json.JSONObject

/** Upcoming events and the next alarm, and timers and alarms set from the computer. */
object Agenda {

    private const val TAG = "TesseraAgenda"

    fun canReadCalendar(context: Context): Boolean =
        context.checkSelfPermission(Manifest.permission.READ_CALENDAR) == PackageManager.PERMISSION_GRANTED

    /** Event instances from an hour ago to [days] days ahead, soonest first. */
    fun events(context: Context, days: Int, limit: Int = 50): JSONArray {
        if (!canReadCalendar(context)) return JSONArray()
        val now = System.currentTimeMillis()
        val begin = now - 60 * 60 * 1000L
        val end = now + days.coerceIn(1, 31) * 24 * 60 * 60 * 1000L
        val uri = ContentUris.appendId(
            ContentUris.appendId(CalendarContract.Instances.CONTENT_URI.buildUpon(), begin), end,
        ).build()
        val projection = arrayOf(
            CalendarContract.Instances.EVENT_ID,
            CalendarContract.Instances.TITLE,
            CalendarContract.Instances.BEGIN,
            CalendarContract.Instances.END,
            CalendarContract.Instances.ALL_DAY,
            CalendarContract.Instances.EVENT_LOCATION,
            CalendarContract.Instances.CALENDAR_DISPLAY_NAME,
        )
        val items = mutableListOf<JSONObject>()
        runCatching {
            context.contentResolver.query(uri, projection, null, null, "${CalendarContract.Instances.BEGIN} ASC")
        }.onFailure { Log.w(TAG, "calendar query failed", it) }.getOrNull()?.use { cursor ->
            while (cursor.moveToNext() && items.size < limit) {
                items += JSONObject()
                    .put("id", cursor.getLong(0))
                    .put("title", cursor.getString(1).orEmpty())
                    .put("begin", cursor.getLong(2))
                    .put("end", cursor.getLong(3))
                    .put("allDay", cursor.getInt(4) != 0)
                    .put("location", cursor.getString(5).orEmpty())
                    .put("calendar", cursor.getString(6).orEmpty())
            }
        }
        return JSONArray(items)
    }

    /** When the next alarm rings, in epoch milliseconds; 0 when none is set. */
    fun nextAlarm(context: Context): JSONObject {
        val manager = context.getSystemService(AlarmManager::class.java)
        val next = runCatching { manager?.nextAlarmClock?.triggerTime }.getOrNull() ?: 0L
        return JSONObject().put("time", next)
    }

    /** Starts a timer in the phone's clock app. Null when it worked. */
    fun setTimer(context: Context, seconds: Int, label: String): String? {
        if (seconds <= 0) return "A timer needs a length."
        val intent = Intent(AlarmClock.ACTION_SET_TIMER)
            .putExtra(AlarmClock.EXTRA_LENGTH, seconds)
            .putExtra(AlarmClock.EXTRA_SKIP_UI, true)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        if (label.isNotBlank()) intent.putExtra(AlarmClock.EXTRA_MESSAGE, label)
        return launch(context, intent, "timers")
    }

    /** Sets an alarm in the phone's clock app. Null when it worked. */
    fun setAlarm(context: Context, hour: Int, minute: Int, label: String): String? {
        if (hour !in 0..23 || minute !in 0..59) return "That is not a time of day."
        val intent = Intent(AlarmClock.ACTION_SET_ALARM)
            .putExtra(AlarmClock.EXTRA_HOUR, hour)
            .putExtra(AlarmClock.EXTRA_MINUTES, minute)
            .putExtra(AlarmClock.EXTRA_SKIP_UI, true)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        if (label.isNotBlank()) intent.putExtra(AlarmClock.EXTRA_MESSAGE, label)
        return launch(context, intent, "alarms")
    }

    private fun launch(context: Context, intent: Intent, what: String): String? = try {
        context.startActivity(intent)
        null
    } catch (_: ActivityNotFoundException) {
        "No clock app on the phone takes $what."
    } catch (e: Exception) {
        "The phone could not set it: ${e.message}"
    }
}
