package com.gsm2computer.bridge

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class VoiceTxUnmuteTest {
    private val livePcm = """
        00-02: VoiceMMode1 (*) :  : playback 1 : capture 1
        00-03: VoIP (*) :  : playback 1 : capture 1
        00-15: VoiceMMode2 (*) :  : playback 1 : capture 1
        00-23: MultiMedia9 (*) :  : playback 1 : capture 1
    """.trimIndent()

    @Test
    fun livePcmIdsKeepTheDynamicStarSuffix() {
        val lines = livePcm.lines()
        assertEquals("VoiceMMode1 (*)", VoiceTxUnmute.idFromPcmListLine(lines[0]))
        assertEquals("VoIP (*)", VoiceTxUnmute.idFromPcmListLine(lines[1]))
        assertEquals("VoiceMMode2 (*)", VoiceTxUnmute.idFromPcmListLine(lines[2]))
        assertEquals("MultiMedia9 (*)", VoiceTxUnmute.idFromPcmListLine(lines[3]))
        assertNull(VoiceTxUnmute.idFromPcmListLine("00-02:  :  : playback 1"))
    }

    @Test
    fun shorterVoicePrefixDoesNotStealVoiceMMode1OrVoice2() {
        assertEquals(VoiceTxUnmute.MMODE1_VSID, VoiceTxUnmute.match("VoiceMMode1 (*)")!!.vsid)
        assertEquals(0x10DC1000L, VoiceTxUnmute.match("Voice2 (*)")!!.vsid)
        assertEquals(0x10C01000L, VoiceTxUnmute.match("Voice (*)")!!.vsid)
        assertFalse(VoiceTxUnmute.match("VoiceMMode2 (*)")!!.vsid == VoiceTxUnmute.MMODE1_VSID)
    }

    @Test
    fun noStatusFilesOnTheLiveCardStillWritesOnlyMMode1() {
        val choice = VoiceTxUnmute.choose(livePcm, "")
        assertEquals(listOf(VoiceTxUnmute.MMODE1_VSID), choice.vsids)
        assertEquals(297816064L, choice.vsids.single())
        assertEquals("default-mmode1", choice.why)
        assertFalse(choice.vsids.contains(VoiceTxUnmute.MMODE2_VSID))
    }

    @Test
    fun emptyProbeAndEmptyHalStillDefaultToMMode1() {
        val choice = VoiceTxUnmute.choose("", "")
        assertEquals(listOf(VoiceTxUnmute.MMODE1_VSID), choice.vsids)
        assertEquals("default-mmode1", choice.why)
    }

    @Test
    fun statusTextIsNotAGate() {
        val closed = "id=VoiceMMode1 (*) state=closed\nid=VoiceMMode2 (*) state=OPEN"
        val choice = VoiceTxUnmute.choose(livePcm, closed)
        assertEquals(listOf(VoiceTxUnmute.MMODE1_VSID), choice.vsids)
        assertEquals("default-mmode1", choice.why)
    }

    @Test
    fun halUmtsVoiceMMode1SelectsThatVsidDespiteBothPcmIds() {
        val hal = "active UMTS voicemmode1-call, VSID 0x11c05000"
        val choice = VoiceTxUnmute.choose(livePcm, hal)
        assertEquals(listOf(VoiceTxUnmute.MMODE1_VSID), choice.vsids)
        assertEquals("hal", choice.why)
    }

    @Test
    fun halVoiceMMode2SelectsMMode2() {
        val choice = VoiceTxUnmute.choose(livePcm, "voicemmode2-call VSID 0x11DC5000")
        assertEquals(listOf(VoiceTxUnmute.MMODE2_VSID), choice.vsids)
        assertEquals("hal", choice.why)
    }

    @Test
    fun propertyNamingOnlyMMode2SelectsMMode2() {
        val prop = "[vendor.audio.voice.vsid]: [0x11DC5000]"
        val choice = VoiceTxUnmute.choose(livePcm, prop)
        assertEquals(listOf(VoiceTxUnmute.MMODE2_VSID), choice.vsids)
        assertEquals("hal", choice.why)
    }

    @Test
    fun pcmListWithOnlyMMode2SelectsMMode2() {
        val only = "00-15: VoiceMMode2 (*) :  : playback 1 : capture 1"
        val choice = VoiceTxUnmute.choose(only, "")
        assertEquals(listOf(VoiceTxUnmute.MMODE2_VSID), choice.vsids)
        assertEquals("pcm-id", choice.why)
    }

    @Test
    fun ambiguousHalDoesNotWriteBothSlots() {
        val hal = "VoiceMMode1 (*) and VoiceMMode2 (*) both registered"
        val choice = VoiceTxUnmute.choose(livePcm, hal)
        assertEquals(1, choice.vsids.size)
        assertEquals(VoiceTxUnmute.MMODE1_VSID, choice.vsids.single())
        assertEquals("default-mmode1", choice.why)
    }

    @Test
    fun chosenVsidIsAlwaysAKnownMMode() {
        val samples = listOf("", "garbage", livePcm, "id=Voice state=RUNNING", "0", "session 0")
        for (sample in samples) {
            val choice = VoiceTxUnmute.choose(sample, sample)
            assertEquals(1, choice.vsids.size)
            assertTrue(
                choice.vsids.single() == VoiceTxUnmute.MMODE1_VSID ||
                    choice.vsids.single() == VoiceTxUnmute.MMODE2_VSID,
            )
        }
    }

    @Test
    fun retriesAreZeroOneSecondAndThreeSeconds() {
        assertEquals(listOf(0L, 1_000L, 3_000L), VoiceTxUnmute.RETRY_DELAYS_MS)
    }

    @Test
    fun unmuteTupleIsThreeValuesAndLeavesDeviceMuteAlone() {
        val cmd = VoiceTxUnmute.unmuteCommand(VoiceTxUnmute.MMODE1_VSID)
        assertEquals("tinymix 'Voice Tx Mute' 0 297816064 0", cmd)
        assertFalse(cmd.contains("Device Mute"))
        assertFalse(cmd.contains("Voice Rx"))
        val readback = VoiceTxUnmute.readbackCommand()
        assertEquals("tinymix 'Voice Tx Mute'", readback)
        assertFalse(readback.contains("Device Mute"))
    }

    @Test
    fun attemptLogCarriesArgvExitAndReadback() {
        val argv = VoiceTxUnmute.unmuteCommand(VoiceTxUnmute.MMODE1_VSID)
        val line = VoiceTxUnmute.attemptLine(
            "call-active",
            0,
            "default-mmode1",
            VoiceTxUnmute.MMODE1_VSID,
            argv,
            0,
            "ok\n",
        )
        assertTrue(line.contains("argv=tinymix 'Voice Tx Mute' 0 297816064 0"))
        assertTrue(line.contains("exit=0"))
        assertTrue(line.contains("out=ok"))
        assertTrue(line.contains("delayMs=0"))
        val read = VoiceTxUnmute.readbackLine("route-type-18", 3000, VoiceTxUnmute.readbackCommand(), 1, "(read error)")
        assertTrue(read.startsWith("voice tx unmute readback"))
        assertTrue(read.contains("argv=tinymix 'Voice Tx Mute'"))
        assertTrue(read.contains("exit=1"))
        assertTrue(read.contains("out=(read error)"))
    }

    @Test
    fun hintScriptsDoNotReadSubstreamStatus() {
        assertTrue(VoiceTxUnmute.PCM_LIST.contains("/proc/asound/pcm"))
        assertFalse(VoiceTxUnmute.PCM_LIST.contains("status"))
        assertFalse(VoiceTxUnmute.PCM_LIST.contains("state:"))
        assertFalse(VoiceTxUnmute.HAL_HINT.contains("sub*/status"))
        assertFalse(VoiceTxUnmute.HAL_HINT.contains("state:"))
        assertFalse(VoiceTxUnmute.PCM_LIST.contains("tinymix"))
        assertFalse(VoiceTxUnmute.HAL_HINT.contains("Voice Tx Mute"))
    }

    @Test
    fun eachTinymixStatementKeepsItsOwnExitAndStderr() {
        val parts = TinymixTrace.statements(
            "tinymix 'Incall_Music Audio Mixer MultiMedia9' 1 2>/dev/null; tinymix 'Voice Tx Mute' 0",
        )
        assertEquals(2, parts.size)
        val captured = TinymixTrace.captureCommand(parts[1])
        assertEquals("{ tinymix 'Voice Tx Mute' 0; } 2>&1", captured)
        assertFalse(TinymixTrace.captureCommand(parts[0]).contains("/dev/null"))
        val (code, body) = TinymixTrace.exitOf("Error: bad session\ntinymix_exit:1")
        assertEquals(1, code)
        assertEquals("Error: bad session", body)
        val line = TinymixTrace.line(parts[1], code, body)
        assertTrue(line.startsWith("tinymix write exit=1 cmd=tinymix 'Voice Tx Mute' 0"))
    }
}
