package dev.tessera.companion.features

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.os.IBinder
import android.util.Log
import org.lsposed.hiddenapibypass.HiddenApiBypass
import rikka.shizuku.ShizukuBinderWrapper
import rikka.shizuku.SystemServiceHelper
import java.lang.reflect.Method

/** Reads and writes the phone's clipboard from inside the app. */
object ClipboardBridge {

    private const val TAG = "TesseraClipboard"

    /** Calls must name the package that owns the calling uid, which is shell. */
    private const val CALLER = "com.android.shell"

    private val BYPASS = ClipboardCalls.Lookup { service ->
        runCatching {
            HiddenApiBypass.getDeclaredMethods(service.javaClass).filterIsInstance<Method>()
        }.getOrElse { service.javaClass.methods.toList() }
    }

    fun viaShizuku(): Boolean = PrivilegedShell.hasPermission()

    fun available(): Boolean = viaShizuku() || ClipboardAccessibility.running

    /** Which route is in use, for the desktop to explain. */
    fun route(): String = when {
        viaShizuku() -> "shizuku"
        ClipboardAccessibility.running -> "accessibility"
        else -> ""
    }

    /** The clipboard's current text, or null when empty or unreadable. */
    fun read(): String? {
        if (!viaShizuku()) return ClipboardAccessibility.instance?.readClipboard()
        val service = clipboardService() ?: return null
        return runCatching { ClipboardCalls.read(service, CALLER, BYPASS) }
            .onFailure { Log.w(TAG, "could not read the clipboard", it) }.getOrNull()
    }

    /** Replaces the clipboard contents. Returns true when it was accepted. */
    fun write(text: String): Boolean {
        if (!viaShizuku()) return ClipboardAccessibility.instance?.writeClipboard(text) ?: false
        val service = clipboardService() ?: return false
        return runCatching { ClipboardCalls.write(service, CALLER, text, BYPASS) }
            .onFailure { Log.w(TAG, "could not write the clipboard", it) }.getOrDefault(false)
    }

    private fun clipboardService(): Any? = runCatching {
        val binder: IBinder =
            ShizukuBinderWrapper(SystemServiceHelper.getSystemService(Context.CLIPBOARD_SERVICE))
        ClipboardCalls.asInterface(binder)
    }.onFailure { Log.w(TAG, "clipboard service unavailable", it) }.getOrNull()

    /**
     * Best-effort write without Shizuku, for when the app happens to be in the
     * foreground. Kept because it costs nothing and covers the case where the
     * user is looking at Tessera when the desktop copies something.
     */
    fun writeDirect(context: Context, text: String): Boolean = runCatching {
        val manager = context.getSystemService(ClipboardManager::class.java) ?: return false
        manager.setPrimaryClip(ClipData.newPlainText("Tessera", text))
        true
    }.getOrDefault(false)
}
