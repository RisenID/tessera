package dev.risenid.tessera

import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.util.Log
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import com.google.android.material.dialog.MaterialAlertDialogBuilder

/** "Share to Tessera" from anywhere on the phone. */
class ShareActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        handle(intent)
    }

    private fun handle(intent: Intent?) {
        if (intent == null) return finish()

        val uris: List<Uri> = when (intent.action) {
            Intent.ACTION_SEND -> listOfNotNull(stream(intent))
            Intent.ACTION_SEND_MULTIPLE ->
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                    intent.getParcelableArrayListExtra(Intent.EXTRA_STREAM, Uri::class.java)
                } else {
                    @Suppress("DEPRECATION")
                    intent.getParcelableArrayListExtra<Uri>(Intent.EXTRA_STREAM)
                } ?: emptyList()
            else -> emptyList()
        }
        val text = intent.getStringExtra(Intent.EXTRA_TEXT).orEmpty()

        // Read permission on a shared Uri belongs to this task and dies with it, so the service is
        // handed the Uris while that grant still holds and reads them immediately.
        uris.forEach { uri ->
            runCatching {
                grantUriPermission(packageName, uri, Intent.FLAG_GRANT_READ_URI_PERMISSION)
            }
        }

        val service = TesseraService.running_instance
        if (service == null) {
            toast(getString(R.string.share_not_running))
            return finish()
        }
        val desktops = service.desktops()
        when (desktops.size) {
            0 -> {
                toast(getString(R.string.share_no_desktop))
                finish()
            }
            1 -> {
                send(service, uris, text, "")
                finish()
            }
            // Two computers on: ask, rather than opening a link on both.
            else -> choose(service, uris, text, desktops)
        }
    }

    private fun choose(
        service: TesseraService,
        uris: List<Uri>,
        text: String,
        desktops: List<Pair<String, String>>,
    ) {
        val names = desktops.map { it.second } + getString(R.string.share_all_computers)
        MaterialAlertDialogBuilder(this)
            .setTitle(R.string.share_choose_title)
            .setItems(names.toTypedArray()) { _, which ->
                val token = desktops.getOrNull(which)?.first.orEmpty()
                send(service, uris, text, token, desktops.getOrNull(which)?.second)
            }
            .setNegativeButton(android.R.string.cancel, null)
            .setOnDismissListener { finish() }
            .show()
    }

    private fun send(service: TesseraService, uris: List<Uri>, text: String, token: String, name: String? = null) {
        val sent = service.share(uris, text, token)
        toast(
            when {
                sent == 0 -> getString(R.string.share_no_desktop)
                name != null -> getString(R.string.share_sent_to, name)
                uris.isEmpty() -> getString(R.string.share_text_sent)
                uris.size == 1 -> getString(R.string.share_one_sent)
                else -> getString(R.string.share_many_sent, uris.size)
            }
        )
    }

    private fun stream(intent: Intent): Uri? =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            intent.getParcelableExtra(Intent.EXTRA_STREAM, Uri::class.java)
        } else {
            @Suppress("DEPRECATION")
            intent.getParcelableExtra(Intent.EXTRA_STREAM)
        }

    private fun toast(message: String) {
        Log.i(TAG, message)
        Toast.makeText(this, message, Toast.LENGTH_SHORT).show()
    }

    private companion object {
        const val TAG = "TesseraShare"
    }
}
