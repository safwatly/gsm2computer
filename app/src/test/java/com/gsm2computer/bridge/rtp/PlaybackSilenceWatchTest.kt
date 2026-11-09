package com.gsm2computer.bridge.rtp

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * No handset and no GSM call. A rebuild is what the bridge logs as
 * `playback silence: rebuilding AudioTrack`. The first window can rebuild
 * call-start silence in about a second. Prebuffer, a single sample, and a
 * head that keeps stepping must not rebuild: that toggles incall_music.
 */
class PlaybackSilenceWatchTest {

    @Test
    fun speechWrittenAndConsumedDoesNotRebuild() {
        val watch = PlaybackSilenceWatch()
        var decision = PlaybackSilenceWatch.Decision(false)
        for (t in 0L..3_000L step 20L) {
            decision = watch.observe(t, inputRms = 4_000, playRms = 4_000, headAdvanced = true, eligible = true)
        }
        assertFalse(decision.rebuild)
        assertTrue(decision.evaluated)
        assertEquals(0L, decision.silentMs)
    }

    @Test
    fun callStartPlayRmsZeroRebuildsWithinTwoSeconds() {
        val watch = PlaybackSilenceWatch()
        var decision = PlaybackSilenceWatch.Decision(false)
        for (t in 0L..980L step 20L) {
            decision = watch.observe(t, inputRms = 3_200, playRms = 0, headAdvanced = true, eligible = true)
            assertFalse(decision.rebuild)
            assertFalse(decision.evaluated)
        }
        decision = watch.observe(1_000L, inputRms = 3_200, playRms = 0, headAdvanced = true, eligible = true)
        assertTrue(decision.rebuild)
        assertTrue(decision.evaluated)
        assertTrue(decision.silentMs >= 600L)
        assertTrue(decision.reason.contains("call-start"))
        assertTrue(decision.reason.contains("playRMS=0"))
        assertTrue(decision.reason.contains("rx speech"))
    }

    @Test
    fun afterAHealthyWindowAShortSilenceDoesNotRebuild() {
        val watch = PlaybackSilenceWatch()
        var decision = PlaybackSilenceWatch.Decision(false)
        for (t in 0L..3_000L step 20L) {
            decision = watch.observe(t, inputRms = 4_000, playRms = 4_000, headAdvanced = true, eligible = true)
        }
        assertFalse(decision.rebuild)
        assertTrue(decision.evaluated)
        for (t in 3_020L..3_720L step 20L) {
            decision = watch.observe(t, inputRms = 3_200, playRms = 0, headAdvanced = true, eligible = true)
            assertFalse(decision.rebuild)
        }
    }

    @Test
    fun trackHeadStallWhileSpeechIsWrittenRebuilds() {
        val watch = PlaybackSilenceWatch()
        var decision = PlaybackSilenceWatch.Decision(false)
        for (t in 0L..3_000L step 20L) {
            decision = watch.observe(t, inputRms = 2_000, playRms = 2_000, headAdvanced = false, eligible = true)
        }
        assertTrue(decision.rebuild)
        assertTrue(decision.reason.contains("head stalled"))
    }

    @Test
    fun slowHeadStepDuringSpeechDoesNotRebuild() {
        val watch = PlaybackSilenceWatch()
        var decision = PlaybackSilenceWatch.Decision(false)
        for (t in 0L..6_000L step 20L) {
            decision = watch.observe(
                t,
                inputRms = 2_000,
                playRms = 2_000,
                headAdvanced = t % 100L == 0L,
                eligible = true,
            )
            assertFalse(decision.rebuild)
        }
    }

    @Test
    fun quietCallDoesNotRebuild() {
        val watch = PlaybackSilenceWatch()
        var decision = PlaybackSilenceWatch.Decision(false)
        for (t in 0L..6_000L step 20L) {
            decision = watch.observe(t, inputRms = 0, playRms = 0, headAdvanced = false, eligible = true)
        }
        assertFalse(decision.rebuild)
    }

    @Test
    fun prebufferOnsetDoesNotRebuild() {
        val watch = PlaybackSilenceWatch()
        var decision = PlaybackSilenceWatch.Decision(false)
        for (t in 0L..2_800L step 20L) {
            decision = watch.observe(t, inputRms = 3_200, playRms = 0, headAdvanced = true, eligible = false)
            assertFalse(decision.rebuild)
        }
        decision = watch.observe(3_000L, inputRms = 3_200, playRms = 0, headAdvanced = true, eligible = true)
        assertFalse(decision.rebuild)
    }

    @Test
    fun singleSampleDoesNotCloseTheWindow() {
        val watch = PlaybackSilenceWatch()
        assertFalse(
            watch.observe(0L, inputRms = 3_200, playRms = 0, headAdvanced = true, eligible = true).rebuild,
        )
        val closed = watch.observe(3_000L, inputRms = 3_200, playRms = 0, headAdvanced = true, eligible = true)
        assertFalse(closed.rebuild)
    }

    @Test
    fun postCooldownStartsAFreshFullWindow() {
        val watch = PlaybackSilenceWatch(cooldownMs = 8_000)
        assertTrue(drive(watch, 0L, playRms = 0).rebuild)
        val opened = watch.observe(11_000L, inputRms = 3_200, playRms = 0, headAdvanced = true, eligible = true)
        assertFalse(opened.rebuild)
        val early = watch.observe(11_020L, inputRms = 3_200, playRms = 0, headAdvanced = true, eligible = true)
        assertFalse(early.rebuild)
        assertTrue(drive(watch, 11_020L, playRms = 0).rebuild)
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
    fun telephonyRouteClearsAPendingRebuild() {
        assertEquals(true, playbackRebuildOnRoute(routedType = 2, telephonyType = 18))
        assertEquals(false, playbackRebuildOnRoute(routedType = 18, telephonyType = 18))
        assertNull(playbackRebuildOnRoute(routedType = -1, telephonyType = 18))
    }

    @Test
    fun retoggleOnlyWhenTheTrackIsNotOnTelephony() {
        assertFalse(incallRetoggleNeeded(routedType = 18, telephonyType = 18))
        assertTrue(incallRetoggleNeeded(routedType = 2, telephonyType = 18))
        assertTrue(incallRetoggleNeeded(routedType = -1, telephonyType = 18))
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
            decision = watch.observe(t, inputRms = 3_000, playRms = playRms, headAdvanced = true, eligible = true)
            if (decision.rebuild) return decision
            t += 20L
        }
        return decision
    }
}
