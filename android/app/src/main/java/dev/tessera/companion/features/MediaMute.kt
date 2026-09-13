package dev.tessera.companion.features

import android.content.Context
import android.media.AudioManager
import android.util.Log

/**
 * Silences the phone's own speaker while its audio is playing on the desktop.
 *
 * Playback capture hands over a *copy* of the media mix, so by default the
 * music comes out of both the phone and the computer at once -- two copies of
 * the same track, a fraction of a second apart. That is right for a phone on
 * headphones and wrong for a phone on the desk, so it is the desktop's choice
 * and it travels with the request to start.
 *
 * The copy is taken before the volume stage: measured on an S25, capture with
 * the media volume at zero was as loud as capture at 8/15 (peaks 0.594 and
 * 0.751 on the same track, frames above silence 249/249). Muting the phone
 * therefore costs the desktop nothing, which is what makes this workable at
 * all.
 *
 * [AudioManager.ADJUST_MUTE] rather than setting the volume to zero, for two
 * reasons: Android remembers the level itself, so there is nothing of the
 * user's to save and restore wrongly, and a mute is released automatically if
 * the process holding it dies -- a crash here cannot leave the phone silent.
 */
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
