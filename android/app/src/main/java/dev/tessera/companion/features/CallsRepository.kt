package dev.tessera.companion.features

import android.Manifest
import android.annotation.SuppressLint
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.media.AudioManager
import android.net.Uri
import android.os.Build
import android.provider.CallLog
import android.telecom.TelecomManager
import android.util.Log
import org.json.JSONArray
import org.json.JSONObject

/** Recent calls, and control of the one happening now. */
object CallsRepository {

    private const val TAG = "TesseraCalls"

    private val PROJECTION = arrayOf(
        CallLog.Calls.NUMBER,
        CallLog.Calls.CACHED_NAME,
        CallLog.Calls.TYPE,
        CallLog.Calls.DATE,
        CallLog.Calls.DURATION,
    )

    fun canReadLog(context: Context): Boolean =
        context.checkSelfPermission(Manifest.permission.READ_CALL_LOG) ==
            PackageManager.PERMISSION_GRANTED

    fun canControl(context: Context): Boolean =
        context.checkSelfPermission(Manifest.permission.ANSWER_PHONE_CALLS) ==
            PackageManager.PERMISSION_GRANTED

    fun canDialDirectly(context: Context): Boolean =
        context.checkSelfPermission(Manifest.permission.CALL_PHONE) ==
            PackageManager.PERMISSION_GRANTED

    /** Recent calls, newest first. */
    fun recent(context: Context, limit: Int = 100): JSONArray {
        val result = JSONArray()
        if (!canReadLog(context)) return result

        runCatching {
            context.contentResolver.query(
                CallLog.Calls.CONTENT_URI,
                PROJECTION,
                null,
                null,
                // No "LIMIT n" here: Android 11+ rejects SQL keywords in the
                // sort order. The cursor is lazy, so stopping early is enough.
                "${CallLog.Calls.DATE} DESC",
            )
        }.onFailure { Log.w(TAG, "call log query failed", it) }.getOrNull()?.use { cursor ->
            val number = cursor.getColumnIndex(CallLog.Calls.NUMBER)
            val name = cursor.getColumnIndex(CallLog.Calls.CACHED_NAME)
            val type = cursor.getColumnIndex(CallLog.Calls.TYPE)
            val date = cursor.getColumnIndex(CallLog.Calls.DATE)
            val duration = cursor.getColumnIndex(CallLog.Calls.DURATION)

            while (cursor.moveToNext() && result.length() < limit) {
                val raw = if (number >= 0) cursor.getString(number).orEmpty() else ""
                result.put(
                    JSONObject()
                        .put("number", raw)
                        .put(
                            "name",
                            (if (name >= 0) cursor.getString(name) else null)
                                ?: Contacts.nameFor(context, raw),
                        )
                        .put("kind", describe(if (type >= 0) cursor.getInt(type) else 0))
                        .put("time", if (date >= 0) cursor.getLong(date) else 0L)
                        .put("duration", if (duration >= 0) cursor.getLong(duration) else 0L)
                )
            }
        }
        return result
    }

    private fun describe(type: Int): String = when (type) {
        CallLog.Calls.INCOMING_TYPE -> "incoming"
        CallLog.Calls.OUTGOING_TYPE -> "outgoing"
        CallLog.Calls.MISSED_TYPE -> "missed"
        CallLog.Calls.REJECTED_TYPE -> "rejected"
        CallLog.Calls.BLOCKED_TYPE -> "blocked"
        CallLog.Calls.VOICEMAIL_TYPE -> "voicemail"
        else -> "unknown"
    }

    /** Answers the ringing call. Returns null on success, else a message. */
    // canControl() rejects the call before it gets here, but lint's flow
    // analysis does not follow the guard across the early return.
    @SuppressLint("MissingPermission")
    fun answer(context: Context): String? {
        if (!canControl(context)) {
            return "Grant Tessera permission to answer calls on the phone."
        }
        val telecom = context.getSystemService(TelecomManager::class.java)
            ?: return "Telephony is not available on this device."
        return runCatching {
            telecom.acceptRingingCall()
            null
        }.getOrElse {
            Log.w(TAG, "answer failed", it)
            it.message ?: "The call could not be answered."
        }
    }

