package com.gsm2computer.bridge

/**
 * When to apply AudioManager mic mute for [InjectPolicy.HAL_OWNED_API_MUTE].
 * The incall-music usecase has to be up first, or Qualcomm falls back to
 * stream Voice Tx Mute. The AudioFlinger output is the preferred signal.
 * A successful playback write plus [WRITE_DELAY_MS] is the fallback.
 */
internal object ApiMuteTiming {
    const val WRITE_DELAY_MS = 400L
    const val AUDIOFLINGER = "audioflinger"
    const val WRITE_DELAY = "write-delay"

    fun decide(
        trackPlaying: Boolean,
        routedToTelephony: Boolean,
        dumpsys: String,
        firstWriteAtMs: Long,
        nowMs: Long,
        observedForMs: Long = ApiMuteCaptureGuard.WINDOW_MS,
        writeDelayMs: Long = WRITE_DELAY_MS,
        captureWindowMs: Long = ApiMuteCaptureGuard.WINDOW_MS,
    ): String? {
        if (!trackPlaying || !routedToTelephony) return null
        if (observedForMs < captureWindowMs) return null
        if (audioFlingerIncallMusicActive(dumpsys)) return AUDIOFLINGER
        if (firstWriteAtMs > 0L && nowMs - firstWriteAtMs >= writeDelayMs) return WRITE_DELAY
        return null
    }

    /** True when a dump shows TELEPHONY_TX/INCALL_MUSIC and that output is not only standby. */
    fun audioFlingerIncallMusicActive(dump: String): Boolean {
        if (!dump.contains("INCALL_MUSIC") || !dump.contains("TELEPHONY_TX")) return false
        val standbyYes = Regex("""Standby:\s*yes""", RegexOption.IGNORE_CASE).containsMatchIn(dump)
        val standbyNo = Regex("""Standby:\s*no""", RegexOption.IGNORE_CASE).containsMatchIn(dump)
        if (standbyYes && !standbyNo) return false
        val stateIdle = dump.contains("state: STANDBY") || dump.contains("state: IDLE")
        val stateActive = dump.contains("state: ACTIVE")
        if (stateIdle && !stateActive && !standbyNo) return false
        return true
    }
}

/**
 * Compares about two seconds of capture RMS before the API mute with the
 * two seconds after it. A collapse means the mute hit the voice stream,
 * so the call drops back to leaving the mic alone.
 */
internal class ApiMuteCaptureGuard {
    private val before = ArrayList<Int>()
    private val after = ArrayList<Int>()
    private var mutedAtMs = 0L
    private var decided = false
    private var lastLogMs = 0L

    fun onFrame(rms: Int, nowMs: Long, muted: Boolean): GuardStep {
        if (decided) return GuardStep()
        if (!muted) {
            before.add(rms)
            while (before.size > MAX_SAMPLES) before.removeAt(0)
            return GuardStep(periodic(nowMs, "before", rms))
        }
        if (mutedAtMs == 0L) mutedAtMs = nowMs
        after.add(rms)
        if (nowMs - mutedAtMs < WINDOW_MS) {
            val opened = if (after.size == 1) summary("before", before) else null
            return GuardStep(opened ?: periodic(nowMs, "after", rms))
        }
        decided = true
        val collapse = collapsed(before, after)
        val line = summary("after", after)
        return GuardStep(line, revert = collapse)
    }

    private fun periodic(nowMs: Long, phase: String, rms: Int): String? {
        if (nowMs - lastLogMs < LOG_EVERY_MS) return null
        lastLogMs = nowMs
        return "rawCapRMS phase=$phase latest=$rms"
    }

    companion object {
        const val WINDOW_MS = 2_000L
        const val LOG_EVERY_MS = 500L
        const val MAX_SAMPLES = 120
        const val REVERT_LOG = "api_mute reverted: capture collapsed"

        fun collapsed(before: List<Int>, after: List<Int>): Boolean {
            if (before.isEmpty() || after.isEmpty()) return false
            val base = before.average()
            val now = after.average()
            if (base <= 0.0) return false
            if (now <= NEAR_ZERO) return true
            return now < base * COLLAPSE_RATIO
        }

        fun summary(phase: String, samples: List<Int>): String {
            if (samples.isEmpty()) return "rawCapRMS $phase n=0"
            return "rawCapRMS $phase n=${samples.size} mean=${samples.average().toInt()} " +
                "min=${samples.minOrNull()} max=${samples.maxOrNull()}"
        }

        private const val COLLAPSE_RATIO = 0.10
        private const val NEAR_ZERO = 1.0
    }
}

internal data class GuardStep(val logLine: String? = null, val revert: Boolean = false)
