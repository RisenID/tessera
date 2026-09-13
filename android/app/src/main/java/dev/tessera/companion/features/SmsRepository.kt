package dev.tessera.companion.features

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.provider.ContactsContract
import android.provider.Telephony
import android.telephony.SmsManager
import android.util.Log
import org.json.JSONArray
import org.json.JSONObject

/** SMS conversations, read straight from the Telephony provider. */
object SmsRepository {

    private const val TAG = "TesseraSms"

    private val PROJECTION = arrayOf(
        Telephony.Sms._ID,
        Telephony.Sms.THREAD_ID,
        Telephony.Sms.ADDRESS,
        Telephony.Sms.BODY,
        Telephony.Sms.DATE,
        Telephony.Sms.TYPE,
        Telephony.Sms.READ,
    )

    fun canRead(context: Context): Boolean =
        context.checkSelfPermission(Manifest.permission.READ_SMS) == PackageManager.PERMISSION_GRANTED

    fun canSend(context: Context): Boolean =
        context.checkSelfPermission(Manifest.permission.SEND_SMS) == PackageManager.PERMISSION_GRANTED

    /** Recent messages, newest first, grouped into threads by the caller. */
    fun messages(context: Context, limit: Int = 500, threadId: String? = null): JSONArray {
        val result = JSONArray()
        if (!canRead(context)) return result

        val selection = threadId?.let { "${Telephony.Sms.THREAD_ID} = ?" }
        val arguments = threadId?.let { arrayOf(it) }
        // Same trap as the media query: "LIMIT n" in the sort order throws
        // "Invalid token LIMIT" on Android 11+. Truncate while reading instead.
        val order = "${Telephony.Sms.DATE} DESC"

        runCatching {
            context.contentResolver.query(
                Telephony.Sms.CONTENT_URI, PROJECTION, selection, arguments, order
            )
        }.onFailure {
            Log.w(TAG, "sms query failed", it)
        }.getOrNull()?.use { cursor ->
            val id = cursor.getColumnIndex(Telephony.Sms._ID)
            val thread = cursor.getColumnIndex(Telephony.Sms.THREAD_ID)
            val address = cursor.getColumnIndex(Telephony.Sms.ADDRESS)
            val body = cursor.getColumnIndex(Telephony.Sms.BODY)
            val date = cursor.getColumnIndex(Telephony.Sms.DATE)
            val type = cursor.getColumnIndex(Telephony.Sms.TYPE)
            val read = cursor.getColumnIndex(Telephony.Sms.READ)

            while (cursor.moveToNext() && result.length() < limit) {
                val number = cursor.getStringOrEmpty(address)
                result.put(
                    JSONObject()
                        .put("id", cursor.getStringOrEmpty(id))
                        .put("thread", cursor.getStringOrEmpty(thread))
                        .put("address", number)
                        .put("name", Contacts.nameFor(context, number))
                        .put("body", cursor.getStringOrEmpty(body))
                        .put("time", if (date >= 0) cursor.getLong(date) else 0L)
                        .put("outgoing", type >= 0 && cursor.getInt(type) == Telephony.Sms.MESSAGE_TYPE_SENT)
                        .put("read", read < 0 || cursor.getInt(read) != 0)
                )
            }
        }
        return result
    }

    /** One entry per conversation, carrying only the latest message. */
    fun threads(context: Context, limit: Int = 200): JSONArray {
        val all = messages(context, limit = 1000)
        val seen = LinkedHashMap<String, JSONObject>()
        for (index in 0 until all.length()) {
            val message = all.getJSONObject(index)
            val key = message.optString("thread").ifEmpty { message.optString("address") }
            // `all` is newest first, so the first sighting of a thread is its
            // most recent message.
            if (!seen.containsKey(key)) seen[key] = message
            if (seen.size >= limit) break
        }
        return JSONArray(seen.values.toList())
    }

    fun send(context: Context, address: String, text: String): String? {
        if (!canSend(context)) return "SMS permission has not been granted on the phone."
        if (address.isBlank()) return "No recipient."
        return runCatching {
            val manager = context.getSystemService(SmsManager::class.java)
                ?: return "SMS is not available on this device."
            val parts = manager.divideMessage(text)
            if (parts.size > 1) {
                manager.sendMultipartTextMessage(address, null, parts, null, null)
            } else {
                manager.sendTextMessage(address, null, text, null, null)
            }
            null
        }.getOrElse { error ->
            Log.w("TesseraSms", "send failed", error)
            error.message ?: "Sending failed."
        }
    }

    private fun android.database.Cursor.getStringOrEmpty(index: Int): String =
        if (index >= 0) getString(index).orEmpty() else ""
}

/** Number-to-name lookup, cached per process. */
object Contacts {

    private val cache = HashMap<String, String>()

    fun nameFor(context: Context, number: String): String {
        if (number.isBlank()) return ""
        cache[number]?.let { return it }
        if (context.checkSelfPermission(Manifest.permission.READ_CONTACTS)
            != PackageManager.PERMISSION_GRANTED
        ) {
            return ""
        }

        val uri = android.net.Uri.withAppendedPath(
            ContactsContract.PhoneLookup.CONTENT_FILTER_URI,
            android.net.Uri.encode(number)
        )
        val name = runCatching {
            context.contentResolver.query(
                uri, arrayOf(ContactsContract.PhoneLookup.DISPLAY_NAME), null, null, null
            )?.use { cursor ->
                if (cursor.moveToFirst()) cursor.getString(0).orEmpty() else ""
            }.orEmpty()
        }.getOrDefault("")

        cache[number] = name
        return name
    }
}
