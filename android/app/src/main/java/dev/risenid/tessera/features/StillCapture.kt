package dev.risenid.tessera.features

import android.Manifest
import android.annotation.SuppressLint
import android.content.Context
import android.content.pm.PackageManager
import android.graphics.ImageFormat
import android.hardware.camera2.CameraCaptureSession
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraDevice
import android.hardware.camera2.CameraManager
import android.hardware.camera2.CaptureRequest
import android.hardware.camera2.CaptureResult
import android.hardware.camera2.TotalCaptureResult
import android.media.ImageReader
import android.os.Handler
import android.os.HandlerThread
import android.util.Log
import java.util.concurrent.atomic.AtomicBoolean

/** One photo, taken for the computer: focus and exposure settle first, then a JPEG. */
class StillCapture(
    private val context: Context,
    private val facing: String,
    private val cameraId: String,
    private val onDone: (ByteArray?, String?) -> Unit,
) {
    private val thread = HandlerThread("tessera-still")
    private val handler by lazy { Handler(thread.looper) }
    private val finished = AtomicBoolean(false)

    private var device: CameraDevice? = null
    private var session: CameraCaptureSession? = null
    private var preview: ImageReader? = null
    private var jpeg: ImageReader? = null
    private var orientation = 0
    private var settledFrames = 0
    private var shot = false

    fun start() {
        if (context.checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) {
            finish(null, "Camera permission has not been granted on the phone.")
            return
        }
        thread.start()
        handler.postDelayed({ finish(null, "The camera took too long.") }, TIMEOUT_MS)
        runCatching { open() }.onFailure { finish(null, it.message ?: "The camera could not be opened.") }
    }

    @SuppressLint("MissingPermission")
    private fun open() {
        val manager = context.getSystemService(CameraManager::class.java)
            ?: throw IllegalStateException("no camera service")
        val id = select(manager) ?: throw IllegalStateException("no $facing camera on this phone")
        val traits = manager.getCameraCharacteristics(id)
        orientation = traits.get(CameraCharacteristics.SENSOR_ORIENTATION) ?: 0
        val sizes = traits.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP)
            ?.getOutputSizes(ImageFormat.JPEG).orEmpty()
        // The largest that still fits one frame on the link, with room to spare.
        val size = sizes.filter { it.width.toLong() * it.height <= MAX_PIXELS }
            .maxByOrNull { it.width.toLong() * it.height }
            ?: sizes.minByOrNull { it.width.toLong() * it.height }
            ?: throw IllegalStateException("the camera offers no JPEG size")

        jpeg = ImageReader.newInstance(size.width, size.height, ImageFormat.JPEG, 2).apply {
            setOnImageAvailableListener({ reader ->
                val image = reader.acquireLatestImage() ?: return@setOnImageAvailableListener
                val buffer = image.planes[0].buffer
                val bytes = ByteArray(buffer.remaining()).also(buffer::get)
                image.close()
                finish(bytes, null)
            }, handler)
        }
        // A small stream keeps the 3A loops fed while they settle; its frames are dropped.
        preview = ImageReader.newInstance(640, 480, ImageFormat.YUV_420_888, 3).apply {
            setOnImageAvailableListener({ reader -> reader.acquireLatestImage()?.close() }, handler)
        }

        manager.openCamera(id, object : CameraDevice.StateCallback() {
            override fun onOpened(camera: CameraDevice) {
                device = camera
                runCatching { settle(camera) }.onFailure { finish(null, it.message ?: "The camera would not start.") }
            }

            override fun onDisconnected(camera: CameraDevice) = finish(null, "The camera was taken by another app.")

            override fun onError(camera: CameraDevice, error: Int) =
                finish(null, "The camera could not be opened (error $error).")
        }, handler)
    }

    private fun select(manager: CameraManager): String? {
        if (cameraId.isNotBlank() && manager.cameraIdList.contains(cameraId)) return cameraId
        val wanted = when (facing) {
            "front" -> CameraCharacteristics.LENS_FACING_FRONT
            "external" -> CameraCharacteristics.LENS_FACING_EXTERNAL
            else -> CameraCharacteristics.LENS_FACING_BACK
        }
        val ids = manager.cameraIdList
        return ids.firstOrNull {
            manager.getCameraCharacteristics(it).get(CameraCharacteristics.LENS_FACING) == wanted
        } ?: ids.firstOrNull()
    }

    @Suppress("DEPRECATION")
    private fun settle(camera: CameraDevice) {
        val previewSurface = preview!!.surface
        val stillSurface = jpeg!!.surface
        val request = camera.createCaptureRequest(CameraDevice.TEMPLATE_PREVIEW).apply {
            addTarget(previewSurface)
            set(CaptureRequest.CONTROL_AF_MODE, CaptureRequest.CONTROL_AF_MODE_CONTINUOUS_PICTURE)
            set(CaptureRequest.CONTROL_AE_MODE, CaptureRequest.CONTROL_AE_MODE_ON)
        }.build()

        camera.createCaptureSession(
            listOf(previewSurface, stillSurface),
            object : CameraCaptureSession.StateCallback() {
                override fun onConfigured(configured: CameraCaptureSession) {
                    session = configured
                    runCatching { configured.setRepeatingRequest(request, watcher, handler) }
                        .onFailure { finish(null, it.message ?: "The camera would not start capturing.") }
                }

                override fun onConfigureFailed(configured: CameraCaptureSession) =
                    finish(null, "The camera rejected the photo format.")
            },
            handler,
        )
    }

    /** Shoots once focus and exposure have settled, or after enough frames regardless. */
    private val watcher = object : CameraCaptureSession.CaptureCallback() {
        override fun onCaptureCompleted(
            session: CameraCaptureSession,
            request: CaptureRequest,
            result: TotalCaptureResult,
        ) {
            if (shot) return
            settledFrames++
            val af = result.get(CaptureResult.CONTROL_AF_STATE)
            val ae = result.get(CaptureResult.CONTROL_AE_STATE)
            val focused = af == null || af in FOCUSED
            val exposed = ae == null || ae == CaptureResult.CONTROL_AE_STATE_CONVERGED ||
                ae == CaptureResult.CONTROL_AE_STATE_FLASH_REQUIRED
            if ((focused && exposed && settledFrames >= MIN_FRAMES) || settledFrames >= MAX_FRAMES) shoot()
        }
    }

    private fun shoot() {
        val camera = device ?: return
        val live = session ?: return
        shot = true
        runCatching {
            live.stopRepeating()
            val still = camera.createCaptureRequest(CameraDevice.TEMPLATE_STILL_CAPTURE).apply {
                addTarget(jpeg!!.surface)
                set(CaptureRequest.CONTROL_AF_MODE, CaptureRequest.CONTROL_AF_MODE_CONTINUOUS_PICTURE)
                set(CaptureRequest.JPEG_ORIENTATION, orientation)
                set(CaptureRequest.JPEG_QUALITY, 92.toByte())
            }.build()
            live.capture(still, null, handler)
        }.onFailure { finish(null, it.message ?: "The photo could not be taken.") }
    }

    private fun finish(bytes: ByteArray?, problem: String?) {
        if (!finished.compareAndSet(false, true)) return
        handler.removeCallbacksAndMessages(null)
        runCatching { session?.close() }
        runCatching { device?.close() }
        runCatching { preview?.close() }
        runCatching { jpeg?.close() }
        session = null
        device = null
        thread.quitSafely()
        if (problem != null) Log.w(TAG, "photo failed: $problem")
        onDone(bytes, problem)
    }

    companion object {
        private const val TAG = "TesseraStill"
        private const val TIMEOUT_MS = 10_000L
        private const val MIN_FRAMES = 8
        private const val MAX_FRAMES = 45
        /** 12 megapixels: a few megabytes of JPEG, well inside one frame on the link. */
        private const val MAX_PIXELS = 12_500_000L
        private val FOCUSED = setOf(
            CaptureResult.CONTROL_AF_STATE_FOCUSED_LOCKED,
            CaptureResult.CONTROL_AF_STATE_PASSIVE_FOCUSED,
            CaptureResult.CONTROL_AF_STATE_NOT_FOCUSED_LOCKED,
            CaptureResult.CONTROL_AF_STATE_PASSIVE_UNFOCUSED,
            CaptureResult.CONTROL_AF_STATE_INACTIVE,
        )
    }
}
