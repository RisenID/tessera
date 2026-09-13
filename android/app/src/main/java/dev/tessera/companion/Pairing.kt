package dev.tessera.companion

import android.os.SystemClock

/** The six-digit code shown on the phone during pairing. */
object Pairing {

    private const val VALID_MILLIS = 60_000L

    @Volatile
    private var code: String? = null

    @Volatile
    private var issuedAt = 0L

    val active: String?
        get() = code?.takeIf { SystemClock.elapsedRealtime() - issuedAt < VALID_MILLIS }

    fun issue(): String {
        val generated = Store.pairingCode()
        code = generated
        issuedAt = SystemClock.elapsedRealtime()
        return generated
    }

    fun cancel() {
        code = null
    }

    /** Checks *candidate* and burns the code on success. */
    fun consume(candidate: String): Boolean {
        val current = active ?: return false
        // Constant-time-ish comparison; the code is short but there is no reason
        // to leak position information.
        if (candidate.length != current.length) return false
        var difference = 0
        for (index in current.indices) {
            difference = difference or (candidate[index].code xor current[index].code)
        }
        if (difference != 0) return false
        code = null
        return true
    }
}
