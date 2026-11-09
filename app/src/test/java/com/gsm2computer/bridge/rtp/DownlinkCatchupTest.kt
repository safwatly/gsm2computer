package com.gsm2computer.bridge.rtp

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
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
    }

    @Test
    fun underTheTargetNothingIsDropped() {
        val trim = DownlinkCatchup.trim(IntArray(10) { 4_000 })
        assertEquals(0, trim.silenceDropped)
        assertEquals(0, trim.speechDropped)
        assertTrue(trim.keep.all { it })
    }

    @Test
    fun speedupSkipsEveryFourthSpeechFrameAndLeavesAShortQueueAlone() {
        val playout = DownlinkPlayout()
        val skips = (1..8).map { playout.skip(queueSizeIncludingThis = 40, energy = 3_000) }
        assertEquals(listOf(false, false, false, true, false, false, false, true), skips)
        assertFalse(playout.skip(queueSizeIncludingThis = 25, energy = 3_000))
        assertFalse(playout.skip(queueSizeIncludingThis = 10, energy = 0))
    }

    @Test
    fun speedupDropsSilenceWhileOverTheTarget() {
        val playout = DownlinkPlayout()
        assertTrue(playout.skip(queueSizeIncludingThis = 40, energy = 0))
        assertTrue(playout.skip(queueSizeIncludingThis = 30, energy = 8))
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
}
