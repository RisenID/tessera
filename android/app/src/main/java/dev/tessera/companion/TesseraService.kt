package dev.tessera.companion

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.IBinder
import android.util.Log
import dev.tessera.companion.features.PrivilegedShell
import dev.tessera.companion.features.ProjectionGrant
import dev.tessera.companion.net.Advertiser
import dev.tessera.companion.net.TlsServer
import dev.tessera.companion.protocol.Session
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.Executors
import kotlin.concurrent.thread

/** Keeps the phone reachable. */
class TesseraService : Service() {

    private lateinit var store: Store
    private val tls = TlsServer()
    private lateinit var advertiser: Advertiser

    private val sessions = CopyOnWriteArrayList<Session>()
    private val workers = Executors.newCachedThreadPool()
    private var acceptThread: Thread? = null

    @Volatile
    private var running = false

    /** The user's answer to the screen-capture dialog, waiting to be spent.
     *  Android treats it as single use, so it is cleared when a projection is
     *  made from it and the next stream asks again. */
    private var audioConsent: Intent? = null
    private var audioConsentCode: Int = 0

    /** What to do once the user has consented, and the session that asked. */
    private var pendingAudio: (() -> Unit)? = null
    private var pendingOwner: Session? = null

    /** Guards the exported consent activity; see [newConsentToken]. */
    @Volatile
    private var consentToken: String? = null

