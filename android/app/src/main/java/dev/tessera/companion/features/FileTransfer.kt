package dev.tessera.companion.features

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.MediaStore
import android.util.Log
import android.webkit.MimeTypeMap
import dev.tessera.companion.R
import org.json.JSONObject
import java.io.IOException
import java.io.InputStream
import java.io.OutputStream
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference
import kotlin.concurrent.thread

/**
 * Files between this phone and a desktop, in both directions.
 *
 * Receiving writes straight into MediaStore's Downloads collection, which is
 * the one place an ordinary app can put a file where the user's own file
 * manager will find it, with no storage permission at all. The entry is
 * created as `IS_PENDING`, so nothing else on the phone sees a half-written
 * file, and published when the last chunk lands.
 *
 * Sending reads a content Uri a chunk at a time. Nothing is ever loaded whole:
 * the files people actually share are videos, and a 4K clip does not fit in an
 * app's heap.
 */
object FileTransfer {

    private const val TAG = "TesseraFiles"

    /** Matches the desktop's chunk. Big enough to be fast, small enough to
     *  keep memory flat and cancellation quick. */
    const val CHUNK = 256 * 1024

    private const val CHANNEL_ID = "tessera-files"

    /**
     * How many chunks may wait to be written before the reader has to stop.
     *
     * The queue exists so that reading the socket and writing to storage
     * happen at the same time rather than in turn: without it the link sat
     * idle for every disk write, which measured as a third of the throughput
     * the same phone manages over adb. Bounded, so a fast link cannot turn
     * into unbounded memory -- a full queue stops the reader, TCP's window
     * closes, and the sender slows down, which is exactly the wanted
     * behaviour.
     */
    private const val QUEUE_DEPTH = 12

    /** One file arriving from a desktop. */
    class Incoming(
        private val context: Context,
        val id: String,
        val name: String,
        val size: Long,
        mime: String,
    ) {
        private val resolver = context.contentResolver
        private val values = ContentValues().apply {
            put(MediaStore.MediaColumns.DISPLAY_NAME, safeName(name))
            put(MediaStore.MediaColumns.MIME_TYPE, mime.ifEmpty { guessMime(name) })
            put(MediaStore.MediaColumns.IS_PENDING, 1)
            if (size > 0) put(MediaStore.MediaColumns.SIZE, size)
        }

        var uri: Uri? = null
            private set
        private var stream: OutputStream? = null
        var written: Long = 0
            private set

        private val queue = ArrayBlockingQueue<ByteArray>(QUEUE_DEPTH)
        private val failure = AtomicReference<String?>(null)
        private var writer: Thread? = null

        /** The marker that means "no more chunks", rather than a second queue. */
        private val end = ByteArray(0)

        fun open(): String? {
            val problem = runCatching {
                // Straight into MediaStore, as a pending entry. Staging in the
                // app's cache first was tried and measured: no faster, and it
                // needs the whole file a second time on a partition that is
                // usually the smaller of the two.
                val target = resolver.insert(downloads(), values)
                    ?: return "the phone would not make a file to write"
                uri = target
                stream = resolver.openOutputStream(target)
                    ?: return "the phone would not open the file it just made"
                null
            }.getOrElse { it.message ?: "the file could not be created" }
            if (problem != null) return problem

            writer = thread(name = "tessera-file-write") { drain() }
            return null
        }

        private fun drain() {
            while (true) {
                val chunk = try {
                    queue.take()
                } catch (_: InterruptedException) {
                    return
                }
                if (chunk === end) return
                try {
                    stream?.write(chunk)
                    written += chunk.size
                } catch (error: Exception) {
                    failure.set(error.message ?: "the file could not be written")
                    return
                }
            }
        }

        /** Hand a chunk to the writer, waiting if it is already behind. */
        fun write(bytes: ByteArray) {
            failure.get()?.let { throw IOException(it) }
            // put(), not offer(): a full queue must stop the socket being
            // read, which is how the sender is told to slow down.
            queue.put(bytes)
        }

        /** Publish it: the file becomes visible to the rest of the phone. */
        fun finish(): String {
            runCatching {
                queue.put(end)
                writer?.join(30_000)
            }
            writer = null
            runCatching { stream?.flush() }
            runCatching { stream?.close() }
            stream = null
            failure.get()?.let { problem ->
                Log.w(TAG, "incoming file failed: $problem")
                discard()
                return ""
            }

            val target = uri ?: return ""
            // Published only now: until this update the entry is pending, and
            // nothing else on the phone can see a half-written file.
            runCatching {
                resolver.update(
                    target,
                    ContentValues().apply { put(MediaStore.MediaColumns.IS_PENDING, 0) },
                    null, null,
                )
            }
            return target.toString()
        }

        /** Throw it away, so a cancelled transfer leaves nothing behind. */
        fun discard() {
            writer?.interrupt()
            writer = null
            queue.clear()
            runCatching { stream?.close() }
            stream = null
            uri?.let { target -> runCatching { resolver.delete(target, null, null) } }
            uri = null
        }
    }

