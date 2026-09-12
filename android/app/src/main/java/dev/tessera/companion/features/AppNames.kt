package dev.tessera.companion.features

import android.content.Context
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.drawable.BitmapDrawable
import java.io.ByteArrayOutputStream

/** Human-readable app labels and icons, cached because notifications repeat. */
object AppNames {

    private val labels = HashMap<String, String>()
    private val icons = HashMap<String, ByteArray>()

    fun label(context: Context, packageName: String): String =
        labels.getOrPut(packageName) {
            runCatching {
                val manager = context.packageManager
                manager.getApplicationLabel(
                    manager.getApplicationInfo(packageName, 0)
                ).toString()
            }.getOrDefault(packageName)
        }

    /** The app's launcher icon as PNG bytes, for the desktop's notification list. */
    fun icon(context: Context, packageName: String, size: Int = 96): ByteArray? =
        icons.getOrPut(packageName) {
            runCatching {
                val drawable = context.packageManager.getApplicationIcon(packageName)
                val bitmap = if (drawable is BitmapDrawable && drawable.bitmap != null) {
                    Bitmap.createScaledBitmap(drawable.bitmap, size, size, true)
                } else {
                    // Adaptive icons have no backing bitmap; render them.
                    Bitmap.createBitmap(size, size, Bitmap.Config.ARGB_8888).also { bitmap ->
                        val canvas = Canvas(bitmap)
                        drawable.setBounds(0, 0, size, size)
                        drawable.draw(canvas)
                    }
                }
                ByteArrayOutputStream().use { stream ->
                    bitmap.compress(Bitmap.CompressFormat.PNG, 100, stream)
                    stream.toByteArray()
                }
            }.getOrDefault(ByteArray(0))
        }.takeIf { it.isNotEmpty() }

    fun clear() {
        labels.clear()
        icons.clear()
    }
}
