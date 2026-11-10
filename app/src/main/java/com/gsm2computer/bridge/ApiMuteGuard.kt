package com.gsm2computer.bridge

/**
 * When to apply AudioManager mic mute for [InjectPolicy.HAL_OWNED_API_MUTE].
 * The incall-music usecase has to be up first, or Qualcomm falls back to
 * stream Voice Tx Mute. The signal is the output thread that carries the
 * flag, not a match anywhere in the dumpsys. A successful playback write
 * plus [WRITE_DELAY_MS] is the fallback when that thread never appears.
 */
internal object ApiMuteTiming {
    const val WRITE_DELAY_MS = 400L
    const val AUDIOFLINGER = "audioflinger"
    const val WRITE_DELAY = "write-delay"
    const val FALLBACK_LOG = "api_mute fallback: no usecase signal"

    /** Keep each output thread intact so Standby stays with its own flags. */
    const val DUMP_CMD = "dumpsys audio | awk '" +
        "/Output thread/ || /- Output:/ { " +
        "if (block ~ /INCALL_MUSIC|TELEPHONY_TX|telephony-tx/) print block \"\\n\"; " +
        "block = \$0; next } " +
        "{ block = block \"\\n\" \$0 } " +
        "END { if (block ~ /INCALL_MUSIC|TELEPHONY_TX|telephony-tx/) print block }'"

    fun decide(
        trackPlaying: Boolean,
        routedToTelephony: Boolean,
        dumpsys: String,
        firstWriteAtMs: Long,
        nowMs: Long,
        writeDelayMs: Long = WRITE_DELAY_MS,
    ): String? {
        if (!trackPlaying || !routedToTelephony) return null
        if (audioFlingerIncallMusicActive(dumpsys)) return AUDIOFLINGER
        if (firstWriteAtMs > 0L && nowMs - firstWriteAtMs >= writeDelayMs) return WRITE_DELAY
        return null
    }

    /**
     * True only when the output thread that carries INCALL_MUSIC is running.
     * Standby on a different thread does not count, and a telephony-tx
     * thread with no incall-music flag is not the usecase.
     */
    fun audioFlingerIncallMusicActive(dump: String): Boolean {
        return outputThreads(dump).any { block ->
            if (!block.contains("INCALL_MUSIC")) return@any false
            val running = Regex("""Standby:\s*no""", RegexOption.IGNORE_CASE).containsMatchIn(block) ||
                block.contains("state: ACTIVE")
            val stopped = Regex("""Standby:\s*yes""", RegexOption.IGNORE_CASE).containsMatchIn(block) ||
                block.contains("state: STANDBY") ||
                block.contains("state: IDLE")
            running && !stopped
        }
    }

    fun outputThreads(dump: String): List<String> {
        return Regex("(?m)(?=^\\s*(?:Output thread|- Output:))")
            .split(dump)
            .map { it.trim() }
            .filter { it.startsWith("Output thread") || it.startsWith("- Output:") }
    }
}

/**
 * Diagnostic RMS windows around the API mute. A quiet window is logged and
 * left alone; the bridge does not unmute on its own.
 */
internal class ApiMuteCaptureGuard {
    private val preMeans = ArrayList<Int>()
    private val postMeans = ArrayList<Int>()
    private val window = ArrayList<Int>()
    private var windowStartMs = UNSET
    private var sawMute = false
    private var done = false

    fun onFrame(rms: Int, nowMs: Long, muted: Boolean): String? {
        if (done) return null
        if (muted && !sawMute) {
            sawMute = true
            closeWindow(pre = true, restartAt = nowMs)
        }
        if (windowStartMs == UNSET) windowStartMs = nowMs
        val collectingPre = !sawMute
        if (collectingPre && preMeans.size >= MAX_WINDOWS) return null
        if (!collectingPre && postMeans.size >= MAX_WINDOWS) {
            done = true
            return null
        }
        window.add(rms)
        if (nowMs - windowStartMs < WINDOW_MS) return null
        return closeWindow(pre = collectingPre, restartAt = nowMs)
    }

    private fun closeWindow(pre: Boolean, restartAt: Long): String? {
        val had = window.isNotEmpty()
        if (had) {
            val mean = window.average().toInt()
            if (pre) {
                if (preMeans.size < MAX_WINDOWS) preMeans.add(mean)
            } else if (postMeans.size < MAX_WINDOWS) {
                postMeans.add(mean)
            }
        }
        window.clear()
        windowStartMs = restartAt
        if (!pre && postMeans.size >= MAX_WINDOWS) done = true
        if (!had) return null
        return format(preMeans, postMeans)
    }

    companion object {
        const val WINDOW_MS = 2_000L
        const val MAX_WINDOWS = 5
        private const val UNSET = Long.MIN_VALUE

        fun format(pre: List<Int>, post: List<Int>): String =
            "api_mute capture pre=${pre.joinToString(",")} post=${post.joinToString(",")}"
    }
}