    /** One file on its way to a desktop, read as it goes. */
    class Outgoing(
        context: Context,
        val id: String,
        val uri: Uri,
        val name: String,
        val size: Long,
        val mime: String,
    ) {
        private val resolver = context.contentResolver
        private var stream: InputStream? = null
        private val cancelled = AtomicBoolean(false)

        fun open(): Boolean {
            stream = runCatching { resolver.openInputStream(uri) }.getOrNull()
            return stream != null
        }

        /** The next chunk, or null at the end. */
        fun next(): ByteArray? {
            if (cancelled.get()) return null
            val source = stream ?: return null
            val buffer = ByteArray(CHUNK)
            var filled = 0
            // read() is free to return less than asked for; a short chunk is
            // legal but wasteful, so fill the buffer before sending it.
            while (filled < CHUNK) {
                val read = runCatching { source.read(buffer, filled, CHUNK - filled) }
                    .getOrDefault(-1)
                if (read <= 0) break
                filled += read
            }
            if (filled == 0) return null
            return if (filled == CHUNK) buffer else buffer.copyOf(filled)
        }

        fun cancel() {
            cancelled.set(true)
        }

        fun close() {
            runCatching { stream?.close() }
            stream = null
        }
    }

    // -- reading what the share sheet handed us ------------------------------

    /** Name, size and type of a content Uri, as the desktop needs them. */
    fun describe(context: Context, uri: Uri): Triple<String, Long, String> {
        var name = uri.lastPathSegment?.substringAfterLast('/') ?: "file"
        var size = 0L
        runCatching {
            context.contentResolver.query(uri, null, null, null, null)?.use { cursor ->
                if (cursor.moveToFirst()) {
                    val nameColumn = cursor.getColumnIndex(MediaStore.MediaColumns.DISPLAY_NAME)
                    val sizeColumn = cursor.getColumnIndex(MediaStore.MediaColumns.SIZE)
                    if (nameColumn >= 0 && !cursor.isNull(nameColumn)) {
                        name = cursor.getString(nameColumn)
                    }
                    if (sizeColumn >= 0 && !cursor.isNull(sizeColumn)) {
                        size = cursor.getLong(sizeColumn)
                    }
                }
            }
        }
        val mime = context.contentResolver.getType(uri) ?: guessMime(name)
        return Triple(name, size, mime)
    }

    // -- telling the user ----------------------------------------------------

    /** A notification for a file that has arrived, which opens it when tapped. */
    fun announce(context: Context, name: String, uri: String) {
        val manager = context.getSystemService(NotificationManager::class.java) ?: return
        ensureChannel(context, manager)

        val open = runCatching {
            val intent = Intent(Intent.ACTION_VIEW).apply {
                setDataAndType(Uri.parse(uri), guessMime(name))
                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
            }
            PendingIntent.getActivity(
                context, uri.hashCode(), intent,
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
            )
        }.getOrNull()

        val notification = Notification.Builder(context, CHANNEL_ID)
            .setContentTitle(context.getString(R.string.file_received_title))
            .setContentText(name)
            .setSmallIcon(R.drawable.ic_stat_tessera)
            .setAutoCancel(true)
            .apply { if (open != null) setContentIntent(open) }
            .build()
        runCatching { manager.notify(name.hashCode(), notification) }
    }

    private fun ensureChannel(context: Context, manager: NotificationManager) {
        if (manager.getNotificationChannel(CHANNEL_ID) != null) return
        manager.createNotificationChannel(
            NotificationChannel(
                CHANNEL_ID,
                context.getString(R.string.channel_files),
                NotificationManager.IMPORTANCE_DEFAULT,
            ).apply { description = context.getString(R.string.channel_files_description) }
        )
    }

    // -- helpers -------------------------------------------------------------

    private fun downloads(): Uri =
        MediaStore.Downloads.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY)

    /**
     * A name from a desktop, reduced to something that cannot escape.
     *
     * It comes from another machine, so a path separator in it is either a
     * mistake or an attempt to write somewhere else. Only the last component
     * survives.
     */
    fun safeName(name: String): String {
        val last = name.replace('\\', '/').substringAfterLast('/').trim()
        val cleaned = last.filter { it.isLetterOrDigit() || it in " .,-_()[]'#&+" }
            .trimStart('.')
        return if (cleaned.isEmpty()) "file" else cleaned.take(180)
    }

    fun guessMime(name: String): String {
        val extension = name.substringAfterLast('.', "").lowercase()
        return MimeTypeMap.getSingleton().getMimeTypeFromExtension(extension)
            ?: "application/octet-stream"
    }

    fun log(message: String) = Log.i(TAG, message)
}
