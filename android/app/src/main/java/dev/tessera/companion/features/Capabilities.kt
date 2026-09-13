package dev.tessera.companion.features

import android.content.Context
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraManager
import android.hardware.camera2.params.StreamConfigurationMap
import android.media.MediaCodec
import android.net.wifi.WifiManager
import android.os.Build
import android.util.Log
import org.json.JSONArray
import org.json.JSONObject

/** What this particular phone can actually do. */
object Capabilities {

    private const val TAG = "TesseraCaps"

    // -- cameras -------------------------------------------------------------

    /**
     * Every camera, with the sizes the encoder can consume and the frame rates
     * each size supports.
     */
    fun cameras(context: Context): JSONArray {
        val manager = context.getSystemService(CameraManager::class.java)
            ?: return JSONArray()
        val cameras = JSONArray()

        val ids = runCatching { manager.cameraIdList }.getOrElse {
            Log.w(TAG, "could not list cameras", it)
            return cameras
        }

        for (id in ids) {
            runCatching {
                val characteristics = manager.getCameraCharacteristics(id)
                val map = characteristics.get(
                    CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP
                ) ?: return@runCatching

                val facing = when (characteristics.get(CameraCharacteristics.LENS_FACING)) {
                    CameraCharacteristics.LENS_FACING_FRONT -> "front"
                    CameraCharacteristics.LENS_FACING_EXTERNAL -> "external"
                    else -> "back"
                }

                cameras.put(
                    JSONObject()
                        .put("id", id)
                        .put("facing", facing)
                        .put("sizes", sizesFor(map))
                        .put("fps", frameRatesFor(characteristics))
                )
            }.onFailure { Log.d(TAG, "skipping camera $id: ${it.message}") }
        }
        return cameras
    }

    private fun sizesFor(map: StreamConfigurationMap): JSONArray {
        val sizes = runCatching { map.getOutputSizes(MediaCodec::class.java) }
            .getOrNull() ?: return JSONArray()

        val result = JSONArray()
        // Largest first: the desktop shows them in that order, and the phone's
        // preferred resolution is normally the biggest one it lists.
        for (size in sizes.sortedByDescending { it.width.toLong() * it.height }) {
            val minDuration = runCatching {
                map.getOutputMinFrameDuration(MediaCodec::class.java, size)
            }.getOrDefault(0L)
            val maxFps = if (minDuration > 0L) {
                (1_000_000_000.0 / minDuration).toInt()
            } else {
                30
            }
            result.put(
                JSONObject()
                    .put("w", size.width)
                    .put("h", size.height)
                    .put("maxFps", maxFps)
            )
        }
        return result
    }

    /** Frame rates the auto-exposure system will actually target. */
    private fun frameRatesFor(characteristics: CameraCharacteristics): JSONArray {
        val ranges = characteristics.get(
            CameraCharacteristics.CONTROL_AE_AVAILABLE_TARGET_FPS_RANGES
        ) ?: return JSONArray(listOf(30))

        val rates = ranges
            .map { it.upper }
            .filter { it in 1..960 }
            .distinct()
            .sorted()
        return JSONArray(if (rates.isEmpty()) listOf(30) else rates)
    }

    // -- hotspot -------------------------------------------------------------

    /** Bands this phone can run a soft AP on. */
    fun hotspotBands(context: Context): JSONArray {
        val wifi = context.applicationContext.getSystemService(WifiManager::class.java)
        val bands = JSONArray()
        if (wifi == null) {
            bands.put("2.4")
            return bands
        }

        val has24 = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            runCatching { wifi.is24GHzBandSupported }.getOrDefault(true)
        } else {
            true
        }
        if (has24) bands.put("2.4")
        if (runCatching { wifi.is5GHzBandSupported }.getOrDefault(false)) bands.put("5")
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R &&
            runCatching { wifi.is6GHzBandSupported }.getOrDefault(false)
        ) {
            bands.put("6")
        }

        if (bands.length() == 0) bands.put("2.4")
        return bands
    }
}
