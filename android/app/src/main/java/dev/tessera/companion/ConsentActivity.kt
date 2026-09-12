package dev.tessera.companion

import android.content.Intent
import android.media.projection.MediaProjectionManager
import android.os.Bundle
import android.util.Log
import androidx.activity.ComponentActivity
import androidx.activity.result.contract.ActivityResultContracts

/**
 * Asks Android for a MediaProjection, and gets out of the way.
 *
 * It exists because only an activity may raise that request -- a service
 * cannot -- and this app's reason for wanting one is audio, which the user
 * asked for from their computer and should not have to come back to the phone
 * to confirm.
 *
 * So this activity has no interface at all. Where the projection app op has
 * been granted (see [dev.tessera.companion.features.ProjectionGrant]) the
 * system approves without drawing anything and the activity finishes in the
 * same breath, leaving the phone exactly as it was -- locked screen included,
 * which is why it is allowed to show over the keyguard without waking it.
 *
 * Where the op has not been granted, this is the ordinary path: the system's
 * dialog appears, the user answers, and the result goes to the service either
 * way.
 */
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

        // Exported, because the shell has to be able to start it -- a service
        // may not start an activity itself. So it is guarded instead: only a
        // request carrying the token this app just generated is answered, and
        // each token works once. Without this, any app on the phone could ask
        // us to raise a capture request.
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
