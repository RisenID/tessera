package dev.tessera.companion

import android.content.Context
import android.util.AttributeSet
import android.view.KeyEvent
import android.view.inputmethod.EditorInfo
import android.view.inputmethod.InputConnection
import android.view.inputmethod.InputConnectionWrapper
import com.google.android.material.textfield.TextInputEditText

/**
 * A text box that reports what the keyboard does as it does it: characters
 * typed, characters deleted, Enter. The keyboard composes and corrects words in
 * place; each such change is sent as the backspaces and characters that turn
 * the old word into the new one, so the computer sees exactly one edit stream.
 */
class RemoteEditText @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
) : TextInputEditText(context, attrs) {

    var onText: ((String) -> Unit)? = null
    var onBackspace: ((Int) -> Unit)? = null
    var onEnter: (() -> Unit)? = null

    /** The word the keyboard is still composing, as last sent. */
    private var composing = ""

    override fun onCreateInputConnection(outAttrs: EditorInfo): InputConnection? {
        val base = super.onCreateInputConnection(outAttrs) ?: return null
        return object : InputConnectionWrapper(base, true) {
            override fun setComposingText(text: CharSequence, newCursorPosition: Int): Boolean {
                replace(text.toString())
                composing = text.toString()
                return super.setComposingText(text, newCursorPosition)
            }

            override fun commitText(text: CharSequence, newCursorPosition: Int): Boolean {
                replace(text.toString())
                composing = ""
                return super.commitText(text, newCursorPosition)
            }

            override fun finishComposingText(): Boolean {
                composing = ""
                return super.finishComposingText()
            }

            override fun deleteSurroundingText(beforeLength: Int, afterLength: Int): Boolean {
                if (beforeLength > 0) onBackspace?.invoke(beforeLength)
                return super.deleteSurroundingText(beforeLength, afterLength)
            }

            override fun sendKeyEvent(event: KeyEvent): Boolean {
                if (event.action == KeyEvent.ACTION_DOWN) {
                    when (event.keyCode) {
                        KeyEvent.KEYCODE_DEL -> onBackspace?.invoke(1)
                        KeyEvent.KEYCODE_ENTER -> onEnter?.invoke()
                    }
                }
                return super.sendKeyEvent(event)
            }
        }
    }

    /** Turns the composing word into [new] on the computer. */
    private fun replace(new: String) {
        val common = composing.commonPrefixWith(new).length
        val gone = composing.length - common
        if (gone > 0) onBackspace?.invoke(gone)
        if (new.length > common) onText?.invoke(new.substring(common))
    }

    /** After the box is emptied from code, nothing is being composed. */
    fun reset() {
        composing = ""
    }
}
