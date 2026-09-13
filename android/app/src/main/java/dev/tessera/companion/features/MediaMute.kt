package dev.tessera.companion.features

import android.content.Context
import android.media.AudioManager
import android.util.Log

/** Silences the phone's own speaker while its audio is playing on the desktop. */
object MediaMute {

    private const val TAG = "TesseraMute"

    /** Whether *we* muted it, so an unmute never touches a mute of theirs. */
    @Volatile
    private var muted = false

    val active: Boolean
        get() = muted

    /** Returns whether the phone is now silent because of us. */
    @Synchronized
    fun mute(context: Context): Boolean {
        if (muted) return true
        val manager = context.getSystemService(AudioManager::class.java) ?: return false
        return runCatching {
            manager.adjustStreamVolume(AudioManager.STREAM_MUSIC, AudioManager.ADJUST_MUTE, 0)
            muted = true
            Log.i(TAG, "phone muted while its audio plays on the desktop")
            true
        }.getOrElse { error ->
            // Do Not Disturb makes volume changes a privileged operation, and
            // this app deliberately does not hold notification policy access.
            Log.w(TAG, "could not mute the phone", error)
            false
        }
    }

    @Synchronized
    fun unmute(context: Context) {
        if (!muted) return
        muted = false
        val manager = context.getSystemService(AudioManager::class.java) ?: return
        runCatching {
            manager.adjustStreamVolume(AudioManager.STREAM_MUSIC, AudioManager.ADJUST_UNMUTE, 0)
            Log.i(TAG, "phone unmuted")
        }.onFailure { Log.w(TAG, "could not unmute the phone", it) }
    }
}
