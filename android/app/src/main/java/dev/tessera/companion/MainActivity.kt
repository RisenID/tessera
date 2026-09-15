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
import android.text.format.DateUtils
import android.widget.Toast
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import dev.tessera.companion.databinding.ViewComputerRowBinding
import dev.tessera.companion.databinding.ViewSetupRowBinding
import dev.tessera.companion.features.CallsRepository
import dev.tessera.companion.features.ClipboardBridge
import dev.tessera.companion.features.ClipboardWatcher
import dev.tessera.companion.features.DndController
import dev.tessera.companion.features.MediaRepository
import dev.tessera.companion.features.NotificationBridge
import dev.tessera.companion.features.PrivilegedShell
import dev.tessera.companion.features.ProjectionGrant
import dev.tessera.companion.features.SmsRepository
import dev.tessera.companion.features.StorageServer
import dev.tessera.companion.net.TlsServer

/** Setup and status. */
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

        binding.grantAll.setOnClickListener { requestRuntimePermissions(allPermissions()) }
        binding.pairButton.setOnClickListener { showPairingCode() }
        binding.clipboardPull.setOnClickListener { pullClipboard() }
        binding.swipeRefresh.setOnRefreshListener {
            TesseraService.running_instance?.refreshComputerNames()
            refresh()
            // Names arrive a moment later and refresh the list again on their own.
            binding.swipeRefresh.postDelayed({ binding.swipeRefresh.isRefreshing = false }, 600)
        }
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

    /** Stops Android asking before every audio stream. */
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
                grant = { requestRuntimePermissions(messagesPermissions()) },
            ),
            Row(
                binding.rowCalls,
                R.string.perm_calls,
                R.string.perm_calls_why,
                R.drawable.ic_phone_link,
                granted = { CallsRepository.canReadLog(this) && CallsRepository.canControl(this) },
                grant = { requestRuntimePermissions(callsPermissions()) },
            ),
            Row(
                binding.rowPhotos,
                R.string.perm_photos,
                R.string.perm_photos_why,
                R.drawable.ic_photo,
                granted = { MediaRepository.canRead(this) },
                grant = { requestRuntimePermissions(photosPermissions()) },
            ),
            Row(
                binding.rowCamera,
                R.string.perm_camera,
                R.string.perm_camera_why,
                R.drawable.ic_camera,
                granted = {
                    checkSelfPermission(Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED
                },
                grant = { requestRuntimePermissions(listOf(Manifest.permission.CAMERA)) },
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
                // Playback capture needs it; the microphone is never opened.
                grant = { requestRuntimePermissions(listOf(Manifest.permission.RECORD_AUDIO)) },
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

    private fun messagesPermissions() = listOf(
        Manifest.permission.READ_SMS,
        Manifest.permission.SEND_SMS,
        Manifest.permission.READ_CONTACTS,
    )

    private fun callsPermissions() = buildList {
        add(Manifest.permission.READ_CALL_LOG)
        add(Manifest.permission.READ_PHONE_STATE)
        add(Manifest.permission.ANSWER_PHONE_CALLS)
        add(Manifest.permission.CALL_PHONE)
        add(Manifest.permission.READ_CONTACTS)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            add(Manifest.permission.BLUETOOTH_CONNECT)
        }
    }

    private fun photosPermissions() = buildList {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            add(Manifest.permission.READ_MEDIA_IMAGES)
            add(Manifest.permission.READ_MEDIA_VIDEO)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
                add(Manifest.permission.READ_MEDIA_VISUAL_USER_SELECTED)
            }
        } else {
            add(Manifest.permission.READ_EXTERNAL_STORAGE)
        }
    }

    /** Every runtime permission, for the Grant all button. */
    private fun allPermissions() = buildList {
        addAll(messagesPermissions())
        addAll(callsPermissions())
        addAll(photosPermissions())
        add(Manifest.permission.CAMERA)
        add(Manifest.permission.RECORD_AUDIO)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            add(Manifest.permission.POST_NOTIFICATIONS)
        }
    }.distinct()

    private fun missing(permissions: List<String>) =
        permissions.filter { checkSelfPermission(it) != PackageManager.PERMISSION_GRANTED }

    private fun requestRuntimePermissions(permissions: List<String>) {
        val wanted = missing(permissions)

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
        renderComputers()
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

        binding.grantAll.visibility =
            if (missing(allPermissions()).isEmpty()) View.GONE else View.VISIBLE

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

    override fun onStart() {
        super.onStart()
        TesseraService.onSessionsChanged = { runOnUiThread { refresh() } }
        TesseraService.running_instance?.refreshComputerNames()
    }

    override fun onStop() {
        TesseraService.onSessionsChanged = null
        super.onStop()
    }

    private fun renderComputers() {
        val list = binding.computersList
        list.removeAllViews()
        val connected = TesseraService.running_instance?.connectedTokens().orEmpty()
        val computers = store.computers()
        binding.clipboardPull.isEnabled = connected.isNotEmpty()
        if (computers.isEmpty()) {
            val empty = ViewComputerRowBinding.inflate(layoutInflater, list, false)
            empty.computerName.setText(R.string.computers_none)
            empty.computerDetail.visibility = View.GONE
            empty.computerRemove.visibility = View.GONE
            list.addView(empty.root)
            return
        }
        for (computer in computers) {
            val row = ViewComputerRowBinding.inflate(layoutInflater, list, false)
            row.computerName.text = computer.name
            row.computerDetail.text = when {
                computer.token in connected -> getString(R.string.computer_connected)
                computer.lastSeen > 0 -> getString(
                    R.string.computer_last_seen,
                    DateUtils.getRelativeTimeSpanString(computer.lastSeen),
                )
                else -> getString(R.string.computer_never)
            }
            row.computerRemove.setOnClickListener { confirmRemove(computer) }
            list.addView(row.root)
        }
    }

    private fun confirmRemove(computer: Store.Computer) {
        MaterialAlertDialogBuilder(this)
            .setTitle(getString(R.string.computer_remove_title, computer.name))
            .setMessage(R.string.computer_remove_body)
            .setNegativeButton(android.R.string.cancel, null)
            .setPositiveButton(R.string.computer_remove) { _, _ ->
                store.revoke(computer.token)
                TesseraService.running_instance?.disconnect(computer.token)
                refresh()
            }
            .show()
    }

    /** Copies the most recently copied clipboard from the connected computers. */
    private fun pullClipboard() {
        val service = TesseraService.running_instance
        if (service == null) {
            Toast.makeText(this, R.string.clipboard_pull_offline, Toast.LENGTH_SHORT).show()
            return
        }
        binding.clipboardPull.isEnabled = false
        service.latestClipboard { text, from ->
            runOnUiThread {
                binding.clipboardPull.isEnabled = true
                if (text.isNullOrEmpty()) {
                    Toast.makeText(this, R.string.clipboard_pull_none, Toast.LENGTH_SHORT).show()
                    return@runOnUiThread
                }
                // This activity has focus, so a direct write is allowed.
                ClipboardWatcher.note(text)
                if (!ClipboardBridge.writeDirect(this, text)) ClipboardBridge.write(text)
                Toast.makeText(
                    this, getString(R.string.clipboard_pulled, from ?: "your computer"),
                    Toast.LENGTH_SHORT,
                ).show()
            }
        }
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
