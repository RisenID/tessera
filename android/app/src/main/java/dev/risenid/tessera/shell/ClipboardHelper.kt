package dev.risenid.tessera.shell

import android.os.Build
import android.os.IBinder
import dev.risenid.tessera.features.ClipboardCalls
import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import kotlin.concurrent.thread
import kotlin.system.exitProcess

/** The phone's clipboard for a desktop that has adb and nothing else. */
object ClipboardHelper {

    private const val SHELL = "com.android.shell"

    @Volatile
    private var last: String? = null

    @JvmStatic
    fun main(args: Array<String>) {
        // --package exists for testing the same calls as another uid (run-as).
        val caller = args.indexOf("--package").takeIf { it >= 0 }
            ?.let { args.getOrNull(it + 1) } ?: SHELL

        val service = try {
            val binder = Class.forName("android.os.ServiceManager")
                .getMethod("getService", String::class.java)
                .invoke(null, "clipboard") as IBinder
            ClipboardCalls.asInterface(binder)
        } catch (e: Throwable) {
            emit(JSONObject().put("t", "error").put("message", "no clipboard service: $e"))
            exitProcess(2)
        }

        last = readSafely(service, caller)
        val listening = runCatching {
            ClipboardCalls.listen(service, caller, { changed(service, caller) })
        }.getOrElse { false }
        emit(
            JSONObject().put("t", "ready")
                .put("sdk", Build.VERSION.SDK_INT)
                .put("listening", listening)
        )
        if (!listening) {
            // No overload fitted: a second's delay is still better than none.
            thread(isDaemon = true, name = "tessera-clip-poll") {
                while (true) {
                    Thread.sleep(1_000)
                    changed(service, caller)
                }
            }
        }

        val input = BufferedReader(InputStreamReader(System.`in`, Charsets.UTF_8))
        while (true) {
            // End of input is the desktop going away, and the end of the job.
            val line = input.readLine() ?: break
            val message = runCatching { JSONObject(line) }.getOrNull() ?: continue
            when (message.optString("t")) {
                "set" -> {
                    val text = message.optString("text")
                    // Recorded first, so the change it causes is not sent back.
                    last = text
                    val ok = runCatching { ClipboardCalls.write(service, caller, text) }
                        .getOrElse { false }
                    if (!ok) emit(JSONObject().put("t", "error").put("message", "the clipboard refused the text"))
                }
                "get" -> emit(
                    JSONObject().put("t", "clip").put("reply", true)
                        .put("text", readSafely(service, caller).orEmpty())
                )
                // What this phone's clipboard service looks like, for when a
                // new Android release reshapes it again.
                "probe" -> emit(
                    JSONObject().put("t", "probe")
                        .put("methods", org.json.JSONArray(
                            service.javaClass.methods
                                .filter { "Clip" in it.name }
                                .map { m -> m.name + m.parameterTypes.joinToString(",", "(", ")") { it.simpleName } }
                        ))
                        .put("read", runCatching { ClipboardCalls.read(service, caller) }
                            .fold({ it.toString() }, { error ->
                                val cause = (error as? java.lang.reflect.InvocationTargetException)?.targetException ?: error
                                "failed: $cause"
                            }))
                )
                "quit" -> break
            }
        }
        exitProcess(0)
    }

    /** A change arrived that could not be read yet. */
    @Volatile
    private var unread = false
    private var rechecker: Thread? = null

    private fun changed(service: Any, caller: String) {
        val now = readSafely(service, caller)
        if (now == null) {
            unread = true
            synchronized(this) {
                if (rechecker == null) {
                    rechecker = thread(isDaemon = true, name = "tessera-clip-recheck") {
                        while (unread) {
                            Thread.sleep(2_000)
                            if (readSafely(service, caller) != null) {
                                unread = false
                                changed(service, caller)
                            }
                        }
                        synchronized(this) { rechecker = null }
                    }
                }
            }
            return
        }
        unread = false
        synchronized(this) {
            if (now == last) return
            last = now
        }
        emit(JSONObject().put("t", "clip").put("text", now))
    }

    private fun readSafely(service: Any, caller: String): String? =
        runCatching { ClipboardCalls.read(service, caller) }.getOrNull()

    @Synchronized
    private fun emit(message: JSONObject) {
        // JSONObject escapes newlines, so one object is always one line.
        println(message.toString())
        System.out.flush()
    }
}
