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
     * The wallpaper's colour as #rrggbb, by whichever route this phone allows.
     *
     * Three, in order of how closely they describe the picture:
     *
     *  1. the home screen wallpaper's own colours;
     *  2. the lock screen's, which is often a still image even when the home
     *     screen is not;
     *  3. the phone's system accent, which Android 12 and later derive *from*
     *     the wallpaper and expose to every app without permission.
     *
     * The third is what answers on this phone and many like it: the wallpaper
     * is a live one -- Samsung's own, here -- so there is no image and no
     * wallpaper colours, but the palette the whole phone is themed with is
     * still the wallpaper's palette.
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
        return seed() ?: accent(context)
    }

    /**
     * The colour the phone itself derived from the wallpaper.
     *
     * One UI keeps it in the wallpaper service's dump, and it is the honest
     * answer for a live wallpaper: the picture cannot be read by anyone -- not
     * even the shell -- but this is the colour the phone themed itself with
     * because of it. On the phone this was written against, the public API
     * answers a flat grey and this answers the deep red the wallpaper is.
     *
     * Reading a dump needs the DUMP permission, so it goes through the same
     * Shizuku shell as the hotspot and the clipboard, and is simply skipped
     * where that is not available.
     */
    private fun seed(): String? {
        if (!PrivilegedShell.hasPermission()) return null
        val output = runCatching { PrivilegedShell.run("dumpsys wallpaper") }.getOrNull()
        if (output == null || output.code != 0) return null
        val match = SEED.find(output.text) ?: return null
        val value = match.groupValues[1].toIntOrNull() ?: return null
        return hex((value shr 16) and 0xFF, (value shr 8) and 0xFF, value and 0xFF)
    }


    /** The phone's own themed accent, which Android derives from the wallpaper. */
    private fun accent(context: Context): String? = runCatching {
        val value = context.getColor(android.R.color.system_accent1_400)
        hex(
            (value shr 16) and 0xFF,
            (value shr 8) and 0xFF,
            value and 0xFF,
        )
    }.getOrNull()

    private val SEED = Regex("SeedColors,\\s*\\[(-?\\d+)")

    private fun hex(red: Int, green: Int, blue: Int): String =
        String.format("#%02x%02x%02x", red, green, blue)

    private fun read(context: Context): Bitmap? {
        val manager = runCatching { WallpaperManager.getInstance(context) }.getOrNull()
            ?: return null
        // Wrapped rather than checked: this throws SecurityException on the
        // versions that have closed it off, and returns the default wallpaper
        // on some others. Both are "no picture", not a failure to report.
        val drawable: Drawable? = runCatching { manager.drawable }
            .onFailure { Log.i(TAG, "the phone will not share its wallpaper: ${it.message}") }
            .getOrNull()
        return drawable?.let { toBitmap(it) }
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
