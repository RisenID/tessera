package dev.tessera.companion.protocol

import android.content.Context
import android.util.Log
import dev.tessera.companion.Bus
import dev.tessera.companion.Pairing
import dev.tessera.companion.TesseraService
import dev.tessera.companion.Store
import dev.tessera.companion.features.AppNames
import dev.tessera.companion.features.AppsRepository
import dev.tessera.companion.features.AudioStreamer
import dev.tessera.companion.features.CameraStreamer
import dev.tessera.companion.features.CallMonitor
import dev.tessera.companion.features.CallsRepository
import dev.tessera.companion.features.Capabilities
import dev.tessera.companion.features.ClipboardBridge
import dev.tessera.companion.features.ClipboardWatcher
import dev.tessera.companion.features.DndController
import dev.tessera.companion.features.FindPhone
import dev.tessera.companion.features.Hotspot
import dev.tessera.companion.features.MediaRepository
import dev.tessera.companion.features.NetworkAddresses
import dev.tessera.companion.features.NotificationBridge
import dev.tessera.companion.features.NowPlaying
import dev.tessera.companion.features.PhoneStatus
import dev.tessera.companion.features.PrivilegedShell
import dev.tessera.companion.features.ProjectionGrant
import dev.tessera.companion.features.TetheringController
import dev.tessera.companion.features.SmsRepository
import org.json.JSONObject
import java.io.BufferedOutputStream
import java.io.DataInputStream
import java.net.Socket
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.RejectedExecutionException
import java.util.concurrent.atomic.AtomicBoolean

/**
 * One connected desktop.
 *
 * Runs on its own thread for the life of the socket. Writes are serialised
 * through [writeLock] because a JSON header and the binary frame that follows
 * it must stay adjacent on the wire, and events can arrive from the
 * notification listener at any moment.
 */
