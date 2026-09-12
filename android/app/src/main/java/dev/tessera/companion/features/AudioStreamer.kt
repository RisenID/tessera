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

/**
 * Streams what this phone is playing to the desktop.
 *
 * Playback capture, not a Bluetooth profile. Android hands us a *copy* of the
 * media mix, so the phone keeps playing exactly as it was: through its speaker,
 * or through headphones that are none of our business. Nothing here can pull
 * audio away from them, which is the whole reason this route was chosen over
 * A2DP -- and it is the same route Phone Link and scrcpy take.
 *
 * Two consequences worth knowing, both Android's rules rather than ours:
 *
 *  * it needs a MediaProjection, which means the user consents on the phone --
 *    the same dialog screen sharing uses, because this is the same permission;
 *  * an app can opt out of being captured (`allowAudioPlaybackCapture=false`),
 *    and apps playing DRM-protected audio generally do. Those arrive as
 *    silence; everything else is captured.
 *
 * The wire format is signed 16-bit PCM at 48 kHz stereo -- what the capture
 * gives us, forwarded untouched. That is 1.5 Mbit/s, which a local network does
 * not notice, and it means no decoder on the desktop and nothing to go wrong
 * between the phone's mix and the speakers. The header names the format, so a
 * compressed one can be added later without either end guessing.
 */
class AudioStreamer(
    private val context: Context,
    private val projection: MediaProjection,
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
        // What to capture: media and games, plus UNKNOWN, which is where a
        // surprising number of players end up when they set no attributes.
        // Deliberately not USAGE_VOICE_COMMUNICATION (a call is not ours to
        // copy) and not the assistant or notification usages, which would put
        // the phone's own beeps on the desktop's speakers.
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

        onStarted(
            JSONObject()
                .put("t", "audio_started")
                .put("codec", "pcm_s16le")
                .put("rate", SAMPLE_RATE)
                .put("channels", 2)
                .put("frameBytes", FRAME_BYTES)
        )

        reader = thread(name = "tessera-audio") { pump(audio) }
    }

    /**
     * Reads the capture and hands each frame on, for as long as we are running.
     *
     * One frame is 20 ms. Small enough that the desktop can keep its buffer
     * short, large enough that the socket is not doing thousands of tiny
     * writes a second.
     */
    private fun pump(audio: AudioRecord) {
        val frame = ByteArray(FRAME_BYTES)
        while (running.get()) {
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
                onFrame(frame.copyOf())
            }
        }
    }

    /** Whether nothing is playing: capture returns zeros, not nothing. */
    private fun isQuiet(frame: ByteArray): Boolean = frame.all { it == 0.toByte() }

    /** Seconds of continuous silence, for the desktop's "nothing playing" hint. */
    val quietSeconds: Int
        get() = quietFrames * FRAME_MS / 1000

    fun stop() {
        if (!running.compareAndSet(true, false)) return
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
