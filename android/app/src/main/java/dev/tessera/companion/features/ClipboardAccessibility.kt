package dev.tessera.companion.features

import android.accessibilityservice.AccessibilityService
import android.content.ClipData
import android.content.ClipboardManager
import android.graphics.PixelFormat
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.view.accessibility.AccessibilityEvent
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlin.concurrent.thread

/** Clipboard sharing that survives a reboot, for phones without Shizuku. */
class ClipboardAccessibility : AccessibilityService() {

    private val main = Handler(Looper.getMainLooper())
    private var pending = false

    override fun onServiceConnected() {
        super.onServiceConnected()
        instance = this
        Log.i(TAG, "clipboard accessibility route connected")
    }

    override fun onDestroy() {
        if (instance === this) instance = null
        super.onDestroy()
    }

    override fun onInterrupt() = Unit

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        if (event == null || !ClipboardWatcher.watching) return
        // Shizuku reads without stealing focus; there is nothing to add.
        if (ClipboardBridge.viaShizuku()) return
        if (!looksLikeCopy(event)) return
        if (pending) return
        pending = true
        // Let the copy land first: the tap arrives before the app acts on it.
        main.postDelayed({
            pending = false
            thread(name = "tessera-clip-read") { ClipboardWatcher.checkNow() }
        }, SETTLE_MS)
    }

    private fun looksLikeCopy(event: AccessibilityEvent): Boolean {
        val source = event.packageName?.toString().orEmpty()
        if (source == packageName) return false
        return when (event.eventType) {
            AccessibilityEvent.TYPE_VIEW_CLICKED -> said(event) { text ->
                text == copyWord || text.startsWith("$copyWord ") || text == "copy"
            }
            // The "copied" toast most apps and One UI show.
            AccessibilityEvent.TYPE_NOTIFICATION_STATE_CHANGED -> said(event) { text ->
                "copied" in text || "clipboard" in text || copiedWord.isNotEmpty() && copiedWord in text
            }
            // Android 13's clipboard overlay, and Samsung's clipboard edge.
            AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED ->
                "clipboard" in (event.className?.toString().orEmpty() + source).lowercase()
            else -> false
        }
    }

    private inline fun said(event: AccessibilityEvent, test: (String) -> Boolean): Boolean {
        val words = event.text.map { it.toString() } + listOfNotNull(event.contentDescription?.toString())
        return words.any { test(it.trim().lowercase()) }
    }

    /** "Copy" in the phone's own language. */
    private val copyWord by lazy { runCatching { getString(android.R.string.copy) }.getOrDefault("Copy").lowercase() }

    /** Where the platform has a localised "copied" string, that too. */
    private val copiedWord by lazy {
        val id = resources.getIdentifier("text_copied", "string", "android")
        if (id == 0) "" else runCatching { getString(id).lowercase() }.getOrDefault("")
    }

    // -- typing into whatever has focus ---------------------------------------

    /** Appends [text] to the focused text field. False when nothing has focus. */
    fun typeText(text: String): Boolean {
        val node = focusedEditable() ?: return false
        val current = node.text?.toString().orEmpty()
        val arguments = android.os.Bundle().apply {
            putCharSequence(
                android.view.accessibility.AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE,
                current + text,
            )
        }
        return node.performAction(android.view.accessibility.AccessibilityNodeInfo.ACTION_SET_TEXT, arguments)
    }

    /** The keyboard's Enter, on the focused field. */
    fun pressEnter(): Boolean {
        val node = focusedEditable() ?: return false
        return node.performAction(android.view.accessibility.AccessibilityNodeInfo.ACTION_IME_ENTER)
    }

    private fun focusedEditable(): android.view.accessibility.AccessibilityNodeInfo? {
        val root = rootInActiveWindow ?: return null
        val focused = root.findFocus(android.view.accessibility.AccessibilityNodeInfo.FOCUS_INPUT)
            ?: return null
        return if (focused.isEditable) focused else null
    }

    // -- reading and writing through a focused window ------------------------

    /** Blocks for at most a second. Never call on the main thread. */
    fun readClipboard(): String? = withFocus { manager ->
        manager.primaryClip?.let(ClipboardCalls::textOf)
    }

    fun writeClipboard(text: String): Boolean = withFocus { manager ->
        manager.setPrimaryClip(ClipData.newPlainText("Tessera", text))
        true
    } ?: false

    private fun <T> withFocus(action: (ClipboardManager) -> T?): T? {
        check(Looper.myLooper() != Looper.getMainLooper()) { "would wait on itself" }
        val manager = getSystemService(ClipboardManager::class.java) ?: return null
        val windows = getSystemService(WindowManager::class.java) ?: return null
        val done = CountDownLatch(1)
        var result: T? = null

        main.post {
            val view = object : View(this) {
                override fun onWindowFocusChanged(hasWindowFocus: Boolean) {
                    super.onWindowFocusChanged(hasWindowFocus)
                    if (!hasWindowFocus || done.count == 0L) return
                    result = runCatching { action(manager) }
                        .onFailure { Log.w(TAG, "clipboard call failed", it) }.getOrNull()
                    runCatching { windows.removeView(this) }
                    done.countDown()
                }
            }
            val params = WindowManager.LayoutParams(
                1, 1,
                WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
                WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL or
                    WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE or
                    WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
                PixelFormat.TRANSLUCENT,
            ).apply {
                gravity = Gravity.TOP or Gravity.START
                alpha = 0f
            }
            runCatching { windows.addView(view, params) }.onFailure {
                Log.w(TAG, "could not add the focus window", it)
                done.countDown()
            }
            // Never leave a window behind, whatever focus did.
            main.postDelayed({
                if (view.isAttachedToWindow) runCatching { windows.removeView(view) }
                done.countDown()
            }, FOCUS_TIMEOUT_MS)
        }
        done.await(FOCUS_TIMEOUT_MS + 200, TimeUnit.MILLISECONDS)
        return result
    }

    companion object {
        private const val TAG = "TesseraClipboard"
        private const val SETTLE_MS = 250L
        private const val FOCUS_TIMEOUT_MS = 800L

        @Volatile
        var instance: ClipboardAccessibility? = null
            private set

        val running: Boolean
            get() = instance != null
    }
}
