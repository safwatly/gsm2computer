package com.gsm2computer.bridge.rtp

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * No handset and no GSM call. Feed the watch the same numbers the playback
 * loop will: received-frame RMS, written PCM RMS, and whether the track head
 * moved. A rebuild decision is what build 347 logs as
 * `playback silence: rebuilding AudioTrack`.
 */
class PlaybackSilenceWatchTest {

    @Test
    fun speechWrittenAndConsumedDoesNotRebuild() {
        val watch = PlaybackSilenceWatch()
        var decision = PlaybackSilenceWatch.Decision(false)
        for (t in 0L..3_000L step 20L) {
            decision = watch.observe(t, rxRms = 4_000, playRms = 4_000, headAdvanced = true)
        }
        assertFalse(decision.rebuild)
    }

    @Test
    fun playRmsStaysZeroWhileRxHasSpeech() {
        val watch = PlaybackSilenceWatch()
        var decision = PlaybackSilenceWatch.Decision(false)
        for (t in 0L..2_980L step 20L) {
            decision = watch.observe(t, rxRms = 3_200, playRms = 0, headAdvanced = true)
            assertFalse(decision.rebuild)
        }
        decision = watch.observe(3_000L, rxRms = 3_200, playRms = 0, headAdvanced = true)
        assertTrue(decision.rebuild)
        assertTrue(decision.reason.contains("playRMS=0"))
        assertTrue(decision.reason.contains("rx speech"))
    }

    @Test
    fun trackHeadStallWhileSpeechIsWrittenRebuilds() {
        val watch = PlaybackSilenceWatch()
        var decision = PlaybackSilenceWatch.Decision(false)
        for (t in 0L..3_000L step 20L) {
            decision = watch.observe(t, rxRms = 2_000, playRms = 2_000, headAdvanced = false)
        }
        assertTrue(decision.rebuild)
        assertTrue(decision.reason.contains("head stalled"))
    }

    @Test
    fun quietCallDoesNotRebuild() {
        val watch = PlaybackSilenceWatch()
        var decision = PlaybackSilenceWatch.Decision(false)
        for (t in 0L..6_000L step 20L) {
            decision = watch.observe(t, rxRms = 0, playRms = 0, headAdvanced = false)
        }
        assertFalse(decision.rebuild)
    }

    @Test
    fun cooldownSuppressesASecondRebuild() {
        val watch = PlaybackSilenceWatch(cooldownMs = 8_000)
        val first = drive(watch, 0L, playRms = 0)
        assertTrue(first.rebuild)
        val during = drive(watch, 3_000L, playRms = 0)
        assertFalse(during.rebuild)
        val after = drive(watch, 3_000L + 8_000L, playRms = 0)
        assertTrue(after.rebuild)
    }

    @Test
    fun frameEnergySeesPcmSpeechAndUlawSilence() {
        val pcm = ByteArray(4)
        pcm[0] = 0x00
        pcm[1] = 0x10 // 4096
        pcm[2] = 0x00
        pcm[3] = 0x10
        assertTrue(FrameEnergy.rms(pcm, "audio/pcm") > 200)
        assertEquals(0, FrameEnergy.rms(ByteArray(8), "audio/pcm"))
        val silence = ByteArray(8) { 0xFF.toByte() }
        assertTrue(FrameEnergy.rms(silence, "audio/pcmu") < 8)
        val loud = ByteArray(8) { 0x00 }
        assertTrue(FrameEnergy.rms(loud, "audio/pcmu") > 200)
    }

    private fun drive(watch: PlaybackSilenceWatch, start: Long, playRms: Int): PlaybackSilenceWatch.Decision {
        var decision = PlaybackSilenceWatch.Decision(false)
        var t = start
        val end = start + 3_000L
        while (t <= end) {
            decision = watch.observe(t, rxRms = 3_000, playRms = playRms, headAdvanced = true)
            if (decision.rebuild) return decision
            t += 20L
        }
        return decision
    }
}
