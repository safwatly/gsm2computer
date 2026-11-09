package com.gsm2computer.bridge.rtp

/**
 * What to keep when a downlink stall dumps a backlog into the jitter buffer.
 *
 * Silence is dropped first. Speech is kept, newest last, up to
 * [speechKeepFrames] (2 s at 20 ms). Older speech past that cap is dropped
 * so latency stays bounded, and the drop is counted separately from silence.
 * While the queue is still over [targetFrames], [DownlinkPlayout] skips one
 * speech frame in every [DownlinkPlayout.speedupEvery] so the kept backlog
 * plays slightly faster instead of sitting at the cap.
 */
internal object DownlinkCatchup {
    const val TARGET_FRAMES = 25
    const val SPEECH_KEEP_FRAMES = 100
    const val SPEECH_RMS = 200

    data class Trim(
        val keep: BooleanArray,
        val silenceDropped: Int,
        val speechDropped: Int,
    )

    fun trim(
        energies: IntArray,
        targetFrames: Int = TARGET_FRAMES,
        speechKeepFrames: Int = SPEECH_KEEP_FRAMES,
        speechRms: Int = SPEECH_RMS,
    ): Trim {
        val keep = BooleanArray(energies.size) { true }
        var alive = energies.size
        var silenceDropped = 0
        var speechDropped = 0
        var i = 0
        while (alive > targetFrames && i < energies.size) {
            if (keep[i] && energies[i] < speechRms) {
                keep[i] = false
                alive--
                silenceDropped++
            }
            i++
        }
        i = 0
        while (alive > speechKeepFrames && i < energies.size) {
            if (keep[i]) {
                keep[i] = false
                alive--
                if (energies[i] < speechRms) silenceDropped++ else speechDropped++
            }
            i++
        }
        return Trim(keep, silenceDropped, speechDropped)
    }
}

/**
 * Gaps between downlink messages on the phone clock.
 * A stall is a gap of at least [stallMs]. The first frame does not count.
 */
internal class DownlinkRxGaps(private val stallMs: Long = 250L) {
    @Volatile var maxGapMs: Long = 0L
        private set
    @Volatile var stalls: Int = 0
        private set
    private var lastAt = 0L

    @Synchronized
    fun onFrame(nowMs: Long): Long {
        val gap = if (lastAt > 0L) (nowMs - lastAt).coerceAtLeast(0L) else 0L
        if (gap > maxGapMs) maxGapMs = gap
        if (gap >= stallMs) stalls++
        lastAt = nowMs
        return gap
    }
}

/** `1` enables the 0.75 s inject marker. Anything else, including null, leaves the greeting alone. */
internal fun injectMarkerBeeps(flag: String?): Int = if (flag == "1") 1 else 0

/**
 * While more than [targetFrames] are queued, skip one speech frame out of
 * every [speedupEvery] so playout creeps back toward the target. Silence
 * above the target is skipped immediately.
 */
internal class DownlinkPlayout(
    private val targetFrames: Int = DownlinkCatchup.TARGET_FRAMES,
    private val speechRms: Int = DownlinkCatchup.SPEECH_RMS,
    private val speedupEvery: Int = 4,
) {
    private var speechPlays = 0

    fun skip(queueSizeIncludingThis: Int, energy: Int): Boolean {
        if (queueSizeIncludingThis <= targetFrames) {
            speechPlays = 0
            return false
        }
        if (energy < speechRms) return true
        speechPlays++
        if (speechPlays >= speedupEvery) {
            speechPlays = 0
            return true
        }
        return false
    }
}
