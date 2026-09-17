package dev.risenid.tessera

import android.os.SystemClock

/** The six-digit code shown on the phone during pairing. */
object Pairing {

    private const val VALID_MILLIS = 60_000L

    @Volatile
    private var code: String? = null

    @Volatile
    private var issuedAt = 0L

    /** Wrong guesses allowed before the code is thrown away. */
    private const val MAX_FAILURES = 5
    private var failures = 0

    val active: String?
        get() = code?.takeIf { SystemClock.elapsedRealtime() - issuedAt < VALID_MILLIS }

    @Synchronized
    fun issue(): String {
        val generated = Store.pairingCode()
        code = generated
        failures = 0
        issuedAt = SystemClock.elapsedRealtime()
        return generated
    }

    fun cancel() {
        code = null
    }

    /** Checks *candidate* and burns the code on success. */
    @Synchronized
    fun consume(candidate: String): Boolean {
        val current = active ?: return false
        // Constant-time-ish comparison; the code is short but there is no reason
        // to leak position information.
        var difference = candidate.length xor current.length
        for (index in current.indices) {
            difference = difference or ((candidate.getOrNull(index)?.code ?: 0) xor current[index].code)
        }
        if (difference != 0) {
            if (++failures >= MAX_FAILURES) code = null
            return false
        }
        code = null
        return true
    }
}
