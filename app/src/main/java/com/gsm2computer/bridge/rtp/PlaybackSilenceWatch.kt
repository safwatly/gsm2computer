package com.gsm2computer.bridge.rtp

/**
 * Notices when hub audio was written to the call track and the track is not
 * delivering it. A fresh [android.media.AudioTrack] is created per call;
 * [android.media.AudioTrack.setPreferredDevice] on an already-playing track
 * makes AudioFlinger replace the native track and drop in-call-music routing.
 *
 * Only [eligible] samples count. Those are frames the playback loop dequeued
 * and wrote. Prebuffer silence and frames still sitting in the jitter buffer
 * are not eligible: their receive energy is not "playRMS should be loud."
 * One eligible sample is not enough. Near-zero output has to cover most of
 * the window. A stalled head counts only while it stays still: the next
 * advance clears that run, so a deep buffer that steps every few polls does
 * not rebuild. Closing a window, or leaving cooldown, starts the next window
 * on the following sample.
 */
class PlaybackSilenceWatch(
    private val speechRms: Int = 200,
    private val silentRms: Int = 8,
    private val windowMs: Long = 3_000,
    private val cooldownMs: Long = 8_000,
    private val sampleGapCapMs: Long = 40,
) {
    private val sustainedMs = windowMs / 2
    private var windowStart = -1L
    private var lastAt = -1L
    private var silentMs = 0L
    private var stallMs = 0L
    private var maxInput = 0
    private var maxPlay = 0
    private var cooldownUntil = 0L

    data class Decision(val rebuild: Boolean, val reason: String = "")

    fun observe(
        nowMs: Long,
        inputRms: Int,
        playRms: Int,
        headAdvanced: Boolean,
        eligible: Boolean,
    ): Decision {
        if (nowMs < cooldownUntil) return Decision(false)
        if (windowStart < 0L) {
            openWindow(nowMs)
            return Decision(false)
        }
        val gap = (nowMs - lastAt).coerceIn(0L, sampleGapCapMs)
        lastAt = nowMs
        if (headAdvanced) stallMs = 0L
        if (eligible && inputRms >= speechRms) {
            maxInput = maxOf(maxInput, inputRms)
            maxPlay = maxOf(maxPlay, playRms)
            if (playRms <= silentRms) silentMs += gap
            if (!headAdvanced) stallMs += gap
        }
        if (nowMs - windowStart < windowMs) return Decision(false)
        val decision = decide()
        windowStart = -1L
        lastAt = -1L
        silentMs = 0L
        stallMs = 0L
        maxInput = 0
        maxPlay = 0
        if (decision.rebuild) cooldownUntil = nowMs + cooldownMs
        return decision
    }

    private fun openWindow(nowMs: Long) {
        windowStart = nowMs
        lastAt = nowMs
        silentMs = 0L
        stallMs = 0L
        maxInput = 0
        maxPlay = 0
    }

    private fun decide(): Decision {
        if (silentMs >= sustainedMs) {
            return Decision(true, "rx speech rms=$maxInput but playRMS=$maxPlay")
        }
        if (stallMs >= sustainedMs) {
            return Decision(true, "track head stalled while rx rms=$maxInput playRMS=$maxPlay")
        }
        return Decision(false)
    }
}

/**
 * `true` when the track left the telephony output and should be rebuilt.
 * `false` when it is back on telephony, which clears a pending rebuild.
 * `null` when the route is not known yet.
 */
internal fun playbackRebuildOnRoute(routedType: Int, telephonyType: Int): Boolean? {
    if (routedType < 0) return null
    return routedType != telephonyType
}

/** Energy of one hub downlink frame, before the playback loop rewrites it. */
object FrameEnergy {
    fun rms(frame: ByteArray, format: String): Int {
        if (frame.isEmpty()) return 0
        val fmt = format.lowercase()
        val pcm = fmt.contains("pcm") && !fmt.contains("pcmu") && !fmt.contains("ulaw")
        return if (pcm) pcm16(frame) else ulaw(frame)
    }

    private fun pcm16(frame: ByteArray): Int {
        val samples = frame.size / 2
        if (samples == 0) return 0
        var sum = 0L
        for (i in 0 until samples) {
            val lo = frame[i * 2].toInt() and 0xFF
            val hi = frame[i * 2 + 1].toInt()
            val sample = (hi shl 8) or lo
            sum += sample.toLong() * sample
        }
        return Math.sqrt(sum.toDouble() / samples).toInt()
    }

    private fun ulaw(frame: ByteArray): Int {
        var sum = 0L
        for (b in frame) {
            val sample = PcmuCodec.decodeSample(b).toInt()
            sum += sample.toLong() * sample
        }
        return Math.sqrt(sum.toDouble() / frame.size).toInt()
    }
}
