package dev.tessera.companion

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log

/**
 * Brings the service back without the user having to open the app.
 *
 * Two cases matter: the phone rebooting, and the app being updated (which stops
 * the service). Before this existed, either one silently took the link down
 * until the app was opened by hand -- the desktop would just sit there
 * reconnecting to a port nothing was listening on.
 */
class BootReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        val action = intent.action ?: return
        if (action !in HANDLED) return

        // Only bother if a desktop has actually been paired; an unpaired phone
        // has nothing to serve and should not run a foreground service.
        if (Store(context).pairedCount == 0) {
            Log.i(TAG, "$action: no paired computers, staying idle")
            return
        }

        Log.i(TAG, "$action: restarting the service")
        runCatching { TesseraService.start(context) }
            .onFailure { Log.w(TAG, "could not restart after $action", it) }
    }

    companion object {
        private const val TAG = "TesseraBoot"
        private val HANDLED = setOf(
            Intent.ACTION_BOOT_COMPLETED,
            Intent.ACTION_LOCKED_BOOT_COMPLETED,
            Intent.ACTION_MY_PACKAGE_REPLACED,
        )
    }
}
