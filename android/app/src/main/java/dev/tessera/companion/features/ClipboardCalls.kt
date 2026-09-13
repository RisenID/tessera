package dev.tessera.companion.features

import android.content.ClipData
import android.os.Binder
import android.os.IBinder
import android.os.Parcel
import java.lang.reflect.Method
import java.lang.reflect.Proxy

/** Calls on the clipboard service's binder interface, whoever is making them. */
object ClipboardCalls {

    /** How to list a binder proxy's methods: the app needs a hidden-API bypass, the shell does not. */
    fun interface Lookup {
        fun methods(service: Any): List<Method>
    }

    val PLAIN = Lookup { service -> service.javaClass.methods.toList() }

    fun read(service: Any, caller: String, lookup: Lookup = PLAIN): String? {
        val method = pick(service, "getPrimaryClip", lookup) ?: return null
        val clip = method.invoke(service, *argumentsFor(method, caller)) as? ClipData ?: return null
        return textOf(clip)
    }

    fun write(service: Any, caller: String, text: String, lookup: Lookup = PLAIN): Boolean {
        val method = pick(service, "setPrimaryClip", lookup) ?: return false
        val clip = ClipData.newPlainText("Tessera", text)
        // The ClipData is the first argument; the rest follow the same
        // package/tag/user/device pattern as the getter.
        method.invoke(service, clip, *argumentsFor(method, caller, skip = 1))
        return true
    }

    /** Asks the service to say when the clipboard changes. */
    fun listen(service: Any, caller: String, onChange: () -> Unit, lookup: Lookup = PLAIN): Boolean {
        val method = lookup.methods(service)
            .filter { it.name == "addPrimaryClipChangedListener" }
            .filter { it.parameterTypes.size >= 2 && it.parameterTypes[0].isInterface }
            .filter { it.parameterTypes.drop(1).all(::fillable) }
            .minByOrNull { it.parameterTypes.size } ?: return false

        val binder = object : Binder() {
            override fun onTransact(code: Int, data: Parcel, reply: Parcel?, flags: Int): Boolean {
                if (code == FIRST_CALL_TRANSACTION) {
                    onChange()
                    return true
                }
                return super.onTransact(code, data, reply, flags)
            }
        }
        val listenerType = method.parameterTypes[0]
        val listener = Proxy.newProxyInstance(listenerType.classLoader, arrayOf(listenerType)) { proxy, called, args ->
            when (called.name) {
                "asBinder" -> binder
                "dispatchPrimaryClipChanged" -> onChange()
                "equals" -> proxy === args?.getOrNull(0)
                "hashCode" -> System.identityHashCode(proxy)
                "toString" -> "TesseraClipListener"
                else -> null
            }
        }
        method.invoke(service, listener, *argumentsFor(method, caller, skip = 1))
        return true
    }

    // -- plumbing ------------------------------------------------------------

    /** The overload with the fewest parameters we know how to fill. */
    private fun pick(service: Any, name: String, lookup: Lookup): Method? =
        lookup.methods(service)
            .filter { it.name == name && it.parameterTypes.all(::fillable) }
            .minByOrNull { it.parameterTypes.size }

    private fun fillable(type: Class<*>): Boolean =
        type == String::class.java ||
            type == Int::class.javaPrimitiveType ||
            type == ClipData::class.java

    /**
     * Builds arguments positionally: strings are the caller package then the
     * attribution tag, and the integers are the user id then the device id.
     */
    private fun argumentsFor(method: Method, caller: String, skip: Int = 0): Array<Any?> {
        var stringsSeen = 0
        return method.parameterTypes.drop(skip).map { type ->
            when (type) {
                String::class.java -> {
                    stringsSeen++
                    if (stringsSeen == 1) caller else null   // package, then attribution tag
                }
                Int::class.javaPrimitiveType -> 0            // user 0, default device
                else -> null
            }
        }.toTypedArray()
    }

    fun textOf(clip: ClipData): String? {
        if (clip.itemCount == 0) return null
        val builder = StringBuilder()
        for (index in 0 until clip.itemCount) {
            clip.getItemAt(index).text?.let { builder.append(it) }
        }
        return builder.toString().takeIf { it.isNotEmpty() }
    }

    /** The clipboard service's binder, for a caller that may use ServiceManager directly. */
    fun asInterface(binder: IBinder): Any =
        Class.forName("android.content.IClipboard\$Stub")
            .getMethod("asInterface", IBinder::class.java)
            .invoke(null, binder)!!
}
