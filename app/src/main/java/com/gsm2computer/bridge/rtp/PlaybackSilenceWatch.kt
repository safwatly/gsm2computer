package com.gsm2computer.bridge.rtp

/**
 * Notices when hub audio is arriving and the call playback path is not
 * delivering it. A fresh [android.media.AudioTrack] is created per call;
 * [android.media.AudioTrack.setPreferredDevice] on an already-playing track
 * makes AudioFlinger log `dead IAudioTrack, creating a new one from
 * setOutputDevice` and the replacement track can lose in-call-music routing.
 * This watch tells the playback thread to release that track and arm a new one.
 *
 * [rxRms] is the energy of frames as they arrived. [playRms] is the energy
 * of what the playback loop actually wrote. [headAdvanced] is whether
 * `AudioTrack.playbackHeadPosition` moved. Mutual silence does not rebuild.
 */
class PlaybackSilenceWatch(
    private val speechRms: Int = 200,
    private val silentRms: Int = 8,
    private val windowMs: Long = 3_000,
    private val cooldownMs: Long = 8_000,
) {
    private var windowStart = -1L
    private var maxRx = 0
    private var maxPlay = 0
    private var headMoved = false
    private var samples = 0
    private var cooldownUntil = 0L

    data class Decision(val rebuild: Boolean, val reason: String = "")

    fun observe(nowMs: Long, rxRms: Int, playRms: Int, headAdvanced: Boolean): Decision {
        if (nowMs < cooldownUntil) return Decision(false)
        if (windowStart < 0L) windowStart = nowMs
        maxRx = maxOf(maxRx, rxRms)
        maxPlay = maxOf(maxPlay, playRms)
        if (headAdvanced) headMoved = true
        samples++
        if (nowMs - windowStart < windowMs) return Decision(false)
        val decision = decide()
        resetWindow(nowMs)
        if (decision.rebuild) cooldownUntil = nowMs + cooldownMs
        return decision
    }

    private fun decide(): Decision {
        if (samples == 0) return Decision(false)
        val rxSpeech = maxRx >= speechRms
        if (!rxSpeech) return Decision(false)
        if (maxPlay <= silentRms) {
            return Decision(true, "rx speech rms=$maxRx but playRMS=$maxPlay")
        }
        if (!headMoved) {
            return Decision(true, "track head stalled while rx rms=$maxRx playRMS=$maxPlay")
        }
        return Decision(false)
    }

    private fun resetWindow(nowMs: Long) {
        windowStart = nowMs
        maxRx = 0
        maxPlay = 0
        headMoved = false
        samples = 0
    }
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
