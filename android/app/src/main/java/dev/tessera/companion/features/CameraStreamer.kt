package dev.tessera.companion.features

import android.Manifest
import android.annotation.SuppressLint
import android.content.Context
import android.content.pm.PackageManager
import android.hardware.camera2.CameraCaptureSession
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraDevice
import android.hardware.camera2.CameraManager
import android.hardware.camera2.CaptureRequest
import android.media.MediaCodec
import android.media.MediaCodecInfo
import android.media.MediaFormat
import android.os.Handler
import android.os.HandlerThread
import android.util.Base64
import android.util.Log
import android.view.Surface
import org.json.JSONObject
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Streams a phone camera to the desktop as H.264.
 *
 * Camera2 feeds MediaCodec's input surface directly, so frames are encoded in
 * hardware and never travel through the app's own memory as bitmaps. The
 * desktop pipes the resulting Annex-B stream into ffmpeg, which writes to a
 * v4l2loopback device -- which is what makes the phone appear as an ordinary
 * webcam to every Linux application.
 */
class CameraStreamer(
    private val context: Context,
    private val facing: String,
    /** Exact camera to open. The phone has several per facing (main, ultrawide),
     *  so an id picks the intended lens; blank falls back to [facing]. */
    private val cameraId: String = "",
    private val width: Int,
    private val height: Int,
    private val fps: Int,
    private val onConfigured: (JSONObject) -> Unit,
    private val onFrame: (ByteArray, Boolean, Long) -> Unit,
    private val onError: (String) -> Unit,
) {

    private var encoder: MediaCodec? = null
    private var inputSurface: Surface? = null
    private var device: CameraDevice? = null
    private var session: CameraCaptureSession? = null

    private val thread = HandlerThread("tessera-camera").apply { start() }
    private val handler = Handler(thread.looper)
    private val running = AtomicBoolean(false)

    fun start() {
        if (context.checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) {
            onError("Camera permission has not been granted on the phone.")
            return
        }
        if (!running.compareAndSet(false, true)) return

        runCatching {
            startEncoder()
            openCamera()
        }.onFailure { error ->
            Log.w(TAG, "camera start failed", error)
            onError(error.message ?: "The camera could not be started.")
            stop()
        }
    }

    private fun startEncoder() {
        val format = MediaFormat.createVideoFormat(MediaFormat.MIMETYPE_VIDEO_AVC, width, height).apply {
            setInteger(
                MediaFormat.KEY_COLOR_FORMAT,
                MediaCodecInfo.CodecCapabilities.COLOR_FormatSurface,
            )
            // A webcam stream is watched live, so favour latency over size.
            setInteger(MediaFormat.KEY_BIT_RATE, width * height * 4)
            setInteger(MediaFormat.KEY_FRAME_RATE, fps)
            // One keyframe a second keeps a late-joining consumer from waiting.
            setInteger(MediaFormat.KEY_I_FRAME_INTERVAL, 1)
            setInteger(MediaFormat.KEY_LATENCY, 0)
        }

        val codec = MediaCodec.createEncoderByType(MediaFormat.MIMETYPE_VIDEO_AVC)
        codec.configure(format, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE)
        inputSurface = codec.createInputSurface()
        codec.setCallback(encoderCallback, handler)
        codec.start()
        encoder = codec
    }

    private val encoderCallback = object : MediaCodec.Callback() {
        override fun onInputBufferAvailable(codec: MediaCodec, index: Int) {
            // Input arrives through the surface; nothing to feed by hand.
        }

        override fun onOutputBufferAvailable(
            codec: MediaCodec,
            index: Int,
            info: MediaCodec.BufferInfo,
        ) {
            if (!running.get()) {
                runCatching { codec.releaseOutputBuffer(index, false) }
                return
            }
            val buffer = runCatching { codec.getOutputBuffer(index) }.getOrNull()
            if (buffer != null && info.size > 0) {
                buffer.position(info.offset)
                buffer.limit(info.offset + info.size)
                val bytes = ByteArray(info.size).also(buffer::get)

                if (info.flags and MediaCodec.BUFFER_FLAG_CODEC_CONFIG != 0) {
                    // SPS/PPS: the decoder needs these before any frame.
                    onConfigured(
                        JSONObject()
                            .put("t", "camera_started")
                            .put("codec", "h264")
                            .put("width", width)
                            .put("height", height)
                            .put("fps", fps)
                            .put("sps_pps", Base64.encodeToString(bytes, Base64.NO_WRAP))
                    )
                } else {
                    val isKey = info.flags and MediaCodec.BUFFER_FLAG_KEY_FRAME != 0
                    onFrame(bytes, isKey, info.presentationTimeUs)
                }
            }
            runCatching { codec.releaseOutputBuffer(index, false) }
        }

        override fun onError(codec: MediaCodec, error: MediaCodec.CodecException) {
            Log.w(TAG, "encoder error", error)
            this@CameraStreamer.onError("The video encoder failed: ${error.message}")
            stop()
        }

        override fun onOutputFormatChanged(codec: MediaCodec, format: MediaFormat) {
            Log.d(TAG, "encoder format: $format")
        }
    }

    // start() refuses to run without CAMERA, but lint's flow analysis does not
    // follow the check across methods.
    @SuppressLint("MissingPermission")
    private fun openCamera() {
        val manager = context.getSystemService(CameraManager::class.java)
            ?: throw IllegalStateException("no camera service")
        val cameraId = selectCamera(manager)
            ?: throw IllegalStateException("no $facing camera on this phone")

        manager.openCamera(cameraId, object : CameraDevice.StateCallback() {
            override fun onOpened(camera: CameraDevice) {
                device = camera
                runCatching { createSession(camera) }.onFailure {
                    onError(it.message ?: "Could not start the camera session.")
                    stop()
                }
            }

            override fun onDisconnected(camera: CameraDevice) {
                Log.i(TAG, "camera disconnected")
                stop()
            }

            override fun onError(camera: CameraDevice, error: Int) {
                this@CameraStreamer.onError(describeCameraError(error))
                stop()
            }
        }, handler)
    }

    private fun selectCamera(manager: CameraManager): String? {
        if (cameraId.isNotBlank() && manager.cameraIdList.contains(cameraId)) {
            return cameraId
        }
        val wanted = when (facing) {
            "front" -> CameraCharacteristics.LENS_FACING_FRONT
            "external" -> CameraCharacteristics.LENS_FACING_EXTERNAL
            else -> CameraCharacteristics.LENS_FACING_BACK
        }
        val ids = manager.cameraIdList
        return ids.firstOrNull { id ->
            manager.getCameraCharacteristics(id).get(CameraCharacteristics.LENS_FACING) == wanted
        } ?: ids.firstOrNull()
    }

    @Suppress("DEPRECATION")
    private fun createSession(camera: CameraDevice) {
        val surface = inputSurface ?: throw IllegalStateException("encoder surface missing")

        val request = camera.createCaptureRequest(CameraDevice.TEMPLATE_RECORD).apply {
            addTarget(surface)
            set(
                CaptureRequest.CONTROL_AE_TARGET_FPS_RANGE,
                android.util.Range(fps, fps),
            )
            set(CaptureRequest.CONTROL_AF_MODE, CaptureRequest.CONTROL_AF_MODE_CONTINUOUS_VIDEO)
        }.build()

        camera.createCaptureSession(
            listOf(surface),
            object : CameraCaptureSession.StateCallback() {
                override fun onConfigured(configured: CameraCaptureSession) {
                    session = configured
                    runCatching { configured.setRepeatingRequest(request, null, handler) }
                        .onFailure {
                            onError(it.message ?: "The camera would not start capturing.")
                            stop()
                        }
                }

                override fun onConfigureFailed(configured: CameraCaptureSession) {
                    onError("The camera rejected the requested video format.")
                    stop()
                }
            },
            handler,
        )
    }

    fun stop() {
        if (!running.compareAndSet(true, false)) return
        // Order matters: stop producing frames, then tear the encoder down.
        runCatching { session?.stopRepeating() }
        runCatching { session?.close() }
        runCatching { device?.close() }
        runCatching { encoder?.stop() }
        runCatching { encoder?.release() }
        runCatching { inputSurface?.release() }
        session = null
        device = null
        encoder = null
        inputSurface = null
        thread.quitSafely()
        Log.i(TAG, "camera stopped")
    }

    private fun describeCameraError(error: Int): String = when (error) {
        CameraDevice.StateCallback.ERROR_CAMERA_DISABLED ->
            "The camera is blocked on this phone. Check the camera privacy " +
                "toggle in Quick Settings, and whether a work profile or device " +
                "admin (for example Knox) disallows camera use."
        CameraDevice.StateCallback.ERROR_CAMERA_IN_USE,
        CameraDevice.StateCallback.ERROR_MAX_CAMERAS_IN_USE ->
            "Another app is using the camera. Close it and try again."
        CameraDevice.StateCallback.ERROR_CAMERA_DEVICE ->
            "The camera hardware reported a fault; it usually clears after a restart."
        else -> "The camera could not be opened (error $error)."
    }

    companion object {
        private const val TAG = "TesseraCamera"
    }
}
