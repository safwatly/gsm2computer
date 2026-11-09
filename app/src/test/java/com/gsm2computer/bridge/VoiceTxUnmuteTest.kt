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
    fun runningVoiceMMode1StarMapsToTheCall1VsidAndNotMMode2() {
        val mmode1 = VoiceTxUnmute.idFromPcmListLine(livePcm.lines()[0])!!
        val voip = VoiceTxUnmute.idFromPcmListLine(livePcm.lines()[1])!!
        val mmode2 = VoiceTxUnmute.idFromPcmListLine(livePcm.lines()[2])!!
        val mm9 = VoiceTxUnmute.idFromPcmListLine(livePcm.lines()[3])!!
        val probe = VoiceTxUnmute.parseProbe(
            """
            id=$mmode1 state=RUNNING
            id=$voip state=RUNNING
            id=$mmode2 state=closed
            id=$mm9 state=RUNNING
            """.trimIndent(),
        )
        assertEquals(1, probe.active.size)
        assertEquals("VoiceMMode1 (*)", probe.active[0].id)
        assertEquals(0x11C05000L, probe.active[0].vsid)
        assertEquals(297816064L, probe.active[0].vsid)
        assertEquals(listOf("VoIP (*)", "MultiMedia9 (*)"), probe.runningUnknown)
        assertEquals(0x11C05000L, VoiceTxUnmute.match(mmode1)!!.vsid)
        assertEquals(0x11DC5000L, VoiceTxUnmute.match(mmode2)!!.vsid)
        assertNull(VoiceTxUnmute.match(voip))
        assertNull(VoiceTxUnmute.match(mm9))
    }

    @Test
    fun shorterVoicePrefixDoesNotStealVoiceMMode1OrVoice2() {
        assertEquals(0x11C05000L, VoiceTxUnmute.match("VoiceMMode1 (*)")!!.vsid)
        assertEquals(0x10DC1000L, VoiceTxUnmute.match("Voice2 (*)")!!.vsid)
        assertEquals(0x10C01000L, VoiceTxUnmute.match("Voice (*)")!!.vsid)
        assertFalse(VoiceTxUnmute.match("VoiceMMode2 (*)")!!.vsid == 0x11C05000L)
    }

    @Test
    fun aClosedSessionIsNotUnmuted() {
        val probe = VoiceTxUnmute.parseProbe(
            "id=VoiceMMode1 (*) state=OPEN\nid=VoiceMMode2 (*) state=closed",
        )
        assertTrue(probe.active.isEmpty())
        val line = VoiceTxUnmute.summary(probe)
        assertTrue(line.contains("chosen=none"))
        assertTrue(line.contains("pcm='VoiceMMode1 (*)' state=OPEN"))
    }

    @Test
    fun voiceMMode2UsesItsOwnVsid() {
        val probe = VoiceTxUnmute.parseProbe("id=VoiceMMode2 (*) state=RUNNING")
        assertEquals(0x11DC5000L, probe.active.single().vsid)
        assertTrue(VoiceTxUnmute.summary(probe).contains("chosen=VoiceMMode2 (*) vsid=${0x11DC5000L}"))
    }

    @Test
    fun unmuteTupleIsThreeValuesAndLeavesDeviceMuteAlone() {
        val cmd = VoiceTxUnmute.unmuteCommand(0x11C05000)
        assertEquals("tinymix 'Voice Tx Mute' 0 297816064 0", cmd)
        assertFalse(cmd.contains("Device Mute"))
        assertFalse(cmd.contains("Voice Rx"))
    }

    @Test
    fun probeReadsIdNotName() {
        assertFalse(VoiceTxUnmute.probeScript.contains("tinymix"))
        assertFalse(VoiceTxUnmute.probeScript.contains("Voice Tx Mute"))
        assertTrue(VoiceTxUnmute.probeScript.contains("$(sed -n 's/^id: //p'"))
        assertFalse(VoiceTxUnmute.probeScript.contains("s/^name:"))
        assertTrue(VoiceTxUnmute.probeScript.contains("echo \"id=\${id} state="))
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
