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

/**
 * Reads and writes the phone's clipboard.
 *
 * From Android 10 an app may only touch the clipboard while it has focus or is
 * the active input method, so a background companion cannot use
 * ClipboardManager directly -- reads return nothing and writes are dropped.
 *
 * The clipboard service itself will serve the shell user, so the calls go
 * through Shizuku like the tethering ones do. The IClipboard signatures have
 * gained parameters over successive releases (attribution tag in 11, device id
 * in 14), so rather than hardcoding one shape, the right overload is chosen by
 * inspecting the parameter types.
 */
object ClipboardBridge {

    private const val TAG = "TesseraClipboard"

    /** Calls must name the package that owns the calling uid, which is shell. */
    private const val CALLER = "com.android.shell"

    fun available(): Boolean = PrivilegedShell.hasPermission()

    /** The clipboard's current text, or null when empty or unreadable. */
    fun read(): String? {
        val service = clipboardService() ?: return null
        return runCatching {
            val method = pick(service, "getPrimaryClip") ?: return null
            val clip = method.invoke(service, *argumentsFor(method)) as? ClipData ?: return null
            textOf(clip)
        }.onFailure { Log.w(TAG, "could not read the clipboard", it) }.getOrNull()
    }

    /** Replaces the clipboard contents. Returns true when it was accepted. */
    fun write(text: String): Boolean {
        val service = clipboardService() ?: return false
        return runCatching {
            val method = pick(service, "setPrimaryClip") ?: return false
            val clip = ClipData.newPlainText("Tessera", text)
            // The ClipData is the first argument; the rest follow the same
            // package/tag/user/device pattern as the getter.
            method.invoke(service, clip, *argumentsFor(method, skip = 1))
            true
        }.onFailure { Log.w(TAG, "could not write the clipboard", it) }.getOrDefault(false)
    }

    // -- plumbing ------------------------------------------------------------

    private fun clipboardService(): Any? = runCatching {
        val binder: IBinder =
            ShizukuBinderWrapper(SystemServiceHelper.getSystemService(Context.CLIPBOARD_SERVICE))
        Class.forName("android.content.IClipboard\$Stub")
            .getMethod("asInterface", IBinder::class.java)
            .invoke(null, binder)
    }.onFailure { Log.w(TAG, "clipboard service unavailable", it) }.getOrNull()

    /** The overload with the fewest parameters we know how to fill. */
    private fun pick(service: Any, name: String): Method? =
        runCatching {
            HiddenApiBypass.getDeclaredMethods(service.javaClass)
                .filterIsInstance<Method>()
                .filter { it.name == name }
                .filter { method -> method.parameterTypes.all(::fillable) }
                .minByOrNull { it.parameterTypes.size }
        }.getOrElse {
            service.javaClass.methods
                .filter { it.name == name && it.parameterTypes.all(::fillable) }
                .minByOrNull { it.parameterTypes.size }
        }

    private fun fillable(type: Class<*>): Boolean =
        type == String::class.java ||
            type == Int::class.javaPrimitiveType ||
            type == ClipData::class.java

    /**
     * Builds arguments positionally: strings are the caller package then the
     * attribution tag, and the integers are the user id then the device id.
     */
    private fun argumentsFor(method: Method, skip: Int = 0): Array<Any?> {
        var stringsSeen = 0
        var intsSeen = 0
        return method.parameterTypes.drop(skip).map { type ->
            when {
                type == String::class.java -> {
                    stringsSeen++
                    if (stringsSeen == 1) CALLER else null   // package, then attribution tag
                }
                type == Int::class.javaPrimitiveType -> {
                    intsSeen++
                    0                                        // user 0, default device
                }
                else -> null
            }
        }.toTypedArray()
    }

    private fun textOf(clip: ClipData): String? {
        if (clip.itemCount == 0) return null
        val builder = StringBuilder()
        for (index in 0 until clip.itemCount) {
            clip.getItemAt(index).text?.let { builder.append(it) }
        }
        return builder.toString().takeIf { it.isNotEmpty() }
    }

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
