package com.gsm2computer.bridge.rtp

/**
 * Notices when hub audio was written to the call track and the track is not
 * delivering it. A fresh [android.media.AudioTrack] is created per call.
 * [android.media.AudioTrack.setPreferredDevice] replaces the native track
 * (`restoreTrack_l` from `setOutputDevice`) even while Java still says the
 * track is stopped, and that drops in-call-music routing.
 *
 * Only [eligible] samples count. Those are frames the playback loop dequeued
 * and wrote. Prebuffer silence and frames still sitting in the jitter buffer
 * are not eligible: their receive energy is not "playRMS should be loud."
 * One eligible sample is not enough. The first window of a call rebuilds
 * once near-zero output has covered [startupSilentMs] and at least
 * [startupElapsedMs] have passed, so a stretch of real silence is not lost
 * for the whole 3s window. A later eligible frame with play RMS above
 * [silentRms] clears that run, the same way a head step clears a stall.
 * Otherwise 600 ms of zeros followed by loud audio would still rebuild at
 * 1 s and [RtpSession] would toggle incall_music. Later windows still need
 * half of [windowMs]. A stalled head counts only while it stays still, and
 * only against that longer bar. Closing a window, or leaving cooldown,
 * starts the next window on the following sample. [Decision.evaluated] is
 * set only when a window actually decides, so the call log can show holds
 * as well as fires. The silence reason quotes the play RMS that accumulated
 * the run, which stays at or under [silentRms], not the window's loudest frame.
 */
class PlaybackSilenceWatch(
    private val speechRms: Int = 200,
    private val silentRms: Int = 8,
    private val windowMs: Long = 3_000,
    private val cooldownMs: Long = 8_000,
    private val sampleGapCapMs: Long = 40,
    private val startupElapsedMs: Long = 1_000,
    private val startupSilentMs: Long = 600,
) {
    private val sustainedMs = windowMs / 2
    private var opening = true
    private var windowStart = -1L
    private var lastAt = -1L
    private var silentMs = 0L
    private var stallMs = 0L
    private var maxInput = 0
    private var maxPlay = 0
    private var silentPlay = 0
    private var cooldownUntil = 0L

    data class Decision(
        val rebuild: Boolean,
        val reason: String = "",
        val evaluated: Boolean = false,
        val silentMs: Long = 0,
        val stallMs: Long = 0,
        val maxInput: Int = 0,
        val maxPlay: Int = 0,
    )

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
            if (playRms <= silentRms) {
                silentMs += gap
                silentPlay = maxOf(silentPlay, playRms)
            } else {
                silentMs = 0L
                silentPlay = 0
            }
            if (!headAdvanced) stallMs += gap
        }
        val elapsed = nowMs - windowStart
        if (opening && elapsed >= startupElapsedMs && silentMs >= startupSilentMs) {
            val reason = "call-start rx speech rms=$maxInput but playRMS=$silentPlay"
            return finish(nowMs, snapshot(true, reason))
        }
        if (elapsed < windowMs) return Decision(false)
        return finish(nowMs, decide())
    }

    private fun openWindow(nowMs: Long) {
        windowStart = nowMs
        lastAt = nowMs
        silentMs = 0L
        stallMs = 0L
        maxInput = 0
        maxPlay = 0
        silentPlay = 0
    }

    private fun decide(): Decision {
        if (silentMs >= sustainedMs) {
            return snapshot(true, "rx speech rms=$maxInput but playRMS=$silentPlay")
        }
        if (stallMs >= sustainedMs) {
            return snapshot(true, "track head stalled while rx rms=$maxInput playRMS=$maxPlay")
        }
        return snapshot(false, "")
    }

    private fun snapshot(rebuild: Boolean, reason: String) = Decision(
        rebuild = rebuild,
        reason = reason,
        evaluated = true,
        silentMs = silentMs,
        stallMs = stallMs,
        maxInput = maxInput,
        maxPlay = maxPlay,
    )

    private fun finish(nowMs: Long, decision: Decision): Decision {
        windowStart = -1L
        lastAt = -1L
        silentMs = 0L
        stallMs = 0L
        maxInput = 0
        maxPlay = 0
        opening = false
        if (decision.rebuild) cooldownUntil = nowMs + cooldownMs
        return decision
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

/**
 * The 3s false→true re-toggle is for a track whose route is not telephony yet.
 * Repeating it once the track is already on telephony tears the incall-music
 * usecase down, and the HAL can take the rest of the greeting to bring it back.
 * An unknown route (`routedType < 0`) still re-toggles.
 */
internal fun incallRetoggleNeeded(routedType: Int, telephonyType: Int): Boolean =
    routedType != telephonyType

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
