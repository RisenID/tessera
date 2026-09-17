package dev.tessera.companion

import android.content.Context
import android.text.Editable
import android.text.TextWatcher
import android.util.AttributeSet
import com.google.android.material.textfield.TextInputEditText

/**
 * A text box whose contents the computer mirrors. After every change, whatever
 * the keyboard did -- a character, a composed word, an autocorrection with its
 * space -- the computer is sent the backspaces and characters that turn what it
 * has into what the box now shows. Edits are taken to be at the end, where the
 * computer's cursor is.
 */
class RemoteEditText @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
) : TextInputEditText(context, attrs) {

    var onText: ((String) -> Unit)? = null
    var onBackspace: ((Int) -> Unit)? = null

    /** What the computer has been sent. */
    private var mirrored = ""
    private var quiet = false

    init {
        addTextChangedListener(object : TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) = Unit
            override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) = Unit
            override fun afterTextChanged(s: Editable?) {
                if (!quiet) sync(s?.toString().orEmpty())
            }
        })
    }

    private fun sync(now: String) {
        if (now == mirrored) return
        var common = mirrored.commonPrefixWith(now).length
        // Never split a surrogate pair: an emoji goes as a whole or not at all.
        if (common > 0 && Character.isHighSurrogate(mirrored[common - 1])) common--
        val gone = mirrored.length - common
        if (gone > 0) onBackspace?.invoke(gone)
        if (now.length > common) onText?.invoke(now.substring(common))
        mirrored = now
    }

    /** Empties the box without the computer being told. */
    fun clear() {
        quiet = true
        setText("")
        quiet = false
        mirrored = ""
    }
}
