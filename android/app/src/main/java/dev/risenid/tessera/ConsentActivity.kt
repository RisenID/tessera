package dev.risenid.tessera

import android.content.Intent
import android.media.projection.MediaProjectionManager
import android.os.Bundle
import android.util.Log
import androidx.activity.ComponentActivity
import androidx.activity.result.contract.ActivityResultContracts

/** Asks Android for a MediaProjection, and gets out of the way. */
class ConsentActivity : ComponentActivity() {

    private val request = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { result ->
        val granted = result.resultCode == RESULT_OK && result.data != null
        Log.i(TAG, if (granted) "projection granted" else "projection refused")
        TesseraService.running_instance?.onAudioConsent(
            result.resultCode,
            if (granted) result.data else null,
        )
        finish()
        // No animation: this was never meant to look like an app opening.
        overridePendingTransition(0, 0)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // Over the lock screen, without lighting it up: a silent grant should
        // not wake a phone lying face down on a desk.
        setShowWhenLocked(true)

        // Exported, because the shell has to be able to start it -- a service may not start
        // an activity itself.
        val token = intent?.getStringExtra(EXTRA_TOKEN).orEmpty()
        if (TesseraService.running_instance?.takeConsentToken(token) != true) {
            Log.w(TAG, "consent request without a valid token; ignoring")
            finish()
            return
        }

        val manager = getSystemService(MediaProjectionManager::class.java)
        if (manager == null) {
            TesseraService.running_instance?.onAudioConsent(RESULT_CANCELED, null)
            finish()
            return
        }
        runCatching { request.launch(manager.createScreenCaptureIntent()) }
            .onFailure { error ->
                Log.w(TAG, "could not ask for a projection", error)
                TesseraService.running_instance?.onAudioConsent(RESULT_CANCELED, null)
                finish()
            }
    }

    companion object {
        private const val TAG = "TesseraConsent"

        const val EXTRA_TOKEN = "token"

        /** How the service starts this, directly or through the shell. */
        fun intent(context: android.content.Context, token: String): Intent =
            Intent(context, ConsentActivity::class.java)
                .putExtra(EXTRA_TOKEN, token)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_NO_ANIMATION)
    }
}
