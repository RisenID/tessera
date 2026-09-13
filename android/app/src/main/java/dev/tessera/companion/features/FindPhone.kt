package dev.tessera.companion.features

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioManager
import android.media.MediaPlayer
import android.media.RingtoneManager
import android.os.Build
import android.os.CombinedVibration
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager
import android.util.Log

/** Ringing the phone from the desktop, to find it. */
object FindPhone {

    private const val TAG = "TesseraRing"

    /** Long enough to find a phone in another room; short enough to forgive. */
    const val LIMIT_MS = 60_000L

    private var player: MediaPlayer? = null
    private var previousVolume: Int? = null
    private var stopAt: Long = 0L
    private val timer = java.util.Timer("tessera-ring", true)
    private var pending: java.util.TimerTask? = null

    val ringing: Boolean
        @Synchronized get() = player != null

    @Synchronized
    fun start(context: Context): String? {
        if (ringing) {
            // Already going: extend rather than starting a second one.
            schedule(context)
            return null
        }
        val audio = context.getSystemService(AudioManager::class.java)
            ?: return "This phone has no audio service."

        val tone = RingtoneManager.getActualDefaultRingtoneUri(
            context, RingtoneManager.TYPE_RINGTONE
        ) ?: RingtoneManager.getDefaultUri(RingtoneManager.TYPE_ALARM)
        ?: return "This phone has no ringtone set."

        return runCatching {
            previousVolume = audio.getStreamVolume(AudioManager.STREAM_ALARM)
            audio.setStreamVolume(
                AudioManager.STREAM_ALARM,
                audio.getStreamMaxVolume(AudioManager.STREAM_ALARM),
                0,
            )

            player = MediaPlayer().apply {
                setAudioAttributes(
                    AudioAttributes.Builder()
                        // An alarm sounds through Do Not Disturb and through a
                        // silenced ringer, which is the point of the feature.
                        .setUsage(AudioAttributes.USAGE_ALARM)
                        .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                        .build()
                )
                setDataSource(context, tone)
                isLooping = true
                prepare()
                start()
            }
            buzz(context)
            schedule(context)
            Log.i(TAG, "ringing")
            null
        }.getOrElse { error ->
            Log.w(TAG, "could not ring", error)
            restoreVolume(context)
            player = null
            error.message ?: "The phone could not be rung."
        }
    }

    @Synchronized
    fun stop(context: Context) {
        pending?.cancel()
        pending = null
        stopBuzz(context)
        val current = player ?: return
        player = null
        runCatching { current.stop() }
        runCatching { current.release() }
        restoreVolume(context)
        Log.i(TAG, "stopped ringing")
    }

    /** Seconds left before it gives up on its own, for the desktop to show. */
    val remainingMs: Long
        @Synchronized get() = if (ringing) maxOf(0, stopAt - System.currentTimeMillis()) else 0

    private fun schedule(context: Context) {
        pending?.cancel()
        stopAt = System.currentTimeMillis() + LIMIT_MS
        val applicationContext = context.applicationContext
        pending = object : java.util.TimerTask() {
            override fun run() {
                stop(applicationContext)
            }
        }.also { timer.schedule(it, LIMIT_MS) }
    }

    private fun restoreVolume(context: Context) {
        val audio = context.getSystemService(AudioManager::class.java) ?: return
        val previous = previousVolume ?: return
        previousVolume = null
        runCatching { audio.setStreamVolume(AudioManager.STREAM_ALARM, previous, 0) }
    }

    /** A buzz as well as a sound: a phone face-down on carpet is quiet. */
    @Suppress("DEPRECATION")
    private fun buzz(context: Context) {
        val pattern = longArrayOf(0, 400, 600)
        runCatching {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                val manager = context.getSystemService(VibratorManager::class.java)
                    ?: return
                manager.vibrate(
                    CombinedVibration.createParallel(
                        VibrationEffect.createWaveform(pattern, 0)
                    )
                )
            } else {
                val vibrator = context.getSystemService(Vibrator::class.java) ?: return
                vibrator.vibrate(VibrationEffect.createWaveform(pattern, 0))
            }
        }.onFailure { Log.d(TAG, "no vibrator: ${it.message}") }
    }

    @Suppress("DEPRECATION")
    private fun stopBuzz(context: Context) {
        runCatching {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                context.getSystemService(VibratorManager::class.java)?.cancel()
            } else {
                context.getSystemService(Vibrator::class.java)?.cancel()
            }
        }
    }
}
