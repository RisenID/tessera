package dev.tessera.companion.features

/** Text typed from the computer into whatever has focus on this phone. */
object TextInput {

    fun available(): Boolean = PrivilegedShell.hasPermission() || ClipboardAccessibility.running

    /** Types [text], then Enter if asked. Null when it worked. */
    fun type(text: String, enter: Boolean): String? {
        if (text.isEmpty() && !enter) return "Nothing to type."
        if (PrivilegedShell.hasPermission()) {
            // The shell's input tool takes the text as one argument; spaces
            // have to be written as %s, and the rest quoted.
            if (text.isNotEmpty()) {
                val escaped = text.replace(" ", "%s")
                val result = PrivilegedShell.run("input text ${PrivilegedShell.quote(escaped)}")
                if (!result.ok) return "The phone would not type it: ${result.text.ifBlank { "input failed" }}"
            }
            if (enter) {
                val result = PrivilegedShell.run("input keyevent 66")
                if (!result.ok) return "The phone would not press Enter."
            }
            return null
        }
        val service = ClipboardAccessibility.instance
            ?: return "Typing needs Shizuku, or Tessera switched on under Accessibility on the phone."
        if (text.isNotEmpty() && !service.typeText(text)) {
            return "Nothing on the phone has a text field in focus."
        }
        if (enter && !service.pressEnter()) return "The focused field took no Enter."
        return null
    }
}
