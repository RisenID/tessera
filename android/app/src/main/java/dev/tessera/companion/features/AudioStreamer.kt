package dev.tessera.companion.features

import android.Manifest
import android.annotation.SuppressLint
import android.content.Context
import android.content.pm.PackageManager
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioPlaybackCaptureConfiguration
import android.media.AudioRecord
import android.media.projection.MediaProjection
import android.os.Build
import android.util.Log
import org.json.JSONObject
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.concurrent.thread

/** Streams what this phone is playing to the desktop. */
class AudioStreamer(
    private val context: Context,
    private val projection: MediaProjection,
    /** Whether to silence the phone's own speaker while this runs. */
    private var mutePhone: Boolean = false,
    private val onStarted: (JSONObject) -> Unit,
    private val onFrame: (ByteArray) -> Unit,
    private val onError: (String) -> Unit,
) {

    private var record: AudioRecord? = null
    private val running = AtomicBoolean(false)
    private var reader: Thread? = null

    /** Silence seen back to back, in frames, for the notification's sake. */
    private var quietFrames = 0

    fun start() {
        if (!supported()) {
            onError(UNSUPPORTED)
            return
        }
        if (context.checkSelfPermission(Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED
        ) {
            onError(NO_PERMISSION)
            return
        }
        if (!running.compareAndSet(false, true)) return

        runCatching { open() }.onFailure { error ->
            Log.w(TAG, "audio capture failed to start", error)
            onError(describe(error))
            stop()
        }
    }

    // start() checks RECORD_AUDIO first; lint does not follow it across methods.
    @SuppressLint("MissingPermission")
    private fun open() {
        // What to capture: media and games, plus UNKNOWN, which is where a surprising number of
        // players end up when they set no attributes.
        val config = AudioPlaybackCaptureConfiguration.Builder(projection)
            .addMatchingUsage(AudioAttributes.USAGE_MEDIA)
            .addMatchingUsage(AudioAttributes.USAGE_GAME)
            .addMatchingUsage(AudioAttributes.USAGE_UNKNOWN)
            .build()

        val format = AudioFormat.Builder()
            .setEncoding(ENCODING)
            .setSampleRate(SAMPLE_RATE)
            .setChannelMask(AudioFormat.CHANNEL_IN_STEREO)
            .build()

        // Room for several frames, so a scheduling hiccup on either side drops
        // nothing: the capture keeps filling while the reader is away.
        val minimum = AudioRecord.getMinBufferSize(SAMPLE_RATE, AudioFormat.CHANNEL_IN_STEREO, ENCODING)
        val buffer = maxOf(minimum, FRAME_BYTES * 8)

        val audio = AudioRecord.Builder()
            .setAudioFormat(format)
            .setAudioPlaybackCaptureConfig(config)
            .setBufferSizeInBytes(buffer)
            .build()

        if (audio.state != AudioRecord.STATE_INITIALIZED) {
            audio.release()
            throw IllegalStateException("the audio capture would not initialise")
        }

        audio.startRecording()
        if (audio.recordingState != AudioRecord.RECORDSTATE_RECORDING) {
            audio.release()
            throw IllegalStateException("the audio capture started but is not recording")
        }
        record = audio

        // Only once the capture is certain to run: muting the phone and then
        // failing to send it anywhere would be the worst of both.
        val silenced = mutePhone && MediaMute.mute(context)

        onStarted(
            JSONObject()
                .put("t", "audio_started")
                .put("codec", "pcm_s16le")
                .put("rate", SAMPLE_RATE)
                .put("channels", 2)
                .put("frameBytes", FRAME_BYTES)
                // Said rather than assumed: Do Not Disturb can refuse the mute, and the desktop
                // should not claim the phone went quiet if it did not.
                .put("muted", silenced)
                .put("muteAsked", mutePhone)
        )

        reader = thread(name = "tessera-audio") { pump(audio) }
    }

    /** Reads the capture and hands each frame on, for as long as we are running. */
    private fun pump(audio: AudioRecord) {
        // A ring of frames rather than a copy per frame: the session drops
        // media past a queue of six, so a buffer is free again long before
        // the ring comes back round to it.
        val ring = Array(RING_FRAMES) { ByteArray(FRAME_BYTES) }
        var slot = 0
        while (running.get()) {
            val frame = ring[slot]
            slot = (slot + 1) % RING_FRAMES
            var filled = 0
            while (filled < FRAME_BYTES && running.get()) {
                val read = audio.read(frame, filled, FRAME_BYTES - filled)
                if (read <= 0) {
                    // ERROR_INVALID_OPERATION arrives when we are being torn
                    // down, which is not a failure worth reporting.
                    if (read < 0 && running.get()) {
                        Log.w(TAG, "audio read returned $read")
                        onError("The phone's audio capture stopped unexpectedly.")
                        running.set(false)
                    }
                    return
                }
                filled += read
            }
            if (filled == FRAME_BYTES && running.get()) {
                if (isQuiet(frame)) quietFrames++ else quietFrames = 0
                onFrame(frame)
            }
        }
    }

    /** Whether nothing is playing: capture returns zeros, not nothing. */
    private fun isQuiet(frame: ByteArray): Boolean = frame.all { it == 0.toByte() }

    /** Seconds of continuous silence, for the desktop's "nothing playing" hint. */
    val quietSeconds: Int
        get() = quietFrames * FRAME_MS / 1000

    /** Mute or unmute the phone without interrupting the stream. */
    fun setMuted(on: Boolean): Boolean {
        mutePhone = on
        if (!running.get()) return false
        return if (on) MediaMute.mute(context) else {
            MediaMute.unmute(context)
            false
        }
    }

    fun stop() {
        if (!running.compareAndSet(true, false)) return
        // Before anything else: the phone must not be left silent because a
        // later step threw.
        MediaMute.unmute(context)
        val audio = record
        record = null
        runCatching { audio?.stop() }
        runCatching { audio?.release() }
        // The projection is single-use by Android's rules, so it is spent now.
        runCatching { projection.stop() }
        reader = null
        Log.i(TAG, "audio capture stopped")
    }

    val active: Boolean
        get() = running.get()

    private fun describe(error: Throwable): String = when {
        error is SecurityException ->
            "Android refused the audio capture. Grant Phone Link the microphone " +
                "permission on the phone -- it is required for playback capture " +
                "even though the microphone is never used."
        else -> error.message ?: "The phone's audio could not be captured."
    }

    companion object {
        private const val TAG = "TesseraAudio"

        const val SAMPLE_RATE = 48_000
        const val ENCODING = AudioFormat.ENCODING_PCM_16BIT
        const val FRAME_MS = 20

        /** 20 ms of 48 kHz stereo 16-bit: 48 * 20 * 2 channels * 2 bytes. */
        const val FRAME_BYTES = SAMPLE_RATE / 1000 * FRAME_MS * 2 * 2

        /** Frames in flight at once; more than the session will ever queue. */
        const val RING_FRAMES = 16

        const val UNSUPPORTED =
            "This phone is on Android 9 or older, which has no way for an app " +
                "to capture what is playing."

        const val NO_PERMISSION =
            "Phone Link does not have the microphone permission on the phone. " +
                "Android requires it for playback capture even though the " +
                "microphone is never used -- grant it in the app's checklist."

        /** Playback capture arrived in Android 10. */
        fun supported(): Boolean = Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q

        fun canCapture(context: Context): Boolean =
            supported() &&
                context.checkSelfPermission(Manifest.permission.RECORD_AUDIO) ==
                PackageManager.PERMISSION_GRANTED
    }
}