class Session(
    private val context: Context,
    private val socket: Socket,
    private val store: Store,
) : Runnable {

    private val input = DataInputStream(socket.getInputStream().buffered())
    private val output = BufferedOutputStream(socket.getOutputStream())
    private val writeLock = Any()
    private val open = AtomicBoolean(true)

    /** Single writer thread: keeps frame order and keeps socket I/O off the
     *  threads that produce events (the main thread, the camera codec). */
    private val writer: ExecutorService =
        Executors.newSingleThreadExecutor { runnable -> Thread(runnable, "tessera-write") }

    private var authenticated = false
    private var subscriber: Bus.Subscriber? = null
    private var camera: CameraStreamer? = null
    private var audio: AudioStreamer? = null

    override fun run() {
        Log.i(TAG, "session from ${socket.inetAddress?.hostAddress}")
        try {
            loop()
        } catch (_: ClosedException) {
            // Normal disconnect.
        } catch (e: Exception) {
            Log.w(TAG, "session ended", e)
        } finally {
            close()
        }
    }

    private fun loop() {
        while (open.get()) {
            val message = Frames.readJson(input)
            if (!authenticated) {
                handleHandshake(message)
            } else {
                handleCommand(message)
            }
        }
    }

    // -- handshake -----------------------------------------------------------

    private fun handleHandshake(message: JSONObject) {
        when (message.optString("t")) {
            "hello" -> {
                val version = message.optInt("v")
                if (version != PROTOCOL_VERSION) {
                    send(JSONObject().put("t", "error").put("message", "protocol v$PROTOCOL_VERSION required"))
                    close()
                    return
                }
                send(
                    JSONObject()
                        .put("t", "hello")
                        .put("v", PROTOCOL_VERSION)
                        .put("id", store.deviceId)
                        .put("name", store.displayName)
                        .put("model", android.os.Build.MODEL)
                )
            }

            "pair" -> {
                if (Pairing.consume(message.optString("code"))) {
                    val token = store.addToken()
                    send(JSONObject().put("t", "pair_ok").put("token", token))
                } else {
                    send(
                        JSONObject().put("t", "pair_fail")
                            .put("message", "That code was wrong or has expired. Open the app on your phone for a new one.")
                    )
                }
            }

            "auth" -> {
                if (store.isKnown(message.optString("token"))) {
                    authenticated = true
                    send(
                        JSONObject()
                            .put("t", "auth_ok")
                            .put("caps", org.json.JSONArray(capabilities()))
                    )
                } else {
                    send(
                        JSONObject().put("t", "auth_fail")
                            .put("message", "This computer is not paired with the phone any more.")
                    )
                    close()
                }
            }

            else -> send(JSONObject().put("t", "error").put("message", "authenticate first"))
        }
    }

    /** Only advertise what the user has actually granted. */
    private fun capabilities(): List<String> = buildList {
        if (NotificationBridge.isConnected) add("notifications")
        if (DndController.canControl(context) || NotificationBridge.isConnected) add("dnd")
        if (SmsRepository.canRead(context)) add("sms")
        if (SmsRepository.canSend(context)) add("sms_send")
        if (MediaRepository.canRead(context)) add("media")
        add("camera")
        // Ringing needs nothing granted: it plays the phone's own ringtone.
        add("ring")
        // Playback capture: what the phone is playing, sent to the desktop.
        // Android 10 and later; the permission is asked for when it starts.
        if (AudioStreamer.supported()) add("phone_audio")
        // Whether it can start without the phone being touched, and whether
        // that can be arranged from the desktop if it cannot.
        if (ProjectionGrant.allowed(context)) add("phone_audio_silent")
        if (ProjectionGrant.grantable()) add("phone_audio_grant")
        add("apps")
        add("media_control")
        if (ClipboardBridge.available()) add("clipboard")
        if (CallsRepository.canReadLog(context)) add("calls")
        if (CallsRepository.canControl(context)) add("call_control")
        // Only claim "hotspot" if the phone will actually take the command.
        if (Hotspot.mode() == Hotspot.Mode.PRIVILEGED) add("hotspot") else add("hotspot_panel")
        add("net_addresses")
        // Battery, signal and ringer state for the desktop's device panel.
        add("status")
        add("ringer")
        // The desktop can wait for a hotspot switched on by hand, because the
        // phone can tell it has come up without any privilege at all.
        add("hotspot_wait")
    }

    // -- commands ------------------------------------------------------------

    private fun handleCommand(message: JSONObject) {
        // "req", not "id": commands like media_get and notif_dismiss carry their
        // own "id", and reusing that key overwrote them.
        val id = if (message.has("req")) message.optInt("req") else null
        when (val kind = message.optString("t")) {
            "sub" -> subscribe()

            "notif_dismiss" ->
                NotificationBridge.instance?.dismiss(message.optString("id"))

            "notif_reply" -> {
                val sent = NotificationBridge.instance
                    ?.reply(message.optString("id"), message.optString("text")) ?: false
                if (!sent) fail(id, "That notification can no longer be replied to.")
            }

            "notif_list" -> reply(
                id,
                JSONObject().put(
                    "items",
                    org.json.JSONArray(NotificationBridge.instance?.snapshot().orEmpty())
                )
            )

            "dnd_set" -> {
                val mode = message.optString("mode")
                if (!DndController.apply(context, mode)) {
                    fail(id, "Do Not Disturb could not be changed. Grant notification access on the phone.")
                }
            }

            "dnd_get" -> reply(id, JSONObject().put("mode", DndController.current(context)))

            "icon_get" -> {
                val packageName = message.optString("icon")
                val bytes = AppNames.icon(context, packageName)
                if (bytes == null) fail(id, "no icon") else sendBinary(
                    JSONObject().put("t", "icon").put("icon", packageName), bytes, id
                )
            }

            "sms_threads" -> reply(
                id, JSONObject().put("items", SmsRepository.threads(context, message.optInt("limit", 200)))
            )

            "sms_messages" -> reply(
                id,
                JSONObject().put(
                    "items",
                    SmsRepository.messages(
                        context,
                        limit = message.optInt("limit", 500),
                        threadId = message.optString("thread").takeIf { it.isNotEmpty() },
                    )
                )
            )

            "sms_send" -> {
                val error = SmsRepository.send(context, message.optString("address"), message.optString("text"))
                if (error != null) fail(id, error) else reply(id, JSONObject().put("sent", true))
            }

            "media_list" -> reply(
                id,
                JSONObject().put(
                    "items",
                    MediaRepository.list(context, message.optInt("limit", 300), message.optInt("offset", 0))
                )
            )

            "media_get" -> {
                val mediaId = message.optString("id")
                val bytes = if (message.optBoolean("thumb"))
                    MediaRepository.thumbnail(context, mediaId)
                else
                    MediaRepository.original(context, mediaId)
                if (bytes == null) {
                    fail(id, "That item could not be read.")
                } else {
                    sendBinary(
                        JSONObject().put("t", "media").put("id", mediaId)
                            .put("thumb", message.optBoolean("thumb")),
                        bytes,
                        id,
                    )
                }
            }

            "camera_start" -> startCamera(message, id)
            "camera_stop" -> stopCamera()

            // Finding the phone. On the alarm stream, so a silenced phone
            // still answers -- which is the phone that usually needs finding.
            "ring" -> {
                val problem = FindPhone.start(context)
                if (problem != null) fail(id, problem)
                else reply(id, JSONObject().put("ringing", true)
                    .put("stopsInMs", FindPhone.remainingMs))
            }

            "ring_stop" -> {
                FindPhone.stop(context)
                reply(id, JSONObject().put("ringing", false))
            }

            "audio_start" -> startAudio(id)
            "audio_stop" -> stopAudio(notify = true)

            // One-time: stop Android asking before every stream. Runs through
            // the same Shizuku shell the hotspot uses, so it needs nothing on
            // the phone -- which is the point.
            "audio_grant" -> {
                val service = TesseraService.running_instance
                if (service == null) {
                    fail(id, "The companion service is not running on the phone.")
                } else {
                    // null means it worked; anything else is why it did not.
                    val problem = service.grantProjection()
                    if (problem == null) {
                        reply(id, JSONObject().put("granted", true))
                        send(
                            JSONObject().put("t", "caps")
                                .put("caps", org.json.JSONArray(capabilities()))
                        )
                    } else {
                        fail(id, problem)
                    }
                }
            }

            "hotspot_start" -> {
                val error = Hotspot.start(
                    context,
                    message.optString("ssid"),
                    message.optString("passphrase"),
                    message.optString("band", "2.4"),
                )
                if (error != null) {
                    fail(id, error)
                } else {
                    val config = Hotspot.config()
                    val actual = Hotspot.activeBand()
                    reply(
                        id,
                        JSONObject()
                            .put("ssid", config?.first ?: message.optString("ssid"))
                            .put("passphrase", config?.second ?: message.optString("passphrase"))
                            .put("requestedBand", message.optString("band", "2.4"))
                            .put("band", actual?.first ?: "")
                            .put("frequency", actual?.second ?: 0)
                            // Where to find this phone once the desktop has
                            // left the network the two are talking over. This
                            // reply is the last chance to say.
                            .put("addresses", NetworkAddresses.listAfterTethering())
                    )
                }
            }

            "hotspot_stop" -> Hotspot.stop(context)?.let { fail(id, it) }
                ?: reply(id, JSONObject().put("stopped", true))

            // Read-only: reports whether privileged control actually works,
            // without changing the phone's network state.
            "hotspot_status" -> reply(
                id,
                JSONObject()
                    .put("mode", Hotspot.mode().name)
                    .put("shizuku", PrivilegedShell.available())
                    .put("permission", PrivilegedShell.hasPermission())
                    .put("enabled", Hotspot.enabled()?.toString() ?: "unknown")
                    .put("probe", PrivilegedShell.run("id").text)
                    .put("binder", TetheringController.available())
                    .put("ssid", Hotspot.config()?.first ?: "")
                    .put("passphrase", Hotspot.config()?.second ?: "")
                    // Read from the network interfaces rather than from
                    // "enabled" above, which needs the shell uid and so is
                    // always unknown on exactly the phones that have to use
                    // the panel. See NetworkAddresses.isSoftAp.
                    .put("tethering", NetworkAddresses.hasSoftAp())
                    .put("addresses", NetworkAddresses.list())
            )

            // Asked for on its own when the hotspot was started some other
            // way -- from the phone, or over adb -- and the desktop still needs
            // somewhere to reconnect to.
            "net_addresses" ->
                reply(id, JSONObject().put("addresses", NetworkAddresses.list()))

            "hotspot_panel" -> {
                Hotspot.openPanel(context)
                reply(id, JSONObject().put("opened", true))
            }

            "app_list" -> reply(id, JSONObject().put("items", AppsRepository.launchable(context)))

            "app_launch" -> {
                if (AppsRepository.launch(context, message.optString("package"))) {
                    reply(id, JSONObject().put("launched", true))
                } else {
                    fail(id, "That app could not be opened.")
                }
            }

            // What this phone can actually do, so the desktop offers real
            // choices instead of a hardcoded list.
            "device_caps" -> reply(
                id,
                JSONObject()
                    .put("cameras", Capabilities.cameras(context))
                    .put("hotspotBands", Capabilities.hotspotBands(context))
            )

            "clipboard_set" -> {
                val text = message.optString("text")
                // Remember it first: the watcher must not announce our own
                // write back to the desktop that sent it.
                ClipboardWatcher.note(text)
                if (!ClipboardBridge.write(text)) {
                    fail(id, "The phone would not let Tessera set the clipboard.")
                }
            }

            "clipboard_get" -> reply(
                id, JSONObject().put("text", ClipboardBridge.read().orEmpty())
            )

            "calls_recent" -> reply(
                id,
                JSONObject().put(
                    "items", CallsRepository.recent(context, message.optInt("limit", 100))
                )
            )

            "call_answer" -> CallsRepository.answer(context)?.let { fail(id, it) }
                ?: reply(id, JSONObject().put("answered", true))

            "call_end" -> CallsRepository.hangUp(context)?.let { fail(id, it) }
                ?: reply(id, JSONObject().put("ended", true))

            "call_dial" -> {
                val note = CallsRepository.dial(context, message.optString("number"))
                // dial() returns a message even on success when it only opened
                // the dialer, so report it without treating it as a failure.
                reply(id, JSONObject().put("note", note ?: ""))
            }

            "call_state" -> reply(id, CallMonitor.snapshot(context))

            "ringer_set" -> {
                if (!PhoneStatus.setRinger(context, message.optString("mode"))) {
                    fail(
                        id,
                        "The ringer could not be changed. Silencing needs " +
                            "notification access, which is granted on the phone."
                    )
                }
            }

            "media_state" -> reply(id, NowPlaying.snapshot())


            "media_command" -> {
                if (!NowPlaying.command(message.optString("action"))) {
                    fail(id, "Nothing on the phone is playing, or it refused the command.")
                }
            }

            "ping" -> reply(id, JSONObject().put("pong", true))

            else -> Log.d(TAG, "ignoring unknown command $kind")
        }
    }

    private fun subscribe() {
        if (subscriber != null) return
        val listener = Bus.Subscriber { event -> send(event) }
        subscriber = listener
        Bus.subscribe(listener)
        ClipboardWatcher.addUser()
        CallMonitor.addUser(context)
        NowPlaying.activeContext = context
        NowPlaying.addUser(context)
        PhoneStatus.addUser(context)

        // Send current state immediately: a desktop that just connected should
        // not have to wait for the next change to know what is on the phone.
        NotificationBridge.instance?.snapshot()?.forEach(::send)
        send(JSONObject().put("t", "dnd").put("mode", DndController.current(context)))
        send(CallMonitor.snapshot(context))
        send(NowPlaying.snapshot())
        send(PhoneStatus.snapshot(context))
    }

    // -- camera --------------------------------------------------------------

    private fun startCamera(message: JSONObject, id: Int?) {
        stopCamera()
        val streamer = CameraStreamer(
            context = context,
            facing = message.optString("facing", "back"),
            cameraId = message.optString("cameraId", ""),
            width = message.optInt("width", 1280),
            height = message.optInt("height", 720),
            fps = message.optInt("fps", 30),
            onConfigured = { header -> send(header) },
            onFrame = { frame, isKey, pts ->
                sendBinary(
                    JSONObject().put("t", "camera_frame").put("key", isKey).put("pts", pts),
                    frame,
                    null,
                )
            },
            onError = { reason -> fail(id, reason) },
        )
        val service = TesseraService.running_instance
        if (service == null || !service.setCameraActive(true)) {
            fail(
                id,
                "The phone will not allow camera access to a background service. " +
                    "Open Phone Link on the phone once, then start the camera again."
            )
            return
        }
        camera = streamer
        streamer.start()
    }

    private fun stopCamera() {
        camera?.stop()
        camera = null
        TesseraService.running_instance?.setCameraActive(false)
    }

    // -- the phone's audio ---------------------------------------------------

    /**
     * Starts sending what the phone is playing.
     *
     * Nothing here happens on a mere connection: the desktop asks for this
     * because someone pressed a button, which is the rule this project keeps --
     * connecting must never take audio off the phone's own headphones. Playback
     * capture cannot do that in any case, which is why it is the route chosen.
     *
     * The user has to consent on the phone. If they have not, the service asks
     * and the stream starts when they answer; the desktop is told to expect
     * that rather than left waiting.
     */
    private fun startAudio(id: Int?) {
        if (!AudioStreamer.supported()) return fail(id, AudioStreamer.UNSUPPORTED)
        if (!AudioStreamer.canCapture(context)) return fail(id, AudioStreamer.NO_PERMISSION)

        val service = TesseraService.running_instance
            ?: return fail(id, "The companion service is not running on the phone.")

        stopAudio()
        val projection = service.audioProjection()
        if (projection == null) {
            val quiet = service.askForAudioConsent { if (open.get()) beginAudio(service) }
            send(
                JSONObject()
                    .put("t", "audio_consent")
                    .put("silent", quiet)
                    .put(
                        "message",
                        if (quiet) "Asking the phone; this takes a moment."
                        else "Tap the notification on the phone to allow it to " +
                            "send its audio. Android asks every time until the " +
                            "one-time permission is granted."
                    )
            )
            return
        }
        beginAudio(service, projection)
    }

    /** Second half of [startAudio], also the callback for a late consent. */
    private fun beginAudio(service: TesseraService, ready: android.media.projection.MediaProjection? = null) {
        val projection = ready ?: service.audioProjection() ?: run {
            fail(null, "The phone would not allow its audio to be captured.")
            return
        }
        val streamer = AudioStreamer(
            context = context,
            projection = projection,
            onStarted = { header -> send(header) },
            onFrame = { frame ->
                sendBinary(JSONObject().put("t", "audio_frame"), frame, null)
            },
            onError = { reason ->
                fail(null, reason)
                stopAudio()
            },
        )
        audio = streamer
        streamer.start()
    }

    /** Stops the stream and tells the desktop, whoever asked. */
    private fun stopAudio(notify: Boolean = false) {
        val streamer = audio ?: run {
            TesseraService.running_instance?.clearPendingAudio()
            return
        }
        audio = null
        streamer.stop()
        TesseraService.running_instance?.let {
            it.clearPendingAudio()
            it.setAudioActive(false)
        }
        if (notify) send(JSONObject().put("t", "audio_stopped"))
    }

    /**
     * The user revoked the projection from the status bar.
     *
     * Called by the service, on any session: only the one that is streaming
     * has anything to do, and the desktop needs telling because it did not ask
     * for this.
     */
    fun stopAudioFromSystem() {
        if (audio == null) return
        stopAudio(notify = true)
    }

    // -- writing -------------------------------------------------------------

    /**
     * Queues a message for the writer thread.
     *
     * Never writes on the caller's thread. Notification and DND events are
     * published from the main thread by the platform's listener callbacks, and
     * writing a socket there throws NetworkOnMainThreadException, which used to
     * tear down the whole session the moment a notification arrived.
     */
    private fun send(message: JSONObject) {
        if (!open.get()) return
        submitWrite {
            Frames.writeJson(output, message)
            output.flush()
        }
    }

    private fun submitWrite(body: () -> Unit) {
        if (!open.get()) return
        try {
            writer.execute {
                if (!open.get()) return@execute
                try {
                    // A single writer thread keeps frames in order, and keeps a
                    // JSON header adjacent to the binary frame that follows it.
                    synchronized(writeLock) { body() }
                } catch (e: Exception) {
                    Log.w(TAG, "write failed", e)
                    close()
                }
            }
        } catch (e: RejectedExecutionException) {
            // The session is shutting down; nothing left to write to.
            Log.d(TAG, "write after close: ${e.message}")
        }
    }

    private fun sendBinary(header: JSONObject, payload: ByteArray, id: Int?) {
        if (!open.get()) return
        if (id != null) header.put("rid", id)
        header.put("binary", true).put("length", payload.size)
        submitWrite {
            Frames.writeJson(output, header)
            Frames.writeBinary(output, payload)
            output.flush()
        }
    }

    private fun reply(id: Int?, body: JSONObject) {
        if (id == null) return
        send(body.put("t", body.optString("t", "reply")).put("rid", id))
    }

    private fun fail(id: Int?, reason: String) {
        val message = JSONObject().put("t", "error").put("message", reason)
        if (id != null) message.put("rid", id)
        send(message)
    }

    fun close() {
        if (!open.compareAndSet(true, false)) return
        subscriber?.let {
            Bus.unsubscribe(it)
            ClipboardWatcher.removeUser()
            CallMonitor.removeUser(context)
            NowPlaying.removeUser()
            PhoneStatus.removeUser(context)
        }
        subscriber = null
        stopCamera()
        stopAudio()
        writer.shutdownNow()
        runCatching { socket.close() }
        Log.i(TAG, "session closed")
    }

    companion object {
        private const val TAG = "TesseraSession"
        const val PROTOCOL_VERSION = 1
    }
}
