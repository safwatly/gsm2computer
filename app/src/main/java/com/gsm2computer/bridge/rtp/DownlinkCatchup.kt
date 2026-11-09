package com.gsm2computer.bridge.rtp

/**
 * What to keep when a downlink stall dumps a backlog into the jitter buffer.
 *
 * Silence is dropped first. Speech is kept, newest last, up to
 * [speechKeepFrames] (2 s at 20 ms). Older speech past that cap is dropped
 * so latency stays bounded. A kept frame that follows a dropped one is a
 * splice: playback crossfades it instead of butting the samples together.
 * While the queue is still over [targetFrames], [dropLowest] removes one
 * lowest-energy frame (a gap between words) per tick. Ties drop the oldest,
 * so a run of equal speech is not punched every fourth frame.
 */
internal object DownlinkCatchup {
    const val TARGET_FRAMES = 25
    const val SPEECH_KEEP_FRAMES = 100
    const val SPEECH_RMS = 200

    data class Trim(
        val keep: BooleanArray,
        val splice: BooleanArray,
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
        return Trim(keep, splicesAfter(keep), silenceDropped, speechDropped)
    }

    /** True on a kept frame whose predecessor in this batch was dropped. */
    fun splicesAfter(keep: BooleanArray): BooleanArray {
        val splice = BooleanArray(keep.size)
        var dropped = false
        for (i in keep.indices) {
            if (!keep[i]) {
                dropped = true
                continue
            }
            splice[i] = dropped
            dropped = false
        }
        return splice
    }

    /**
     * One drain step while [energies] is over [targetFrames]. Drops the
     * quietest frame when it is a gap (under [speechRms], or at most half
     * the loudest). Uniform speech is left alone: the 2 s cap already
     * dropped the oldest of a long burst. The frame after the drop is a splice.
     * Null when there is nothing quieter to remove.
     */
    fun dropLowest(
        energies: IntArray,
        splice: BooleanArray,
        targetFrames: Int = TARGET_FRAMES,
        speechRms: Int = SPEECH_RMS,
    ): Drain? {
        if (energies.size <= targetFrames || energies.isEmpty()) return null
        val idx = lowestIndex(energies)
        val dropped = energies[idx]
        var loudest = energies[0]
        for (e in energies) if (e > loudest) loudest = e
        val gap = dropped < speechRms || dropped * 2 <= loudest
        if (!gap || dropped >= loudest) return null
        val n = energies.size - 1
        val keptEnergy = IntArray(n)
        val keptSplice = BooleanArray(n)
        var w = 0
        for (i in energies.indices) {
            if (i == idx) continue
            keptEnergy[w] = energies[i]
            keptSplice[w] = splice[i] || i == idx + 1
            w++
        }
        return Drain(keptEnergy, keptSplice, dropped, idx)
    }

    /** Oldest silence, or the oldest frame when every frame is speech. */
    fun capDropIndex(energies: IntArray, speechRms: Int = SPEECH_RMS): Int {
        for (i in energies.indices) {
            if (energies[i] < speechRms) return i
        }
        return 0
    }

    private fun lowestIndex(energies: IntArray): Int {
        var best = 0
        for (i in 1 until energies.size) {
            if (energies[i] < energies[best]) best = i
        }
        return best
    }

    data class Drain(
        val energies: IntArray,
        val splice: BooleanArray,
        val droppedEnergy: Int,
        val droppedIndex: Int,
    )
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

/** Diagnostic build: the 0.75 s marker is on unless the property is exactly `0`. */
internal fun injectMarkerBeeps(flag: String?): Int = if (flag == "0") 0 else 1

/**
 * 4 ms linear crossfade at a join where a frame was removed.
 * The overlap starts at the previous sample and ends at the next, so the
 * step between neighbours is the level change spread across the fade.
 */
internal object PcmSplice {
    const val FADE_MS = 4

    fun fadeSamples(sampleRate: Int): Int {
        val rate = sampleRate.coerceIn(8_000, 48_000)
        return (rate * FADE_MS / 1000).coerceIn(16, 240)
    }

    /**
     * Overlap mixed into the previous tail's place, then the middle of [next].
     * The last [fadeSamples] of [next] are left out so the caller can hold
     * them for the following join.
     */
    fun join(previousTail: ByteArray, next: ByteArray, fadeSamples: Int): ByteArray {
        val prevN = previousTail.size / 2
        val nextN = next.size / 2
        val overlap = minOf(prevN, nextN, fadeSamples.coerceAtLeast(1)).coerceAtLeast(1)
        val middleEnd = (nextN - fadeSamples).coerceAtLeast(overlap)
        val middle = (middleEnd - overlap).coerceAtLeast(0)
        val out = ByteArray((overlap + middle) * 2)
        val den = (overlap - 1).coerceAtLeast(1)
        for (i in 0 until overlap) {
            val t = if (overlap == 1) den else i
            val mixed = (sample(previousTail, i).toLong() * (den - t) + sample(next, i).toLong() * t) / den
            put(out, i, mixed.toInt())
        }
        for (i in 0 until middle) {
            put(out, overlap + i, sample(next, overlap + i))
        }
        return out
    }

    fun fadeOut(pcm: ByteArray): ByteArray {
        val out = pcm.copyOf()
        val n = out.size / 2
        val den = (n - 1).coerceAtLeast(1)
        for (i in 0 until n) {
            val faded = sample(out, i).toLong() * (den - i) / den
            put(out, i, faded.toInt())
        }
        return out
    }

    fun takeTail(pcm: ByteArray, samples: Int): ByteArray {
        val n = pcm.size / 2
        val take = samples.coerceIn(0, n)
        return pcm.copyOfRange((n - take) * 2, pcm.size)
    }

    fun maxAdjacentDelta(pcm: ByteArray): Int {
        val n = pcm.size / 2
        var max = 0
        for (i in 1 until n) {
            val d = kotlin.math.abs(sample(pcm, i) - sample(pcm, i - 1))
            if (d > max) max = d
        }
        return max
    }

    private fun sample(pcm: ByteArray, index: Int): Int {
        val lo = pcm[index * 2].toInt() and 0xFF
        val hi = pcm[index * 2 + 1].toInt()
        return (hi shl 8) or lo
    }

    private fun put(pcm: ByteArray, index: Int, sample: Int) {
        val clamped = sample.coerceIn(-32768, 32767)
        pcm[index * 2] = (clamped and 0xFF).toByte()
        pcm[index * 2 + 1] = ((clamped shr 8) and 0xFF).toByte()
    }
}
