package com.gsm2computer.bridge

/**
 * Per-reason gate for [VoiceTxMuteJob].
 *
 * A missing stamp must stay null. Substituting [Long.MIN_VALUE] makes
 * `now - prev` overflow negative, the `< debounce` check stays true, and
 * the call-active Voice Tx Mute tuple never schedules. Stamps are
 * [android.os.SystemClock.elapsedRealtime] so a wall-clock step cannot
 * reopen or freeze the window. [forget] drops the stamp when the call
 * ends, so the next ACTIVE is not still inside the previous window.
 */
internal class VoiceTxDebounce {
    private val lastAt = HashMap<String, Long>()

    /** True when this trigger should run. Stores [now] only when it does. */
    fun allow(reason: String, now: Long, debounceMs: Long): Boolean {
        if (debounceMs <= 0L) return true
        synchronized(lastAt) {
            if (!allows(lastAt[reason], now, debounceMs)) return false
            lastAt[reason] = now
            return true
        }
    }

    fun forget(reason: String) {
        synchronized(lastAt) { lastAt.remove(reason) }
    }

    companion object {
        fun allows(prev: Long?, now: Long, debounceMs: Long): Boolean {
            if (debounceMs <= 0L) return true
            val earlier = prev ?: return true
            return elapsed(earlier, now) >= debounceMs
        }

        /**
         * Non-negative distance from [prev] to [now]. A wrapped subtraction
         * (the [Long.MIN_VALUE] case) is already outside any debounce window.
         * A small backwards step is not.
         */
        fun elapsed(prev: Long, now: Long): Long {
            val delta = now - prev
            if (delta >= 0L) return delta
            if (wrapped(now, prev, delta)) return Long.MAX_VALUE
            return 0L
        }

        private fun wrapped(now: Long, prev: Long, delta: Long): Boolean =
            ((now xor prev) and (now xor delta)) < 0L
    }
}