    /**
     * Ends the current call, or rejects a ringing one -- Telecom uses the same
     * call for both, and which it means depends on the call's state.
     */
    @SuppressLint("MissingPermission")
    fun hangUp(context: Context): String? {
        if (!canControl(context)) {
            return "Grant Tessera permission to answer calls on the phone."
        }
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.P) {
            return "Ending calls from the computer needs Android 9 or newer."
        }
        val telecom = context.getSystemService(TelecomManager::class.java)
            ?: return "Telephony is not available on this device."
        return runCatching {
            if (!telecom.endCall()) "There was no call to end." else null
        }.getOrElse {
            Log.w(TAG, "end call failed", it)
            it.message ?: "The call could not be ended."
        }
    }

    /**
     * The microphone, during a call. Not an InCallService: this is the
     * platform-wide mute, which is what the dialer's own button flips too.
     */
    fun setMuted(context: Context, on: Boolean): String? {
        val audio = context.getSystemService(AudioManager::class.java)
            ?: return "Audio is not available on this device."
        return runCatching {
            audio.isMicrophoneMute = on
            if (audio.isMicrophoneMute == on) null else "The phone kept its microphone as it was."
        }.getOrElse { it.message ?: "The microphone could not be changed." }
    }

    /** The loudspeaker, during a call. */
    @Suppress("DEPRECATION")
    fun setSpeaker(context: Context, on: Boolean): String? {
        val audio = context.getSystemService(AudioManager::class.java)
            ?: return "Audio is not available on this device."
        return runCatching {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                val devices = audio.availableCommunicationDevices
                val wanted = devices.firstOrNull {
                    it.type == if (on) android.media.AudioDeviceInfo.TYPE_BUILTIN_SPEAKER
                    else android.media.AudioDeviceInfo.TYPE_BUILTIN_EARPIECE
                }
                if (wanted == null) {
                    if (on) return "This phone has no loudspeaker route to switch to."
                    audio.clearCommunicationDevice()
                } else if (!audio.setCommunicationDevice(wanted)) {
                    return "The phone refused to move the call's audio."
                }
            } else {
                audio.isSpeakerphoneOn = on
            }
            null
        }.getOrElse { it.message ?: "The speaker could not be changed." }
    }

    /** What the call's audio is doing now, for the desktop's buttons. */
    fun audioState(context: Context): JSONObject {
        val audio = context.getSystemService(AudioManager::class.java)
        @Suppress("DEPRECATION")
        val speaker = when {
            audio == null -> false
            Build.VERSION.SDK_INT >= Build.VERSION_CODES.S ->
                audio.communicationDevice?.type == android.media.AudioDeviceInfo.TYPE_BUILTIN_SPEAKER
            else -> audio.isSpeakerphoneOn
        }
        return JSONObject()
            .put("muted", audio?.isMicrophoneMute ?: false)
            .put("speaker", speaker)
    }

    /**
     * Places a call. With CALL_PHONE it dials immediately; otherwise the
     * dialer opens with the number ready, which needs one tap on the phone.
     */
    fun dial(context: Context, number: String): String? {
        if (number.isBlank()) return "No number given."
        val uri = Uri.fromParts("tel", number, null)
        val action = if (canDialDirectly(context)) Intent.ACTION_CALL else Intent.ACTION_DIAL

        return runCatching {
            context.startActivity(
                Intent(action, uri).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            )
            if (action == Intent.ACTION_DIAL) {
                "The dialer is open on your phone with the number ready; press call there."
            } else {
                null
            }
        }.getOrElse {
            Log.w(TAG, "dial failed", it)
            it.message ?: "The call could not be placed."
        }
    }
}
