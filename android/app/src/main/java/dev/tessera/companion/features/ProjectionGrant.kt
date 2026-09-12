package dev.tessera.companion.features

import android.app.AppOpsManager
import android.content.Context
import android.os.Process
import android.util.Log

/**
 * Making Android stop asking before it will send this phone's audio.
 *
 * Playback capture is gated behind a MediaProjection, and Android 14 made that
 * consent single-use: every stream would mean a dialog on the phone. For a
 * feature whose whole point is that the phone can stay in a pocket, asking the
 * user to pick it up each time is not a feature at all.
 *
 * There is one supported way out, and it is the one the shell already uses: the
 * `PROJECT_MEDIA` app operation. An app whose op is set to *allow* is handed a
 * projection without a dialog. Setting it needs shell privileges, which this
 * app already borrows through Shizuku for the hotspot -- so it is a single
 * grant, made once, surviving reboots, and revocable from the same screen.
 *
 * Nothing here weakens anything silently: the grant is an explicit action in
 * the app's own checklist, and the phone still shows its screen-capture
 * indicator whenever audio is actually being captured.
 */
object ProjectionGrant {

    private const val TAG = "TesseraProjection"

    /** The op's public string name. `AppOpsManager.OPSTR_PROJECT_MEDIA` is
     *  hidden from the SDK, but the value is stable and part of the platform's
     *  own naming scheme. */
    const val OP = "android:project_media"

    /** The name `appops` itself takes on the command line. */
    private const val OP_SHELL = "PROJECT_MEDIA"

    /** Whether Android will hand us a projection without asking. */
    fun allowed(context: Context): Boolean = runCatching {
        val manager = context.getSystemService(AppOpsManager::class.java)
            ?: return false
        val mode = manager.unsafeCheckOpNoThrow(
            OP, Process.myUid(), context.packageName
        )
        mode == AppOpsManager.MODE_ALLOWED
    }.getOrElse {
        Log.w(TAG, "could not read the projection app op", it)
        false
    }

    /** Whether the grant can be made at all, which is to say: is Shizuku up. */
    fun grantable(): Boolean = PrivilegedShell.hasPermission()

    /**
     * Stops Android asking. Returns null on success, or why it could not.
     */
    fun grant(context: Context): String? {
        if (allowed(context)) return null
        if (!grantable()) {
            return "Shizuku is not running on the phone, so the permission " +
                "cannot be granted from here. Start Shizuku and try again, or " +
                "allow the dialog each time."
        }
        val result = PrivilegedShell.run(
            "appops set ${context.packageName} $OP_SHELL allow"
        )
        if (result.code != 0) {
            return "The phone refused the permission: ${result.text.take(160)}"
        }
        return if (allowed(context)) null else
            "The permission was set but Android still reports it as denied."
    }

    /** Puts it back the way it was: Android asks again, every time. */
    fun revoke(context: Context): String? {
        if (!grantable()) return "Shizuku is not running on the phone."
        val result = PrivilegedShell.run(
            "appops set ${context.packageName} $OP_SHELL default"
        )
        return if (result.code == 0) null else result.text.take(160)
    }
}
