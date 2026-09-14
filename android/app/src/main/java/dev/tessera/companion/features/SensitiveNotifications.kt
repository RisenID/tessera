package dev.tessera.companion.features

import android.app.AppOpsManager
import android.content.Context
import android.os.Build
import android.os.Process
import android.util.Log

/** Android 15 hides one-time codes from notification listeners without this app op. */
object SensitiveNotifications {

    private const val TAG = "TesseraSensitive"
    private const val OP = "android:receive_sensitive_notifications"
    private const val OP_SHELL = "RECEIVE_SENSITIVE_NOTIFICATIONS"

    @Volatile
    private var granted = false

    fun allowed(context: Context): Boolean = runCatching {
        val manager = context.getSystemService(AppOpsManager::class.java) ?: return false
        manager.unsafeCheckOpNoThrow(OP, Process.myUid(), context.packageName) == AppOpsManager.MODE_ALLOWED
    }.getOrDefault(false)

    /** Grants it through Shizuku where it can. Blocks; not on the main thread. */
    fun ensure(context: Context) {
        if (granted || Build.VERSION.SDK_INT < 35) return
        if (allowed(context)) {
            granted = true
            return
        }
        if (!PrivilegedShell.hasPermission()) return
        val result = PrivilegedShell.run("appops set ${context.packageName} $OP_SHELL allow")
        granted = allowed(context) || result.code == 0
        if (!granted) Log.w(TAG, "could not allow sensitive notifications: ${result.text.take(160)}")
    }
}
