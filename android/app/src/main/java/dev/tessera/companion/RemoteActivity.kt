package dev.tessera.companion

import android.os.Bundle
import android.text.Editable
import android.text.TextWatcher
import android.view.KeyEvent
import android.view.inputmethod.EditorInfo
import androidx.activity.enableEdgeToEdge
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.updatePadding
import com.google.android.material.color.DynamicColors
import dev.tessera.companion.databinding.ActivityRemoteBinding
import org.json.JSONObject

/** The phone as a trackpad, keyboard and media remote for the computer. */
class RemoteActivity : AppCompatActivity() {

    private lateinit var binding: ActivityRemoteBinding

    /** What the text box held last, so only what was added is sent. */
    private var lastText = ""

    override fun onCreate(savedInstanceState: Bundle?) {
        DynamicColors.applyToActivityIfAvailable(this)
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        binding = ActivityRemoteBinding.inflate(layoutInflater)
        setContentView(binding.root)
        setSupportActionBar(binding.toolbar)
        supportActionBar?.setDisplayHomeAsUpEnabled(true)
        ViewCompat.setOnApplyWindowInsetsListener(binding.root) { view, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.ime())
            view.updatePadding(left = bars.left, right = bars.right, bottom = bars.bottom)
            insets
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

        binding.typeBox.addTextChangedListener(object : TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) = Unit
            override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) = Unit
            override fun afterTextChanged(s: Editable?) {
                val now = s?.toString().orEmpty()
                when {
                    now.length > lastText.length && now.startsWith(lastText) ->
                        send("text", "text" to now.substring(lastText.length))
                    now.length < lastText.length && lastText.startsWith(now) ->
                        repeat(lastText.length - now.length) { key("backspace") }
                    now != lastText -> send("text", "text" to now)
                }
                lastText = now
            }
        })
        binding.typeBox.setOnEditorActionListener { _, actionId, event ->
            if (actionId == EditorInfo.IME_ACTION_SEND || event?.keyCode == KeyEvent.KEYCODE_ENTER) {
                key("enter")
                binding.typeBox.setText("")
                lastText = ""
                true
            } else false
        }
    }

    override fun onSupportNavigateUp(): Boolean {
        finish()
        return true
    }

    override fun onResume() {
        super.onResume()
        binding.remoteState.setText(
            if (TesseraService.running_instance?.hasDesktop == true) R.string.remote_hint
            else R.string.remote_no_desktop
        )
    }

    private fun key(name: String) = send("key", "name" to name)

    private fun send(kind: String, vararg fields: Pair<String, Any>) {
        val event = JSONObject().put("t", "input").put("k", kind)
        for ((name, value) in fields) event.put(name, value)
        TesseraService.running_instance?.broadcast(event)
    }
}
