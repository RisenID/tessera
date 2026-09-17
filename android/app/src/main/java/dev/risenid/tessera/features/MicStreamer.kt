package dev.risenid.tessera.features

import android.Manifest
import android.annotation.SuppressLint
import android.content.Context
import android.content.pm.PackageManager
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.util.Log
import org.json.JSONObject
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.concurrent.thread

/** This phone's microphone, sent to the desktop as a microphone of its own. */
class MicStreamer(
    private val context: Context,
    private val onStarted: (JSONObject) -> Unit,
    private val onFrame: (ByteArray) -> Unit,
    private val onError: (String) -> Unit,
) {
    private var record: AudioRecord? = null
    private val running = AtomicBoolean(false)

    fun start() {
        if (!canRecord(context)) {
            onError(NO_PERMISSION)
            return
        }
        if (!running.compareAndSet(false, true)) return
        runCatching { open() }.onFailure { error ->
            Log.w(TAG, "microphone failed to start", error)
            onError(error.message ?: "The phone's microphone could not be opened.")
            stop()
        }
    }

    @SuppressLint("MissingPermission")
    private fun open() {
        val minimum = AudioRecord.getMinBufferSize(SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, ENCODING)
        // VOICE_COMMUNICATION brings the phone's own echo cancelling and noise
        // suppression, which is what a call from the desktop wants.
        val audio = AudioRecord(
            MediaRecorder.AudioSource.VOICE_COMMUNICATION,
            SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            ENCODING,
            maxOf(minimum, FRAME_BYTES * 8),
        )
        if (audio.state != AudioRecord.STATE_INITIALIZED) {
            audio.release()
            throw IllegalStateException("the microphone would not initialise")
        }
        audio.startRecording()
        if (audio.recordingState != AudioRecord.RECORDSTATE_RECORDING) {
            audio.release()
            throw IllegalStateException("the microphone started but is not recording")
        }
        record = audio
        onStarted(
            JSONObject()
                .put("t", "mic_started")
                .put("codec", "pcm_s16le")
                .put("rate", SAMPLE_RATE)
                .put("channels", 1)
                .put("frameBytes", FRAME_BYTES)
        )
        thread(name = "tessera-mic") { pump(audio) }
    }

    private fun pump(audio: AudioRecord) {
        val ring = Array(RING_FRAMES) { ByteArray(FRAME_BYTES) }
        var slot = 0
        while (running.get()) {
            val frame = ring[slot]
            slot = (slot + 1) % RING_FRAMES
            var filled = 0
            while (filled < FRAME_BYTES && running.get()) {
                val read = audio.read(frame, filled, FRAME_BYTES - filled)
                if (read <= 0) {
                    if (read < 0 && running.get()) {
                        onError("The phone's microphone stopped unexpectedly.")
                        running.set(false)
                    }
                    return
                }
                filled += read
            }
            if (filled == FRAME_BYTES && running.get()) onFrame(frame)
        }
    }

    fun stop() {
        if (!running.compareAndSet(true, false)) return
        val audio = record
        record = null
        runCatching { audio?.stop() }
        runCatching { audio?.release() }
        Log.i(TAG, "microphone stopped")
    }

    companion object {
        private const val TAG = "TesseraMic"
        const val SAMPLE_RATE = 48_000
        const val ENCODING = AudioFormat.ENCODING_PCM_16BIT
        const val FRAME_MS = 20
        /** 20 ms of 48 kHz mono 16-bit. */
        const val FRAME_BYTES = SAMPLE_RATE / 1000 * FRAME_MS * 2
        const val RING_FRAMES = 16

        const val NO_PERMISSION =
            "Tessera does not have the microphone permission on the phone; grant it in the app's checklist."

        fun canRecord(context: Context): Boolean =
            context.checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED
    }
}