    override fun onCreate() {
        super.onCreate()
        store = Store(this)
        advertiser = Advertiser(this)
        running_instance = this
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            stopSelf()
            return START_NOT_STICKY
        }
        if (!running) startServer()
        // START_STICKY: if the system reclaims us, come back on its own.
        return START_STICKY
    }

    private fun startServer() {
        running = true
        // Always name the types explicitly.
        startForeground(
            NOTIFICATION_ID,
            buildNotification("Waiting for your computer"),
            android.content.pm.ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE,
        )

        acceptThread = thread(name = "tessera-accept") {
            try {
                tls.start(store.port)
                advertiser.start(tls.localPort, store.deviceId, store.displayName)
                updateNotification("Ready on port ${tls.localPort}")

                while (running) {
                    val socket = tls.accept()
                    if (socket == null) {
                        if (!running) break
                        // A failed accept (e.g. a probe reset before it was taken) must not end the listener.
                        if (!tls.listening) {
                            runCatching { tls.stop(); tls.start(store.port) }
                                .onFailure { Log.w(TAG, "could not reopen the listener", it) }
                        }
                        Thread.sleep(if (tls.listening) 50 else 2_000)
                        continue
                    }
                    val session = Session(applicationContext, socket, store)
                    sessions.add(session)
                    workers.execute {
                        try {
                            session.run()
                        } finally {
                            sessions.remove(session)
                            updateNotification(statusText())
                            onSessionsChanged?.invoke()
                        }
                    }
                    updateNotification(statusText())
                }
            } catch (e: Exception) {
                Log.e(TAG, "listener stopped", e)
                if (running) updateNotification("Stopped: ${e.message}")
            }
        }
    }

    /** Holds on each service type beyond connectedDevice (camera, microphone, mediaProjection),
     *  counted per user: two computers on the microphone must not lose it when one stops. */
    private val typeHolds = mutableMapOf<Int, Int>()

    /** Takes or releases one hold on a foreground-service type. */
    @Synchronized
    private fun setTypeActive(type: Int, active: Boolean, what: String): Boolean {
        val before = typeHolds[type] ?: 0
        val after = if (active) before + 1 else (before - 1).coerceAtLeast(0)
        typeHolds[type] = after
        // The platform only needs telling when the type comes or goes.
        if ((before > 0) == (after > 0)) return true
        val types = typeHolds.filterValues { it > 0 }.keys
            .fold(ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE) { acc, t -> acc or t }
        return runCatching {
            startForeground(NOTIFICATION_ID, buildNotification(statusText()), types)
            true
        }.onFailure {
            // Expected when the service was started from the background: such a service is barred
            // from the camera and microphone for its whole lifetime, and only a start made while
            // the app was in the foreground is eligible.
            if (active) typeHolds[type] = before
            Log.w(TAG, "could not change the $what service type", it)
        }.getOrDefault(false)
    }

    // -- one phone, several computers -----------------------------------------

    /** The computers connected on a main link, newest first: (token, name). */
    fun desktops(): List<Pair<String, String>> =
        sessions.reversed().filter { it.isAuthenticated && it.role.isEmpty() }
            .distinctBy { it.token }
            .map { it.token to it.label }

    /** Playback capture is one projection for the whole phone: the newest computer takes it. */
    fun claimAudio(owner: Session) {
        sessions.filter { it !== owner && it.isAuthenticated }
            .forEach { it.audioTakenBy(owner.label) }
    }

    /** The camera can only be open once: a new webcam stream ends the old one. */
    fun claimCamera(owner: Session) {
        sessions.filter { it !== owner && it.isAuthenticated }
            .forEach { it.cameraTakenBy(owner.label) }
    }

    /** Which other computer is streaming the camera, if any. */
    fun cameraHolder(except: Session): String? =
        sessions.firstOrNull { it !== except && it.isAuthenticated && it.usesCamera }?.label

    /** Adds or drops the camera foreground-service type. */
    fun setCameraActive(active: Boolean): Boolean =
        setTypeActive(ServiceInfo.FOREGROUND_SERVICE_TYPE_CAMERA, active, "camera")

    /** Adds or drops the microphone foreground-service type. */
    fun setMicActive(active: Boolean): Boolean =
        setTypeActive(ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE, active, "microphone")

    // -- the phone's audio ---------------------------------------------------

    /**
     * A MediaProjection for [owner] to capture playback with, or null when the
     * user has not consented yet.
     */
    fun audioProjection(owner: Session): MediaProjection? {
        val data = audioConsent ?: return null
        if (!setAudioActive(true)) {
            Log.w(TAG, "could not raise the mediaProjection service type")
            return null
        }
        // Single use by Android's rules: spend it whether or not this works,
        // so a stale token is never offered to the platform twice.
        audioConsent = null
        val manager = getSystemService(MediaProjectionManager::class.java)
        val projection = runCatching {
            manager?.getMediaProjection(audioConsentCode, data)
        }.onFailure { Log.w(TAG, "the projection was refused", it) }.getOrNull()

        if (projection == null) {
            setAudioActive(false)
            return null
        }
        // Android 14 insists on a callback before capture begins, and it is
        // how we hear about the user revoking consent from the status bar --
        // or about another computer's projection replacing this one.
        projection.registerCallback(object : MediaProjection.Callback() {
            override fun onStop() {
                Log.i(TAG, "the projection stopped")
                owner.stopAudioFromSystem()
            }
        }, Handler(Looper.getMainLooper()))
        return projection
    }

    /** Asks the user to allow playback capture, and runs [after] if they do. */
    fun askForAudioConsent(owner: Session, after: () -> Unit): Boolean {
        pendingAudio = after
        pendingOwner = owner

        // The quiet path: with the projection app op granted, Android approves without
        // drawing anything, so the request can be made and answered without the phone being
        // touched -- or even woken.
        if (ProjectionGrant.allowed(this)) {
            val token = newConsentToken()
            val started = PrivilegedShell.run(
                "am start -n $packageName/.ConsentActivity " +
                    "--es ${ConsentActivity.EXTRA_TOKEN} $token"
            ).code == 0
            if (started) {
                Log.i(TAG, "asking for a projection quietly")
                return true
            }
            // No Shizuku any more: try it ourselves before bothering the user.
            val direct = runCatching {
                startActivity(ConsentActivity.intent(this, token))
            }
            if (direct.isSuccess) return true
            Log.i(TAG, "could not start the consent activity; asking the user")
        }

        val manager = getSystemService(NotificationManager::class.java) ?: return false
        ensureConsentChannel()

        val intent = ConsentActivity.intent(this, newConsentToken())
        val tap = PendingIntent.getActivity(
            this,
            2,
            intent,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val notification = Notification.Builder(this, CONSENT_CHANNEL_ID)
            .setContentTitle(getString(R.string.audio_consent_title))
            .setContentText(getString(R.string.audio_consent_text))
            .setSmallIcon(R.drawable.ic_stat_tessera)
            .setContentIntent(tap)
            .setAutoCancel(true)
            .build()
        runCatching { manager.notify(CONSENT_NOTIFICATION_ID, notification) }
        return false
    }

    /** A one-use password for the consent activity. */
    private fun newConsentToken(): String =
        java.util.UUID.randomUUID().toString().also { consentToken = it }

    fun takeConsentToken(offered: String): Boolean {
        val expected = consentToken
        consentToken = null
        return expected != null && offered.isNotEmpty() && offered == expected
    }

    /** Grants the projection app op, so nothing is ever asked again. */
    fun grantProjection(): String? = ProjectionGrant.grant(this)

    /** Whether audio can start without the phone being touched. */
    fun projectionSilent(): Boolean = ProjectionGrant.allowed(this)

    /** Called by the activity with the user's answer. */
    fun onAudioConsent(resultCode: Int, data: Intent?) {
        getSystemService(NotificationManager::class.java)
            ?.cancel(CONSENT_NOTIFICATION_ID)
        if (data == null) {
            pendingAudio = null
            pendingOwner = null
            return
        }
        audioConsent = data
        audioConsentCode = resultCode
        val next = pendingAudio
        pendingAudio = null
        pendingOwner = null
        next?.invoke()
    }

    /** Forget [owner]'s request; another computer's stays. */
    fun clearPendingAudio(owner: Session) {
        if (pendingOwner !== owner) return
        pendingAudio = null
        pendingOwner = null
        getSystemService(NotificationManager::class.java)
            ?.cancel(CONSENT_NOTIFICATION_ID)
    }

    /** Adds or drops the mediaProjection foreground-service type. */
    fun setAudioActive(active: Boolean): Boolean =
        setTypeActive(ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION, active, "mediaProjection")

    private fun ensureConsentChannel() {
        val manager = getSystemService(NotificationManager::class.java) ?: return
        if (manager.getNotificationChannel(CONSENT_CHANNEL_ID) != null) return
        manager.createNotificationChannel(
            NotificationChannel(
                CONSENT_CHANNEL_ID,
                getString(R.string.channel_consent),
                // The desktop is waiting on this one: it has to be seen.
                NotificationManager.IMPORTANCE_HIGH,
            ).apply { description = getString(R.string.channel_consent_description) }
        )
    }

    /** Hand shared files (and text) to the computer with [token], or to every one. */
    fun share(uris: List<android.net.Uri>, text: String = "", token: String = ""): Int {
        // One target per desktop, not per connection: a desktop holds two, and files go down its
        // file connection where it has one, so a large transfer never sits in front of its audio.
        val desktops = sessions.filter { it.isAuthenticated && (token.isEmpty() || it.token == token) }
            .groupBy { it.token }.values
        if (desktops.isEmpty()) return 0
        for (connections in desktops) {
            val main = connections.firstOrNull { it.role.isEmpty() } ?: connections.first()
            if (uris.isNotEmpty()) {
                (connections.firstOrNull { it.role == "files" } ?: main).offerFiles(uris)
            } else if (text.isNotEmpty()) {
                main.offerText(text)
            }
        }
        return desktops.size
    }

    /** Sends a remote-screen event to one computer's main link. */
    fun sendInput(token: String, event: org.json.JSONObject) {
        sessions.lastOrNull { it.isAuthenticated && it.role.isEmpty() && it.token == token }
            ?.sendInput(event)
    }

    /** A computer that reconnects replaces its old connection, which may be dead. */
    fun onAuthenticated(session: Session) {
        sessions.filter {
            it !== session && it.isAuthenticated && it.token == session.token && it.role == session.role
        }.forEach(Session::close)
        updateNotification(statusText())
        onSessionsChanged?.invoke()
    }

    /** Asks every connected computer for its current name. */
    fun refreshComputerNames() {
        sessions.filter { it.isAuthenticated && it.role.isEmpty() }
            .distinctBy { it.token }
            .forEach(Session::refreshName)
    }

    /** Tokens of the computers connected right now. */
    fun connectedTokens(): Set<String> =
        sessions.filter { it.isAuthenticated }.map { it.token }.toSet()

    fun disconnect(token: String) {
        sessions.filter { it.token == token }.forEach(Session::close)
        updateNotification(statusText())
    }

    /**
     * The most recently copied clipboard among the connected computers (and [phone], if given).
     * Calls back once, with null text if nobody had anything.
     */
    fun latestClipboard(
        exclude: Session? = null,
        phone: Pair<Store.Computer, String>? = null,
        callback: (text: String?, from: String?) -> Unit,
    ) {
        val lock = Any()
        var bestText: String? = phone?.second
        var bestAt = phone?.first?.lastSeen ?: -1L
        var bestFrom: String? = phone?.first?.name
        val targets = sessions
            .filter { it !== exclude && it.isAuthenticated && it.role.isEmpty() }
            .distinctBy { it.token }
        val finished = java.util.concurrent.atomic.AtomicBoolean(false)
        fun finish() {
            if (finished.compareAndSet(false, true)) {
                synchronized(lock) { callback(bestText, bestFrom) }
            }
        }
        if (targets.isEmpty()) {
            finish()
            return
        }
        val remaining = java.util.concurrent.atomic.AtomicInteger(targets.size)
        for (session in targets) {
            session.queryClipboard { answer ->
                val text = answer?.optString("text").orEmpty()
                val at = answer?.optLong("copiedAt") ?: 0L
                if (text.isNotEmpty()) {
                    synchronized(lock) {
                        if (bestText == null || at > bestAt) {
                            bestText = text
                            bestAt = at
                            bestFrom = session.computerName.ifBlank { "your computer" }
                        }
                    }
                }
                if (remaining.decrementAndGet() == 0) finish()
            }
        }
        Handler(Looper.getMainLooper()).postDelayed(::finish, CLIPBOARD_WAIT_MS)
    }

    private fun statusText(): String = when (
        val count = sessions.filter { it.isAuthenticated && it.role.isEmpty() }
            .map { it.token }.distinct().size
    ) {
        0 -> "Ready on port ${tls.localPort}"
        1 -> "Connected to 1 computer"
        else -> "Connected to $count computers"
    }

    override fun onDestroy() {
        running_instance = null
        running = false
        advertiser.stop()
        sessions.forEach(Session::close)
        sessions.clear()
        dev.tessera.companion.features.StorageServer.stop()
        tls.stop()
        workers.shutdownNow()
        acceptThread?.interrupt()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    // -- the ongoing notification -------------------------------------------

    private fun buildNotification(text: String): Notification {
        ensureChannel()

        val open = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val stop = PendingIntent.getService(
            this,
            1,
            Intent(this, TesseraService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )

        val builder = Notification.Builder(this, CHANNEL_ID)
            .setContentTitle(getString(R.string.app_name))
            .setContentText(text)
            .setSmallIcon(R.drawable.ic_stat_tessera)
            .setContentIntent(open)
            .addAction(
                Notification.Action.Builder(null, getString(R.string.stop), stop).build()
            )
            .setOngoing(true)

        // Keep it out of the way: this is plumbing, not news. Deferred
        // foreground notifications only exist from API 31.
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            builder.setForegroundServiceBehavior(Notification.FOREGROUND_SERVICE_DEFERRED)
        }
        return builder.build()
    }

    private fun updateNotification(text: String) {
        val manager = getSystemService(NotificationManager::class.java) ?: return
        runCatching { manager.notify(NOTIFICATION_ID, buildNotification(text)) }
    }

    private fun ensureChannel() {
        val manager = getSystemService(NotificationManager::class.java) ?: return
        if (manager.getNotificationChannel(CHANNEL_ID) != null) return
        manager.createNotificationChannel(
            NotificationChannel(
                CHANNEL_ID,
                getString(R.string.channel_status),
                NotificationManager.IMPORTANCE_LOW,
            ).apply {
                description = getString(R.string.channel_status_description)
                setShowBadge(false)
            }
        )
    }

    companion object {
        private const val TAG = "TesseraService"

        /** The live service, so a session can raise the camera type. */
        @Volatile
        var running_instance: TesseraService? = null
            private set

        /** Called when a computer connects or disconnects. */
        @Volatile
        var onSessionsChanged: (() -> Unit)? = null

        private const val CLIPBOARD_WAIT_MS = 2_500L

        private const val CHANNEL_ID = "tessera-status"
        private const val CONSENT_CHANNEL_ID = "tessera-consent"
        private const val NOTIFICATION_ID = 1
        private const val CONSENT_NOTIFICATION_ID = 2
        const val ACTION_STOP = "dev.tessera.companion.STOP"

        fun start(context: android.content.Context) {
            context.startForegroundService(Intent(context, TesseraService::class.java))
        }

        fun stop(context: android.content.Context) {
            context.startService(
                Intent(context, TesseraService::class.java).setAction(ACTION_STOP)
            )
        }
    }
}
