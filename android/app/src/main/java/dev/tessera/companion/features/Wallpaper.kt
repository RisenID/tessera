package dev.tessera.companion.features

import android.app.WallpaperManager
import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.drawable.BitmapDrawable
import android.graphics.drawable.Drawable
import android.util.Log
import java.io.ByteArrayOutputStream

/**
 * The phone's wallpaper, for the desktop to show beside its name.
 *
 * Cosmetic, and deliberately so: a sidebar that shows *your* phone rather than
 * a generic outline is the difference between a tool and your phone on your
 * desk. It is also the one piece of the phone's own look the desktop can
 * honestly borrow.
 *
 * Two ways to get it, because Android has been tightening this for years:
 *
 *  * `WallpaperManager.getDrawable`, which works where the platform still
 *    allows an ordinary app to see it;
 *  * failing that, the wallpaper's *colours*, which need no permission at all
 *    and are enough for the desktop to tint its own tile.
 *
 * A live wallpaper has no still image; `getDrawable` returns its preview or
 * nothing, and the colours are the fallback there too.
 */
object Wallpaper {

    private const val TAG = "TesseraWallpaper"

    /** Big enough for a small tile on a high-density screen, no bigger. */
    private const val MAX_EDGE = 720
    private const val QUALITY = 82

    /** The wallpaper as JPEG, or null if this phone will not give it up. */
    fun jpeg(context: Context): ByteArray? {
        val bitmap = read(context) ?: return null
        val scaled = fit(bitmap, MAX_EDGE)
        return runCatching {
            ByteArrayOutputStream().use { out ->
                scaled.compress(Bitmap.CompressFormat.JPEG, QUALITY, out)
                out.toByteArray()
            }
        }.getOrNull()
    }

    /**
     * A colour for the wallpaper, where the picture itself cannot be had.
     *
     * Sent for completeness rather than drawn with: the desktop shows a
     * default wallpaper when the phone has none to give, because a flat colour
     * does not look like a phone. The wallpaper's own colours first, then the
     * accent Android 12 and later derive *from* the wallpaper and expose to
     * every app without permission.
     */
    fun colour(context: Context): String? {
        val manager = runCatching { WallpaperManager.getInstance(context) }.getOrNull()
        for (flag in intArrayOf(WallpaperManager.FLAG_SYSTEM, WallpaperManager.FLAG_LOCK)) {
            val colours = runCatching { manager?.getWallpaperColors(flag) }.getOrNull()
            val primary = colours?.primaryColor ?: continue
            // A Color's components are floats from zero to one, not bytes.
            return hex(
                (primary.red() * 255).toInt(),
                (primary.green() * 255).toInt(),
                (primary.blue() * 255).toInt(),
            )
        }
        return accent(context)
    }

    /** The phone's own themed accent, which Android derives from the wallpaper. */
    private fun accent(context: Context): String? = runCatching {
        val value = context.getColor(android.R.color.system_accent1_400)
        hex((value shr 16) and 0xFF, (value shr 8) and 0xFF, value and 0xFF)
    }.getOrNull()

    private fun hex(red: Int, green: Int, blue: Int): String =
        String.format("#%02x%02x%02x", red, green, blue)

    private fun read(context: Context): Bitmap? {
        val manager = runCatching { WallpaperManager.getInstance(context) }.getOrNull()
            ?: return null

        // The home screen first. Wrapped rather than checked: this throws
        // SecurityException on the versions that have closed it off, and
        // returns nothing for a live wallpaper. Both are "no picture", not a
        // failure worth reporting.
        val drawable: Drawable? = runCatching { manager.drawable }
            .onFailure { Log.i(TAG, "no home wallpaper: ${it.message}") }
            .getOrNull()
        drawable?.let { toBitmap(it) }?.let { return it }

        // Then the lock screen, which is very often a still photograph even on
        // a phone whose home screen is a live wallpaper -- this phone being
        // exactly that case.
        return runCatching {
            manager.getWallpaperFile(WallpaperManager.FLAG_LOCK)?.use { descriptor ->
                android.graphics.BitmapFactory.decodeFileDescriptor(descriptor.fileDescriptor)
            }
        }.onFailure { Log.i(TAG, "no lock wallpaper: ${it.message}") }.getOrNull()
    }

    private fun toBitmap(drawable: Drawable): Bitmap? {
        (drawable as? BitmapDrawable)?.bitmap?.let { return it }
        val width = drawable.intrinsicWidth.takeIf { it > 0 } ?: return null
        val height = drawable.intrinsicHeight.takeIf { it > 0 } ?: return null
        return runCatching {
            val bitmap = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888)
            val canvas = Canvas(bitmap)
            drawable.setBounds(0, 0, canvas.width, canvas.height)
            drawable.draw(canvas)
            bitmap
        }.getOrNull()
    }

    /** Scale so the longest edge is *edge*, keeping the shape. */
    private fun fit(bitmap: Bitmap, edge: Int): Bitmap {
        val longest = maxOf(bitmap.width, bitmap.height)
        if (longest <= edge) return bitmap
        val scale = edge.toFloat() / longest
        return runCatching {
            Bitmap.createScaledBitmap(
                bitmap,
                (bitmap.width * scale).toInt().coerceAtLeast(1),
                (bitmap.height * scale).toInt().coerceAtLeast(1),
                true,
            )
        }.getOrDefault(bitmap)
    }
}
