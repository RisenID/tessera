package dev.tessera.companion

import android.app.Activity
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.util.Log
import android.widget.Toast

/** "Share to Tessera" from anywhere on the phone. */
class ShareActivity : Activity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        handle(intent)
        finish()
    }

    private fun handle(intent: Intent?) {
        if (intent == null) return

        val uris: List<Uri> = when (intent.action) {
            Intent.ACTION_SEND -> listOfNotNull(stream(intent))
            Intent.ACTION_SEND_MULTIPLE ->
                intent.getParcelableArrayListExtra(Intent.EXTRA_STREAM, Uri::class.java)
                    ?: emptyList()
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
            return
        }

        val sent = service.share(uris, text)
        when {
            sent == 0 -> toast(getString(R.string.share_no_desktop))
            uris.isEmpty() -> toast(getString(R.string.share_text_sent))
            uris.size == 1 -> toast(getString(R.string.share_one_sent))
            else -> toast(getString(R.string.share_many_sent, uris.size))
        }
    }

    private fun stream(intent: Intent): Uri? =
        intent.getParcelableExtra(Intent.EXTRA_STREAM, Uri::class.java)

    private fun toast(message: String) {
        Log.i(TAG, message)
        Toast.makeText(this, message, Toast.LENGTH_SHORT).show()
    }

    private companion object {
        const val TAG = "TesseraShare"
    }
}
