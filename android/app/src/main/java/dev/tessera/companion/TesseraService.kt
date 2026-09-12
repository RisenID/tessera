package dev.tessera.companion

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.os.Build
import android.os.IBinder
import android.util.Log
import dev.tessera.companion.net.Advertiser
import dev.tessera.companion.net.TlsServer
import dev.tessera.companion.protocol.Session
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.Executors
import kotlin.concurrent.thread

/**
 * Keeps the phone reachable.
 *
 * A foreground service is required to hold a listening socket reliably, but the
 * cost is small: it waits on accept() and does nothing at all until a desktop
 * connects or the notification listener publishes an event.
 */
class TesseraService : Service() {

    private lateinit var store: Store
    private val tls = TlsServer()
    private lateinit var advertiser: Advertiser

    private val sessions = CopyOnWriteArrayList<Session>()
    private val workers = Executors.newCachedThreadPool()
    private var acceptThread: Thread? = null

    @Volatile
    private var running = false

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
        // Always name the types explicitly. With no type argument the platform
        // applies every type declared in the manifest -- including camera --
        // and a service started from the background is not allowed camera
        // access, so the start dies with a SecurityException.
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
                    val socket = tls.accept() ?: break
                    val session = Session(applicationContext, socket, store)
                    sessions.add(session)
                    workers.execute {
                        try {
                            session.run()
                        } finally {
                            sessions.remove(session)
                            updateNotification(statusText())
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

    /**
     * Adds or drops the camera foreground-service type.
     *
     * The service normally runs as connectedDevice only. Android refuses camera
     * access to a background app, so while a webcam stream is live the service
     * must also be a camera foreground service -- and must stop being one as
     * soon as the stream ends, so the phone is not left in a state that implies
     * the camera is in use.
     */
    fun setCameraActive(active: Boolean): Boolean {
        val types = if (active) {
            android.content.pm.ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE or
                android.content.pm.ServiceInfo.FOREGROUND_SERVICE_TYPE_CAMERA
        } else {
            android.content.pm.ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE
        }
        return runCatching {
            startForeground(NOTIFICATION_ID, buildNotification(statusText()), types)
            true
        }.onFailure {
            // Expected when the service was started from the background: such a
            // service is barred from camera access for its whole lifetime, and
            // only a start made while the app was in the foreground is eligible.
            Log.w(TAG, "could not raise the camera service type", it)
        }.getOrDefault(false)
    }

    private fun statusText(): String = when (val count = sessions.size) {
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

        private const val CHANNEL_ID = "tessera-status"
        private const val NOTIFICATION_ID = 1
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
