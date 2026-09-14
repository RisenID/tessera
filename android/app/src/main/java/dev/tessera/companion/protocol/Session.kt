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
import dev.tessera.companion.features.FileTransfer
import dev.tessera.companion.features.StorageServer
import dev.tessera.companion.features.Wallpaper
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
import java.util.concurrent.Semaphore
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.concurrent.thread

/** One connected desktop. */
class Session(
    private val context: Context,
    private val socket: Socket,
    private val store: Store,
) : Runnable {

    // A 64 kB buffer rather than the default 8: every file chunk is 256 kB, and refilling eight
    // kilobytes at a time was pure overhead on the one path where throughput matters.
    private val input = DataInputStream(socket.getInputStream().buffered(64 * 1024))
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

    /** Files arriving from the desktop, by transfer id. */
    private val incoming = java.util.concurrent.ConcurrentHashMap<String, FileTransfer.Incoming>()
    /** The file going the other way. One at a time: two files sharing the
     *  link finish in twice the time each and neither progress bar means
     *  anything. */
    private var outgoing: FileTransfer.Outgoing? = null
    private val toSend = java.util.concurrent.ConcurrentLinkedQueue<android.net.Uri>()

    /** How many chunks may be queued for the writer at once. */
    private val sendWindow = Semaphore(8)

    /** The header whose binary frame has not arrived yet. */
    private var pendingBinary: JSONObject? = null

    /**
     * What this connection is for. "" is a desktop's main link; "files" is the
     * second connection the same desktop opens for transfers alone, so a large
     * file never queues ahead of that desktop's audio and notifications.
     */
    @Volatile
    var role: String = ""
        private set

    /** Which paired desktop this is, so its two connections can be told apart from another's. */
    @Volatile
    var token: String = ""
        private set

    /** The computer's own name, as it sent it. */
    @Volatile
    var computerName: String = ""
        private set

    /** Tags events this session caused, so they are not echoed back to it. */
    private val key = Store.randomHex(8)

    /** Requests this phone made of the desktop, by id. */
    private val queries = java.util.concurrent.ConcurrentHashMap<Int, (JSONObject?) -> Unit>()
    private val nextQuery = java.util.concurrent.atomic.AtomicInteger(1)

    val isAuthenticated: Boolean
        get() = authenticated

    /** Whether this desktop is holding the storage server open. */
    private var usingStorage = false

    override fun run() {
        Log.i(TAG, "session from ${socket.inetAddress?.hostAddress}")
        // Without this a file chunk -- a small header, then a large payload -- waits for an
        // acknowledgement between the two, which cost about 150 ms per chunk and held transfers to
        // a megabyte a second on a link capable of far more.
        runCatching { socket.tcpNoDelay = true }
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
            val frame = Frames.read(input)
            when (frame.type) {
                Frames.TYPE_JSON -> {
                    val message = JSONObject(String(frame.payload, Charsets.UTF_8))
                    when {
                        !authenticated -> handleHandshake(message)
                        // A header that says "binary" describes the frame immediately after it, and
                        // is not a command in itself.
                        message.optBoolean("binary") -> pendingBinary = message
                        else -> handleCommand(message)
                    }
                }
                Frames.TYPE_BINARY -> {
                    if (!authenticated) throw ProtocolException("binary frame before auth")
                    handleBinary(frame.payload)
                }
                else -> Log.d(TAG, "ignoring frame type ${frame.type}")
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
                    val token = store.addToken(message.optString("name"))
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
                    token = message.optString("token")
                    role = message.optString("role")
                    computerName = message.optString("name")
                    store.noteComputer(token, computerName)
                    send(
                        JSONObject()
                            .put("t", "auth_ok")
                            .put("caps", org.json.JSONArray(capabilities()))
                    )
                    TesseraService.running_instance?.onAuthenticated(this)
                    // Older desktops don't send a name with auth; ask for it.
                    if (computerName.isBlank() && role.isEmpty()) refreshName()
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
        // Whether the phone can be kept quiet while the desktop plays the
        // copy, so the same track is not coming out of both at once.
        if (AudioStreamer.supported()) add("phone_audio_mute")
        add("apps")
        add("media_control")
        if (ClipboardBridge.available()) add("clipboard")
        // Reading through the accessibility route takes focus for an instant,
        // so the desktop should not ask for the clipboard idly.
        if (ClipboardBridge.route() == "accessibility") add("clipboard_accessibility")
        // Files both ways, and the phone's share sheet: MediaStore's Downloads
        // collection needs no permission, so this is always available.
        add("file_transfer")
        // The phone's own look, for the desktop's sidebar.
        add("wallpaper")
        // A second connection for transfers alone.
        add("file_channel")
        // The phone's storage as a folder on the desktop, and whether it can
        // be allowed from there rather than on this screen.
        if (StorageServer.supported()) add("storage")
        if (StorageServer.allowed()) add("storage_allowed")
        if (StorageServer.grantable() && !StorageServer.allowed()) add("storage_grant")
        if (CallsRepository.canReadLog(context)) add("calls")
        if (CallsRepository.canControl(context)) add("call_control")
        // Only claim "hotspot" if the phone will actually take the command.
        if (Hotspot.mode() == Hotspot.Mode.PRIVILEGED) add("hotspot") else add("hotspot_panel")
        add("net_addresses")
        add("media_volume")
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
        if (message.has("rid")) {
            queries.remove(message.optInt("rid"))?.invoke(message)
            return
        }
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
                // Off the reader: checking the change took waits a moment.
                thread(name = "tessera-dnd") {
                    val problem = DndController.set(context, mode)
                    if (problem == null) {
                        reply(id, JSONObject().put("mode", DndController.current(context)))
                    } else {
                        fail(id, problem)
                    }
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

            // -- the phone's storage, for the desktop to mount --------------
            "storage_start" -> {
                try {
                    val info = StorageServer.start(context)
                    // start() counts a user each time; this desktop is one.
                    if (usingStorage) StorageServer.release() else usingStorage = true
                    reply(
                        id,
                        JSONObject()
                            .put("port", info.port)
                            .put("user", StorageServer.USER)
                            .put("password", info.password)
                            .put("path", info.path)
                            .put("hostKey", info.hostKey)
                    )
                } catch (e: Exception) {
                    Log.w(TAG, "file server did not start", e)
                    fail(
                        id,
                        if (!StorageServer.allowed())
                            "The phone has not allowed All files access, which its storage needs."
                        else "The phone could not start its file server: ${e.message}"
                    )
                }
            }
            "storage_stop" -> {
                if (usingStorage) {
                    usingStorage = false
                    StorageServer.release()
                }
                reply(id, JSONObject().put("stopped", true))
            }
            "storage_grant" -> {
                val problem = StorageServer.grant(context)
                if (problem == null) reply(id, JSONObject().put("granted", true))
                else fail(id, problem)
            }

            "wallpaper_get" -> {
                val colour = Wallpaper.colour(context)
                val header = JSONObject().put("t", "wallpaper")
                if (colour != null) header.put("colour", colour)
                val image = Wallpaper.jpeg(context)
                if (image != null) {
                    sendBinary(header.put("format", "jpeg"), image, id)
                } else {
                    // No picture, but the colours are still worth having: the
                    // desktop tints its own tile rather than showing nothing.
                    reply(id, header)
                }
            }

            // -- files, both directions ------------------------------------
            "file_offer" -> receiveOffer(message)
            "file_done" -> finishIncoming(message)
            "file_cancel" -> cancelTransfer(message)
            "file_accept" -> startOutgoing(message)
            "file_reject" -> {
                Log.i(TAG, "the desktop refused a file: ${message.optString("message")}")
                endOutgoing()
            }
            "file_saved" -> Log.i(TAG, "the desktop saved ${message.optString("path")}")

            "audio_start" -> startAudio(id, message.optBoolean("mute", false))
            "audio_stop" -> stopAudio(notify = true)

            // The desktop's "keep the phone quiet" checkbox, pressed while the
            // music is already playing.
            "audio_mute" -> {
                val on = message.optBoolean("on", false)
                val muted = audio?.setMuted(on) ?: false
                reply(id, JSONObject().put("muted", muted).put("streaming", audio != null))
            }

            // One-time: stop Android asking before every stream.
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
                            // Where to find this phone once the desktop has left the network the
                            // two are talking over.
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
                    // Read from the network interfaces rather than from "enabled" above, which
                    // needs the shell uid and so is always unknown on exactly the phones that
                    // have to use the panel.
                    .put("tethering", NetworkAddresses.hasSoftAp())
                    .put("addresses", NetworkAddresses.list())
            )

            // Asked for on its own when the hotspot was started some other way -- from the phone,
            // or over adb -- and the desktop still needs somewhere to reconnect to.
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
                } else {
                    // Pass it on to any other connected computer.
                    Bus.publish(
                        JSONObject().put("t", "clipboard").put("text", text).put("origin", key)
                    )
                }
            }

            "clipboard_get" -> {
                val own = ClipboardBridge.read()
                val service = TesseraService.running_instance
                if (!message.optBoolean("latest") || service == null) {
                    reply(id, JSONObject().put("text", own.orEmpty()))
                } else {
                    // The newest of the phone's clipboard and the other computers'.
                    val phone = own?.let { Store.Computer("", "phone", ClipboardWatcher.changedAt) to it }
                    service.latestClipboard(exclude = this, phone = phone) { text, from ->
                        reply(id, JSONObject().put("text", text.orEmpty()).put("from", from.orEmpty()))
                    }
                }
            }

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

            "volume_set" -> {
                if (!PhoneStatus.setMediaVolume(context, message.optInt("percent", -1))) {
                    fail(id, "The phone's volume could not be changed.")
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
        val listener = Bus.Subscriber { event ->
            if (!event.has("origin")) {
                send(event)
            } else if (event.optString("origin") != key) {
                send(JSONObject(event.toString()).apply { remove("origin") })
            }
        }
        subscriber = listener
        Bus.subscribe(listener)
        ClipboardWatcher.addUser(context)
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

    /** Starts sending what the phone is playing. */
    private fun startAudio(id: Int?, mute: Boolean) {
        if (!AudioStreamer.supported()) return fail(id, AudioStreamer.UNSUPPORTED)
        if (!AudioStreamer.canCapture(context)) return fail(id, AudioStreamer.NO_PERMISSION)

        val service = TesseraService.running_instance
            ?: return fail(id, "The companion service is not running on the phone.")

        stopAudio()
        val projection = service.audioProjection()
        if (projection == null) {
            val quiet = service.askForAudioConsent { if (open.get()) beginAudio(service, mute = mute) }
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
        beginAudio(service, projection, mute)
    }

    /** Second half of [startAudio], also the callback for a late consent. */
    private fun beginAudio(
        service: TesseraService,
        ready: android.media.projection.MediaProjection? = null,
        mute: Boolean = false,
    ) {
        val projection = ready ?: service.audioProjection() ?: run {
            fail(null, "The phone would not allow its audio to be captured.")
            return
        }
        val streamer = AudioStreamer(
            context = context,
            projection = projection,
            mutePhone = mute,
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

    // -- files ---------------------------------------------------------------

    /** The desktop is offering a file. */
    private fun receiveOffer(message: JSONObject) {
        val id = message.optString("id")
        if (id.isEmpty()) return
        val name = message.optString("name", "file")
        val size = message.optLong("size", 0L)

        val transfer = FileTransfer.Incoming(
            context, id, name, size, message.optString("mime"),
        )
        val problem = transfer.open()
        if (problem != null) {
            send(JSONObject().put("t", "file_reject").put("id", id).put("message", problem))
            return
        }
        incoming[id] = transfer
        send(JSONObject().put("t", "file_accept").put("id", id))
    }

    private fun handleBinary(payload: ByteArray) {
        val header = pendingBinary
        pendingBinary = null
        if (header == null || header.optString("t") != "file_chunk") return
        val id = header.optString("id")
        val transfer = incoming[id] ?: return
        runCatching { transfer.write(payload) }.onFailure { error ->
            Log.w(TAG, "could not write an incoming file", error)
            incoming.remove(id)?.discard()
            send(
                JSONObject().put("t", "file_cancel").put("id", id)
                    .put("message", error.message ?: "the phone could not write the file")
            )
        }
    }

    /** The last chunk has landed: publish it and say where it went. */
    private fun finishIncoming(message: JSONObject) {
        val id = message.optString("id")
        val transfer = incoming.remove(id) ?: return
        val uri = transfer.finish()
        if (uri.isEmpty()) {
            // The writer failed at the last moment.
            send(
                JSONObject().put("t", "file_cancel").put("id", id)
                    .put("message", "the phone could not finish writing the file")
            )
            return
        }
        FileTransfer.announce(context, transfer.name, uri)
        send(JSONObject().put("t", "file_saved").put("id", id).put("path", uri))
    }

    private fun cancelTransfer(message: JSONObject) {
        val id = message.optString("id")
        incoming.remove(id)?.discard()
        if (outgoing?.id == id) endOutgoing()
    }

    /** Send files to this desktop. Called by the share sheet. */
    fun offerFiles(uris: List<android.net.Uri>) {
        if (!open.get() || !authenticated) return
        toSend.addAll(uris)
        startNextOutgoing()
    }

    /** Sends a request to the desktop; the callback gets its answer, or null. */
    private fun query(kind: String, callback: (JSONObject?) -> Unit) {
        if (!open.get() || !authenticated || role.isNotEmpty()) {
            callback(null)
            return
        }
        val id = nextQuery.getAndIncrement()
        queries[id] = callback
        send(JSONObject().put("t", kind).put("req", id))
    }

    /** Asks the desktop for its clipboard and when it last changed. */
    fun queryClipboard(callback: (JSONObject?) -> Unit) = query("clipboard_query", callback)

    /** Asks the desktop for its name and stores it. */
    fun refreshName() = query("computer_info") { answer ->
        val name = answer?.optString("name").orEmpty()
        if (name.isNotBlank() && name != computerName) {
            computerName = name
            store.noteComputer(token, name)
            TesseraService.onSessionsChanged?.invoke()
        }
    }

    /** Text shared from the phone, put on the desktop's clipboard. */
    fun offerText(text: String) {
        if (!open.get() || !authenticated || text.isEmpty()) return
        send(JSONObject().put("t", "clipboard").put("text", text))
    }

    @Synchronized
    private fun startNextOutgoing() {
        if (outgoing != null) return
        val uri = toSend.poll() ?: return
        val (name, size, mime) = FileTransfer.describe(context, uri)
        val transfer = FileTransfer.Outgoing(
            context, Store.randomHex(8), uri, name, size, mime,
        )
        if (!transfer.open()) {
            Log.w(TAG, "could not read $name")
            startNextOutgoing()
            return
        }
        outgoing = transfer
        send(
            JSONObject().put("t", "file_offer").put("id", transfer.id)
                .put("name", name).put("size", size).put("mime", mime)
        )
    }

    /** The desktop accepted: read the file and write it out, chunk by chunk. */
    private fun startOutgoing(message: JSONObject) {
        val transfer = outgoing ?: return
        if (message.optString("id") != transfer.id) return

        thread(name = "tessera-file") {
            var failed = false
            while (open.get()) {
                val chunk = try {
                    transfer.next()
                } catch (e: Exception) {
                    Log.w(TAG, "reading ${transfer.name} failed", e)
                    failed = true
                    null
                } ?: break

                // Wait for room rather than queueing the whole file: the writer's queue is
                // unbounded, and reading from storage is far faster than a Wi-Fi link, so without
                // this the file would sit in memory in its entirety.
                if (!sendWindow.tryAcquire(10, java.util.concurrent.TimeUnit.SECONDS)) {
                    if (!open.get()) break
                    continue
                }
                val header = JSONObject().put("t", "file_chunk").put("id", transfer.id)
                    .put("binary", true).put("length", chunk.size)
                submitWrite {
                    try {
                        Frames.writeJson(output, header)
                        Frames.writeBinary(output, chunk)
                        output.flush()
                    } finally {
                        sendWindow.release()
                    }
                }
                // Nothing else to do until there is room; the permit released
                // by the writer is what paces this loop.
            }

            if (open.get()) {
                if (failed) {
                    send(
                        JSONObject().put("t", "file_cancel").put("id", transfer.id)
                            .put("message", "the phone could not read the file")
                    )
                } else {
                    send(JSONObject().put("t", "file_done").put("id", transfer.id))
                }
            }
            endOutgoing()
        }
    }

    @Synchronized
    private fun endOutgoing() {
        outgoing?.let {
            it.cancel()
            it.close()
        }
        outgoing = null
        startNextOutgoing()
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

    /** The user revoked the projection from the status bar. */
    fun stopAudioFromSystem() {
        if (audio == null) return
        stopAudio(notify = true)
    }

    // -- writing -------------------------------------------------------------

    /** Queues a message for the writer thread. */
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
        // Half-written files are thrown away rather than published: an
        // interrupted transfer must not look like a complete download.
        incoming.values.forEach { it.discard() }
        incoming.clear()
        queries.values.forEach { runCatching { it(null) } }
        queries.clear()
        toSend.clear()
        outgoing?.cancel()
        outgoing?.close()
        outgoing = null
        if (usingStorage) {
            usingStorage = false
            StorageServer.release()
        }
        subscriber?.let {
            Bus.unsubscribe(it)
            ClipboardWatcher.removeUser(context)
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
