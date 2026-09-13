package dev.tessera.companion

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.net.wifi.WifiManager
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.text.format.Formatter
import android.view.View
import androidx.activity.enableEdgeToEdge
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.updatePadding
import com.google.android.material.color.DynamicColors
import dev.tessera.companion.databinding.ActivityMainBinding
import dev.tessera.companion.databinding.ViewSetupRowBinding
import dev.tessera.companion.features.ClipboardBridge
import dev.tessera.companion.features.DndController
import dev.tessera.companion.features.MediaRepository
import dev.tessera.companion.features.NotificationBridge
import dev.tessera.companion.features.PrivilegedShell
import dev.tessera.companion.features.ProjectionGrant
import dev.tessera.companion.features.SmsRepository
import dev.tessera.companion.features.StorageServer
import dev.tessera.companion.net.TlsServer

/**
 * Setup and status.
 *
 * Deliberately a checklist rather than a dashboard: every capability the
 * desktop offers depends on a permission granted here, and the usual failure is
 * not knowing which one is missing.
 */
class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private lateinit var store: Store

    /** A checklist row bound to the state it reports and the action it offers. */
    private data class Row(
        val binding: ViewSetupRowBinding,
        val title: Int,
        val summary: Int,
        val icon: Int,
        val granted: () -> Boolean,
        val grant: () -> Unit,
    )

    private lateinit var rows: List<Row>

    /** Set while the projection grant is being made, so the row can say so. */
    private var grantingProjection = false

    override fun onCreate(savedInstanceState: Bundle?) {
        // Wallpaper-derived colour on Android 12+; the baseline M3 palette
        // elsewhere. Applied before inflation so the first frame is right.
        DynamicColors.applyToActivityIfAvailable(this)
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)

        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)
        setSupportActionBar(binding.toolbar)
        store = Store(this)

        applyInsets()
        buildRows()

        binding.pairButton.setOnClickListener { showPairingCode() }
        binding.startService.setOnClickListener {
            TesseraService.start(this)
            refresh()
        }
        binding.stopService.setOnClickListener {
            TesseraService.stop(this)
            refresh()
        }
        binding.shizukuButton.setOnClickListener {
            if (PrivilegedShell.available()) {
                PrivilegedShell.requestPermission(SHIZUKU_REQUEST)
            } else {
                binding.shizukuState.setText(R.string.shizuku_missing)
            }
        }

        TesseraService.start(this)
    }

    /**
     * Stops Android asking before every audio stream.
     *
     * The row is not a permission the app needs to work -- it is the
     * difference between the computer being able to play this phone's music on
     * its own and the phone having to be picked up each time.
     */
    private fun grantProjection() {
        if (!PrivilegedShell.hasPermission()) {
            PrivilegedShell.requestPermission(SHIZUKU_REQUEST)
            return
        }
        grantingProjection = true
        refresh()
        Thread {
            val problem = ProjectionGrant.grant(this)
            runOnUiThread {
                grantingProjection = false
                if (problem != null) {
                    binding.shizukuState.text = problem
                }
                refresh()
            }
        }.start()
    }

    /**
     * All files access, through Shizuku where it is running and through the
     * system's own screen where it is not.
     */
    private fun grantStorage() {
        if (!PrivilegedShell.hasPermission()) {
            runCatching {
                startActivity(
                    Intent(
                        Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION,
                        android.net.Uri.parse("package:$packageName"),
                    )
                )
            }.onFailure { open(Settings.ACTION_MANAGE_ALL_FILES_ACCESS_PERMISSION) }
            return
        }
        Thread {
            val problem = StorageServer.grant(this)
            runOnUiThread {
                if (problem != null) binding.shizukuState.text = problem
                refresh()
            }
        }.start()
    }

    /**
     * From API 35 the system always draws edge-to-edge, so the app must inset
     * its own content. The app bar takes the status bar via fitsSystemWindows;
     * the scrolling content only needs the bottom and the horizontal cutout.
     */
    private fun applyInsets() {
        ViewCompat.setOnApplyWindowInsetsListener(binding.scroll) { view, windowInsets ->
            val bars = windowInsets.getInsets(
                WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.displayCutout()
            )
            view.updatePadding(left = bars.left, right = bars.right, bottom = bars.bottom)
            windowInsets
        }
    }

    private fun buildRows() {
        rows = listOf(
            Row(
                binding.rowNotifications,
                R.string.perm_notifications,
                R.string.perm_notifications_why,
                R.drawable.ic_notifications,
                granted = { NotificationBridge.isConnected },
                grant = { open(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS) },
            ),
            Row(
                binding.rowDnd,
                R.string.perm_dnd,
                R.string.perm_dnd_why,
                R.drawable.ic_dnd,
                granted = { DndController.canControl(this) },
                grant = { open(Settings.ACTION_NOTIFICATION_POLICY_ACCESS_SETTINGS) },
            ),
            Row(
                binding.rowMessages,
                R.string.perm_messages,
                R.string.perm_messages_why,
                R.drawable.ic_message,
                granted = { SmsRepository.canRead(this) && SmsRepository.canSend(this) },
                grant = { requestRuntimePermissions() },
            ),
            Row(
                binding.rowPhotos,
                R.string.perm_photos,
                R.string.perm_photos_why,
                R.drawable.ic_photo,
                granted = { MediaRepository.canRead(this) },
                grant = { requestRuntimePermissions() },
            ),
            Row(
                binding.rowCamera,
                R.string.perm_camera,
                R.string.perm_camera_why,
                R.drawable.ic_camera,
                granted = {
                    checkSelfPermission(Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED
                },
                grant = { requestRuntimePermissions() },
            ),
            Row(
                binding.rowAudio,
                R.string.perm_audio,
                R.string.perm_audio_why,
                R.drawable.ic_audio,
                granted = {
                    checkSelfPermission(Manifest.permission.RECORD_AUDIO) ==
                        PackageManager.PERMISSION_GRANTED
                },
                grant = { requestRuntimePermissions() },
            ),
            Row(
                binding.rowProjection,
                R.string.perm_projection,
                R.string.perm_projection_why,
                R.drawable.ic_audio,
                granted = { ProjectionGrant.allowed(this) },
                grant = { grantProjection() },
            ),
            Row(
                binding.rowStorage,
                R.string.perm_storage,
                R.string.perm_storage_why,
                R.drawable.ic_photo,
                // Nothing to grant on a phone too old to have the permission.
                granted = { !StorageServer.supported() || StorageServer.allowed() },
                grant = { grantStorage() },
            ),
            Row(
                binding.rowClipboard,
                R.string.perm_clipboard,
                R.string.perm_clipboard_why,
                R.drawable.ic_clipboard,
                granted = { ClipboardBridge.available() },
                grant = { open(Settings.ACTION_ACCESSIBILITY_SETTINGS) },
            ),
        )

        for (row in rows) {
            row.binding.rowTitle.setText(row.title)
            row.binding.rowSummary.setText(row.summary)
            row.binding.rowIcon.setImageResource(row.icon)
            row.binding.rowAction.setOnClickListener { row.grant() }
        }
    }

    private fun open(action: String) {
        runCatching { startActivity(Intent(action)) }
    }

    override fun onResume() {
        super.onResume()
        refresh()
    }

    private fun requestRuntimePermissions() {
        val wanted = buildList {
            add(Manifest.permission.READ_SMS)
            add(Manifest.permission.SEND_SMS)
            add(Manifest.permission.READ_CONTACTS)
            add(Manifest.permission.CAMERA)
            // Playback capture needs it; the microphone is never opened.
            add(Manifest.permission.RECORD_AUDIO)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                add(Manifest.permission.READ_MEDIA_IMAGES)
                add(Manifest.permission.READ_MEDIA_VIDEO)
                add(Manifest.permission.POST_NOTIFICATIONS)
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
                    add(Manifest.permission.READ_MEDIA_VISUAL_USER_SELECTED)
                }
            } else {
                add(Manifest.permission.READ_EXTERNAL_STORAGE)
            }
        }.filter { checkSelfPermission(it) != PackageManager.PERMISSION_GRANTED }

        if (wanted.isEmpty()) {
            refresh()
            return
        }
        requestPermissions(wanted.toTypedArray(), RUNTIME_REQUEST)
    }

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<out String>,
        grantResults: IntArray,
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        refresh()
    }

    private fun showPairingCode() {
        val code = Pairing.issue()
        binding.pairingCode.text = code.chunked(3).joinToString(" ")
        binding.pairingCode.visibility = View.VISIBLE
        binding.pairingHint.visibility = View.VISIBLE
        binding.pairingHint.text = getString(R.string.pairing_hint, address())
    }

    private fun address(): String {
        val wifi = applicationContext.getSystemService(WifiManager::class.java)
        @Suppress("DEPRECATION")
        val raw = wifi?.connectionInfo?.ipAddress ?: 0
        @Suppress("DEPRECATION")
        val ip = if (raw != 0) Formatter.formatIpAddress(raw) else getString(R.string.unknown_ip)
        return "$ip:${store.port}"
    }

    private fun refresh() {
        binding.deviceName.text = store.displayName
        binding.pairedCount.text = resources.getQuantityString(
            R.plurals.paired_computers, store.pairedCount, store.pairedCount
        )
        binding.addressText.text = address()

        // Grouped in fours, the way a fingerprint is normally read aloud.
        binding.fingerprintText.text = TlsServer().certificateFingerprint()
            .chunked(4).joinToString(" ")

        var outstanding = 0
        for (row in rows) {
            val granted = row.granted()
            if (!granted) outstanding++
            row.binding.rowAction.visibility = if (granted) View.GONE else View.VISIBLE
            row.binding.rowIcon.setImageResource(if (granted) R.drawable.ic_check else row.icon)
            row.binding.rowIcon.setBackgroundColor(
                colorAttr(
                    if (granted) com.google.android.material.R.attr.colorPrimaryContainer
                    else com.google.android.material.R.attr.colorSecondaryContainer
                )
            )
        }

        // The hero reports the thing that actually blocks the user.
        binding.statusText.text = when {
            outstanding > 0 -> resources.getQuantityString(
                R.plurals.setup_remaining, outstanding, outstanding
            )
            store.pairedCount == 0 -> getString(R.string.status_running)
            else -> getString(R.string.status_paired)
        }
        binding.statusIcon.setImageResource(
            if (outstanding == 0) R.drawable.ic_check else R.drawable.ic_phone_link
        )

        binding.shizukuState.setText(
            when {
                PrivilegedShell.hasPermission() -> R.string.shizuku_ready
                PrivilegedShell.available() -> R.string.shizuku_needs_permission
                else -> R.string.shizuku_missing
            }
        )
        binding.shizukuButton.visibility =
            if (PrivilegedShell.hasPermission()) View.GONE else View.VISIBLE
    }

    private fun colorAttr(attr: Int): Int {
        val typed = android.util.TypedValue()
        theme.resolveAttribute(attr, typed, true)
        return typed.data
    }

    companion object {
        private const val RUNTIME_REQUEST = 100
        private const val SHIZUKU_REQUEST = 101
    }
}
