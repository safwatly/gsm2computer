package com.gsm2computer.bridge.rtp

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Call 1 delivered a contiguous burst and the old catch-up dropped 356 frames,
 * speech included. These cases have no socket and no handset.
 */
class DownlinkCatchupTest {

    @Test
    fun silenceAboveTheTargetIsDroppedAndSpeechIsKept() {
        val energies = IntArray(130) { i -> if (i < 50) 0 else 3_000 }
        val trim = DownlinkCatchup.trim(energies)
        assertEquals(50, trim.silenceDropped)
        assertEquals(0, trim.speechDropped)
        assertEquals(80, trim.keep.count { it })
        assertTrue(trim.keep.copyOfRange(50, 130).all { it })
        assertTrue(trim.splice[50])
        assertFalse(trim.splice[51])
    }

    @Test
    fun aSpeechBurstKeepsTheNewestTwoSeconds() {
        val energies = IntArray(400) { 4_000 }
        val trim = DownlinkCatchup.trim(energies)
        assertEquals(0, trim.silenceDropped)
        assertEquals(300, trim.speechDropped)
        assertEquals(100, trim.keep.count { it })
        assertFalse(trim.keep[0])
        assertTrue(trim.keep[399])
        assertTrue(trim.splice[300])
        assertFalse(trim.splice[301])
    }

    @Test
    fun underTheTargetNothingIsDropped() {
        val trim = DownlinkCatchup.trim(IntArray(10) { 4_000 })
        assertEquals(0, trim.silenceDropped)
        assertEquals(0, trim.speechDropped)
        assertTrue(trim.keep.all { it })
    }

    @Test
    fun drainDropsTheQuietGapAndMarksTheFollowingSplice() {
        val energies = intArrayOf(4_000, 3_000, 400, 4_000)
        val drained = DownlinkCatchup.dropLowest(energies, BooleanArray(4), targetFrames = 3)
        assertEquals(400, drained!!.droppedEnergy)
        assertEquals(2, drained.droppedIndex)
        assertEquals(3, drained.energies.size)
        assertTrue(drained.splice[2])
        assertFalse(drained.splice[0])
    }

    @Test
    fun uniformSpeechIsNotPunchedOnTheWayDownToTheTarget() {
        val energies = IntArray(40) { 4_000 }
        assertNull(DownlinkCatchup.dropLowest(energies, BooleanArray(40), targetFrames = 25))
    }

    @Test
    fun silenceJoinInTrimIsASplice() {
        val trim = DownlinkCatchup.trim(
            intArrayOf(3_000, 0, 3_000),
            targetFrames = 1,
            speechKeepFrames = 10,
        )
        assertTrue(trim.keep[0])
        assertFalse(trim.keep[1])
        assertTrue(trim.keep[2])
        assertTrue(trim.splice[2])
        assertFalse(trim.splice[0])
    }

    @Test
    fun fullBufferDropsOldestSilenceBeforeOldestSpeech() {
        assertEquals(1, DownlinkCatchup.capDropIndex(intArrayOf(3_000, 0, 3_000)))
        assertEquals(0, DownlinkCatchup.capDropIndex(intArrayOf(3_000, 4_000, 3_500)))
    }

    @Test
    fun crossfadeJoinHasNoHardStep() {
        val fade = 32
        val jump = 32_000
        val joined = PcmSplice.join(constantPcm(fade, 16_000), constantPcm(fade * 3, -16_000), fade)
        val step = PcmSplice.maxAdjacentDelta(joined)
        assertTrue("step=$step", step <= jump / (fade - 1) + 4)
        assertTrue(step * 4 < jump)
        assertEquals(16_000, pcmSample(joined, 0))
        assertEquals(-16_000, pcmSample(joined, fade - 1))
    }

    @Test
    fun fadeOutReachesSilenceWithoutAHardStep() {
        val faded = PcmSplice.fadeOut(constantPcm(32, 16_000))
        assertEquals(0, pcmSample(faded, 31))
        val step = PcmSplice.maxAdjacentDelta(faded)
        assertTrue("step=$step", step <= 16_000 / 31 + 4)
    }

    @Test
    fun equalLevelsJoinWithNoStep() {
        val joined = PcmSplice.join(constantPcm(32, 1_000), constantPcm(96, 1_000), 32)
        assertEquals(0, PcmSplice.maxAdjacentDelta(joined))
    }

    @Test
    fun injectMarkerStaysOffUnlessTheFlagIsOne() {
        assertEquals(0, injectMarkerBeeps(null))
        assertEquals(0, injectMarkerBeeps(""))
        assertEquals(0, injectMarkerBeeps("0"))
        assertEquals(1, injectMarkerBeeps("1"))
    }

    @Test
    fun rxGapTracksTheMaxAndCountsStalls() {
        val gaps = DownlinkRxGaps(stallMs = 250)
        assertEquals(0L, gaps.onFrame(1_000L))
        assertEquals(20L, gaps.onFrame(1_020L))
        assertEquals(0, gaps.stalls)
        assertEquals(5_000L, gaps.onFrame(6_020L))
        assertEquals(1, gaps.stalls)
        assertEquals(5_000L, gaps.maxGapMs)
        gaps.onFrame(6_040L)
        assertEquals(1, gaps.stalls)
        assertEquals(5_000L, gaps.maxGapMs)
    }

    private fun constantPcm(samples: Int, level: Int): ByteArray {
        val pcm = ByteArray(samples * 2)
        for (i in 0 until samples) {
            pcm[i * 2] = (level and 0xFF).toByte()
            pcm[i * 2 + 1] = ((level shr 8) and 0xFF).toByte()
        }
        return pcm
    }

    private fun pcmSample(pcm: ByteArray, index: Int): Int {
        val lo = pcm[index * 2].toInt() and 0xFF
        val hi = pcm[index * 2 + 1].toInt()
        return (hi shl 8) or lo
    }
}
