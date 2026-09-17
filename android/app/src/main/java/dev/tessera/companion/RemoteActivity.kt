package dev.tessera.companion

import android.net.wifi.WifiManager
import android.os.Bundle
import android.view.KeyEvent
import android.view.View
import android.view.inputmethod.EditorInfo
import androidx.activity.enableEdgeToEdge
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.updatePadding
import com.google.android.material.chip.Chip
import com.google.android.material.color.DynamicColors
import dev.tessera.companion.databinding.ActivityRemoteBinding
import org.json.JSONObject

/** The phone as a trackpad, keyboard and media remote for one computer. */
class RemoteActivity : AppCompatActivity() {

    private lateinit var binding: ActivityRemoteBinding
    private lateinit var store: Store

    /** The token of the computer being driven. */
    private var target = ""

    /** Wi-Fi power saving batches small packets; a trackpad is nothing but. */
    private var wifiLock: WifiManager.WifiLock? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        DynamicColors.applyToActivityIfAvailable(this)
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        store = Store(this)
        binding = ActivityRemoteBinding.inflate(layoutInflater)
        setContentView(binding.root)
        setSupportActionBar(binding.toolbar)
        supportActionBar?.setDisplayHomeAsUpEnabled(true)
        ViewCompat.setOnApplyWindowInsetsListener(binding.root) { view, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.ime())
            view.updatePadding(left = bars.left, right = bars.right, bottom = bars.bottom)
            insets
        }

        binding.targets.setOnCheckedStateChangeListener { group, checked ->
            val chip = checked.firstOrNull()?.let { group.findViewById<Chip>(it) } ?: return@setOnCheckedStateChangeListener
            target = chip.tag as String
            store.remoteTarget = target
        }

        binding.trackpad.listener = object : TrackpadView.Listener {
            override fun onMove(dx: Float, dy: Float) = send("move", "dx" to dx, "dy" to dy)
            override fun onScroll(dx: Float, dy: Float) = send("scroll", "dx" to dx, "dy" to dy)
            override fun onClick(button: String) = send("click", "b" to button)
            override fun onButton(button: String, down: Boolean) = send("button", "b" to button, "down" to down)
        }

        val keys = mapOf(
            binding.keyLeftClick to { send("click", "b" to "left") },
            binding.keyMiddleClick to { send("click", "b" to "middle") },
            binding.keyRightClick to { send("click", "b" to "right") },
            binding.keyEscape to { key("escape") },
            binding.keyTab to { key("tab") },
            binding.keyBackspace to { key("backspace") },
            binding.keyEnter to { key("enter") },
            binding.keyLeft to { key("left") },
            binding.keyRight to { key("right") },
            binding.keyUp to { key("up") },
            binding.keyDown to { key("down") },
            binding.keyPageUp to { key("pageup") },
            binding.keyPageDown to { key("pagedown") },
            binding.keyF5 to { key("f5") },
            binding.mediaPrevious to { key("previous") },
            binding.mediaPlay to { key("play") },
            binding.mediaNext to { key("next") },
            binding.mediaVolumeDown to { key("volumedown") },
            binding.mediaMute to { key("mute") },
            binding.mediaVolumeUp to { key("volumeup") },
        )
        for ((button, action) in keys) button.setOnClickListener { action() }

        // The keyboard's own edits, as they happen; the box only mirrors them.
        binding.typeBox.onText = { text -> send("text", "text" to text) }
        binding.typeBox.onBackspace = { count -> repeat(count) { key("backspace") } }
        binding.typeBox.setOnEditorActionListener { _, actionId, event ->
            if (actionId == EditorInfo.IME_ACTION_SEND || event?.keyCode == KeyEvent.KEYCODE_ENTER) {
                key("enter")
                binding.typeBox.clear()
                true
            } else false
        }
    }

    override fun onSupportNavigateUp(): Boolean {
        finish()
        return true
    }

    override fun onStart() {
        super.onStart()
        TesseraService.onSessionsChanged = { runOnUiThread { renderTargets() } }
        renderTargets()
        // Low-latency mode only applies while the app is in front, which this is.
        wifiLock = getSystemService(WifiManager::class.java)
            ?.createWifiLock(WifiManager.WIFI_MODE_FULL_LOW_LATENCY, "tessera:remote")
            ?.also { runCatching { it.acquire() } }
    }

    override fun onStop() {
        TesseraService.onSessionsChanged = null
        wifiLock?.let { if (it.isHeld) runCatching { it.release() } }
        wifiLock = null
        super.onStop()
    }

    /** One chip per connected computer; hidden when there is only one to drive. */
    private fun renderTargets() {
        val desktops = TesseraService.running_instance?.desktops().orEmpty()
        val group = binding.targets
        group.removeAllViews()
        if (desktops.isEmpty()) {
            target = ""
            binding.remoteState.setText(R.string.remote_no_desktop)
            binding.targetsScroll.visibility = View.GONE
            return
        }
        binding.remoteState.setText(R.string.remote_hint)
        // Keep the current choice, else the remembered one, else the newest connection.
        val tokens = desktops.map { it.first }
        target = listOf(target, store.remoteTarget).firstOrNull { it in tokens } ?: tokens.first()
        binding.targetsScroll.visibility = if (desktops.size > 1) View.VISIBLE else View.GONE
        for ((token, name) in desktops) {
            val chip = layoutInflater.inflate(R.layout.view_target_chip, group, false) as Chip
            chip.id = View.generateViewId()
            chip.text = name
            chip.tag = token
            group.addView(chip)
            if (token == target) group.check(chip.id)
        }
    }

    private fun key(name: String) = send("key", "name" to name)

    private fun send(kind: String, vararg fields: Pair<String, Any>) {
        if (target.isEmpty()) return
        val event = JSONObject().put("t", "input").put("k", kind)
        for ((name, value) in fields) event.put(name, value)
        TesseraService.running_instance?.sendInput(target, event)
    }
}
