package dev.tessera.companion.features

import android.content.ComponentName
import android.content.Context
import android.media.AudioManager
import android.media.MediaMetadata
import android.media.session.MediaController
import android.media.session.MediaSessionManager
import android.media.session.PlaybackState
import android.os.Handler
import android.os.Looper
import android.util.Log
import dev.tessera.companion.Bus
import org.json.JSONObject

/** What the phone is playing, read from MediaSession. */
object NowPlaying {

    private const val TAG = "TesseraMedia"

    private var manager: MediaSessionManager? = null
    private var controller: MediaController? = null
    private var callback: MediaController.Callback? = null
    private var users = 0

    /** Callbacks are delivered on the main looper. */
    private val mainHandler = Handler(Looper.getMainLooper())

    private val sessionsListener =
        MediaSessionManager.OnActiveSessionsChangedListener { controllers ->
            attach(controllers.orEmpty())
        }

    @Synchronized
    fun addUser(context: Context) {
        users++
        if (users > 1) return
        runCatching {
            val service = context.getSystemService(MediaSessionManager::class.java) ?: return
            val listener = ComponentName(context, NotificationBridge::class.java)
            service.addOnActiveSessionsChangedListener(sessionsListener, listener, mainHandler)
            attach(service.getActiveSessions(listener))
            manager = service
            Log.i(TAG, "watching media sessions")
        }.onFailure { Log.w(TAG, "could not watch media sessions", it) }
        A2dpCodec.start(context) { mainHandler.post { publish() } }
    }

    @Synchronized
    fun removeUser() {
        users = (users - 1).coerceAtLeast(0)
        if (users > 0) return
        activeContext?.let { A2dpCodec.stop(it) }
        runCatching { manager?.removeOnActiveSessionsChangedListener(sessionsListener) }
        detach()
        manager = null
        Log.i(TAG, "stopped watching media sessions")
    }

    /** The current state, for a desktop that has just connected. */
    fun snapshot(): JSONObject = describe(controller)

    fun command(action: String): Boolean {
        val transport = controller?.transportControls ?: return false
        return runCatching {
            when (action) {
                "play" -> transport.play()
                "pause" -> transport.pause()
                "playpause" ->
                    if (controller?.playbackState?.state == PlaybackState.STATE_PLAYING) {
                        transport.pause()
                    } else {
                        transport.play()
                    }
                "next" -> transport.skipToNext()
                "previous" -> transport.skipToPrevious()
                "stop" -> transport.stop()
                else -> return false
            }
            true
        }.getOrElse {
            Log.w(TAG, "media command $action failed", it)
            false
        }
    }

    // -- plumbing ------------------------------------------------------------

    /** Follows whichever session is actually playing. */
    private fun attach(controllers: List<MediaController>) {
        val playing = controllers.firstOrNull {
            it.playbackState?.state == PlaybackState.STATE_PLAYING
        }
        val chosen = playing ?: controllers.firstOrNull()

        if (chosen?.sessionToken == controller?.sessionToken) {
            publish()
            return
        }

        detach()
        controller = chosen ?: return

        val watcher = object : MediaController.Callback() {
            override fun onMetadataChanged(metadata: MediaMetadata?) = publish()
            override fun onPlaybackStateChanged(state: PlaybackState?) = publish()
            override fun onSessionDestroyed() {
                detach()
                publish()
            }
        }
        callback = watcher
        chosen.registerCallback(watcher, mainHandler)
        publish()
    }

    private fun detach() {
        callback?.let { runCatching { controller?.unregisterCallback(it) } }
        callback = null
        controller = null
    }

    private fun publish() {
        Bus.publish(describe(controller))
    }

    /** Why the phone might not be sending audio anywhere. */
    private fun audioMode(context: Context?): String {
        val manager = context?.getSystemService(AudioManager::class.java) ?: return ""
        return when (manager.mode) {
            AudioManager.MODE_RINGTONE -> "ringtone"
            AudioManager.MODE_IN_CALL -> "in_call"
            AudioManager.MODE_IN_COMMUNICATION -> "in_communication"
            AudioManager.MODE_NORMAL -> "normal"
            else -> "other"
        }
    }

    private fun describe(active: MediaController?): JSONObject {
        val context = activeContext
        val message = JSONObject()
            .put("t", "media")
            .put("audioMode", audioMode(context))
        A2dpCodec.describe()?.let { message.put("bluetooth", it) }
        if (active == null) {
            return message.put("playing", false).put("title", "")
        }

        val metadata = active.metadata
        val state = active.playbackState?.state
        return message
            .put("app", AppNames.label(context ?: return message, active.packageName))
            .put("package", active.packageName)
            .put("title", metadata?.getString(MediaMetadata.METADATA_KEY_TITLE).orEmpty())
            .put("artist", metadata?.getString(MediaMetadata.METADATA_KEY_ARTIST).orEmpty())
            .put("album", metadata?.getString(MediaMetadata.METADATA_KEY_ALBUM).orEmpty())
            .put("duration", metadata?.getLong(MediaMetadata.METADATA_KEY_DURATION) ?: 0L)
            .put("position", active.playbackState?.position ?: 0L)
            .put("playing", state == PlaybackState.STATE_PLAYING)
            .put("canControl", active.transportControls != null)
    }

    /** Set once so labels can be resolved without threading a Context around. */
    @Volatile
    var activeContext: Context? = null
}
