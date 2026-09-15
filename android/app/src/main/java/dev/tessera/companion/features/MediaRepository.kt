package dev.tessera.companion.features

import android.Manifest
import android.content.ContentUris
import android.content.Context
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.os.Build
import android.provider.MediaStore
import android.util.Log
import android.util.Size
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream

/** The phone's photo and video library. */
object MediaRepository {

    private const val TAG = "TesseraMedia"

    private val PROJECTION = arrayOf(
        MediaStore.MediaColumns._ID,
        MediaStore.MediaColumns.DISPLAY_NAME,
        MediaStore.MediaColumns.DATE_TAKEN,
        MediaStore.MediaColumns.DATE_ADDED,
        MediaStore.MediaColumns.SIZE,
        MediaStore.MediaColumns.MIME_TYPE,
        MediaStore.MediaColumns.BUCKET_DISPLAY_NAME,
    )

    fun canRead(context: Context): Boolean {
        val permissions = when {
            // Android 14+ lets the user grant access to a chosen subset, which denies
            // READ_MEDIA_IMAGES and grants this instead.
            Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE -> listOf(
                Manifest.permission.READ_MEDIA_IMAGES,
                Manifest.permission.READ_MEDIA_VIDEO,
                Manifest.permission.READ_MEDIA_VISUAL_USER_SELECTED,
            )
            Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU -> listOf(
                Manifest.permission.READ_MEDIA_IMAGES,
                Manifest.permission.READ_MEDIA_VIDEO,
            )
            else -> listOf(Manifest.permission.READ_EXTERNAL_STORAGE)
        }
        return permissions.any {
            context.checkSelfPermission(it) == PackageManager.PERMISSION_GRANTED
        }
    }

    /** Newest first, images and videos merged. */
    fun list(context: Context, limit: Int = 300, offset: Int = 0): JSONArray {
        val items = mutableListOf<JSONObject>()
        if (!canRead(context)) return JSONArray()

        for (isVideo in listOf(false, true)) {
            val collection = if (isVideo) {
                MediaStore.Video.Media.getContentUri(MediaStore.VOLUME_EXTERNAL)
            } else {
                MediaStore.Images.Media.getContentUri(MediaStore.VOLUME_EXTERNAL)
            }
            // No "LIMIT n" in the sort order: Android 11+ rejects SQL keywords there with
            // "Invalid token LIMIT".
            val wanted = limit + offset
            runCatching {
                context.contentResolver.query(
                    collection,
                    PROJECTION,
                    null,
                    null,
                    "${MediaStore.MediaColumns.DATE_ADDED} DESC",
                )
            }.onFailure {
                // Swallowing this is how an empty gallery looked like "no photos".
                Log.w(TAG, "media query failed for $collection", it)
            }.getOrNull()?.use { cursor ->
                val id = cursor.getColumnIndexOrThrow(MediaStore.MediaColumns._ID)
                val name = cursor.getColumnIndex(MediaStore.MediaColumns.DISPLAY_NAME)
                val taken = cursor.getColumnIndex(MediaStore.MediaColumns.DATE_TAKEN)
                val added = cursor.getColumnIndex(MediaStore.MediaColumns.DATE_ADDED)
                val size = cursor.getColumnIndex(MediaStore.MediaColumns.SIZE)
                val mime = cursor.getColumnIndex(MediaStore.MediaColumns.MIME_TYPE)
                val bucket = cursor.getColumnIndex(MediaStore.MediaColumns.BUCKET_DISPLAY_NAME)

                while (cursor.moveToNext() && items.size < wanted) {
                    // DATE_TAKEN is milliseconds and often absent; DATE_ADDED is
                    // seconds and always present.
                    val whenMillis = cursor.takeIf { taken >= 0 && !it.isNull(taken) }
                        ?.getLong(taken)
                        ?: (if (added >= 0) cursor.getLong(added) * 1000 else 0L)

                    items += JSONObject()
                        .put("id", "${if (isVideo) "v" else "i"}${cursor.getLong(id)}")
                        .put("name", if (name >= 0) cursor.getString(name).orEmpty() else "")
                        .put("time", whenMillis)
                        .put("size", if (size >= 0) cursor.getLong(size) else 0L)
                        .put("mime", if (mime >= 0) cursor.getString(mime).orEmpty() else "")
                        .put("album", if (bucket >= 0) cursor.getString(bucket).orEmpty() else "")
                        .put("video", isVideo)
                }
            }
        }

        items.sortByDescending { it.optLong("time") }
        return JSONArray(items.drop(offset).take(limit))
    }

    /** JPEG thumbnail bytes, or null when the item cannot be rendered. */
    fun thumbnail(context: Context, mediaId: String, edge: Int = 384): ByteArray? {
        val uri = uriFor(mediaId) ?: return null
        return runCatching {
            val bitmap: Bitmap = context.contentResolver.loadThumbnail(uri, Size(edge, edge), null)
            ByteArrayOutputStream().use { stream ->
                bitmap.compress(Bitmap.CompressFormat.JPEG, 82, stream)
                stream.toByteArray()
            }
        }.onFailure { Log.w(TAG, "thumbnail for $mediaId failed", it) }.getOrNull()
    }

    /** The original file's bytes, or null when unreadable or over [limit]. */
    fun original(context: Context, mediaId: String, limit: Int): ByteArray? {
        val uri = uriFor(mediaId) ?: return null
        return runCatching {
            context.contentResolver.openInputStream(uri)?.use { input ->
                val out = ByteArrayOutputStream()
                val buffer = ByteArray(64 * 1024)
                while (true) {
                    val read = input.read(buffer)
                    if (read < 0) break
                    if (out.size() + read > limit) return null
                    out.write(buffer, 0, read)
                }
                out.toByteArray()
            }
        }.onFailure { Log.w(TAG, "read $mediaId failed", it) }.getOrNull()
    }

    /** Whether the item is larger than [limit] bytes. */
    fun tooLarge(context: Context, mediaId: String, limit: Int): Boolean {
        val uri = uriFor(mediaId) ?: return false
        val length = runCatching {
            context.contentResolver.openAssetFileDescriptor(uri, "r")?.use { it.length }
        }.getOrNull() ?: return false
        return length > limit
    }

    /**
     * Ids are prefixed with the collection they came from ("i" or "v") because
     * image id 42 and video id 42 are different files.
     */
    private fun uriFor(mediaId: String): android.net.Uri? {
        if (mediaId.length < 2) {
            Log.w(TAG, "media id too short: '$mediaId'")
            return null
        }
        val numeric = mediaId.drop(1).toLongOrNull() ?: run {
            Log.w(TAG, "media id not numeric: '$mediaId'")
            return null
        }
        val collection = when (mediaId.first()) {
            'i' -> MediaStore.Images.Media.getContentUri(MediaStore.VOLUME_EXTERNAL)
            'v' -> MediaStore.Video.Media.getContentUri(MediaStore.VOLUME_EXTERNAL)
            else -> {
                Log.w(TAG, "unknown media id prefix: '$mediaId'")
                return null
            }
        }
        return ContentUris.withAppendedId(collection, numeric)
    }
}
