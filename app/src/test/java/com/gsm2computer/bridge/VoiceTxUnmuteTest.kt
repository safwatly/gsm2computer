package com.gsm2computer.bridge

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class VoiceTxUnmuteTest {
    @Test
    fun runningVoiceMMode1BecomesTheCall1Vsid() {
        val probe = VoiceTxUnmute.parseProbe(
            """
            name=VoiceMMode1 state=RUNNING
            name=VoiceMMode2 state=closed
            name=MultiMedia9 state=RUNNING
            """.trimIndent(),
        )
        assertEquals(1, probe.active.size)
        assertEquals("VoiceMMode1", probe.active[0].name)
        assertEquals(0x11C05000L, probe.active[0].vsid)
        assertEquals(297816064L, probe.active[0].vsid)
        assertEquals(listOf("MultiMedia9"), probe.runningUnknown)
    }

    @Test
    fun aClosedSessionIsNotUnmuted() {
        val probe = VoiceTxUnmute.parseProbe("name=VoiceMMode1 state=OPEN\nname=VoiceMMode2 state=closed")
        assertTrue(probe.active.isEmpty())
    }

    @Test
    fun voiceMMode2UsesItsOwnVsid() {
        val probe = VoiceTxUnmute.parseProbe("name=VoiceMMode2 state=RUNNING")
        assertEquals(0x11DC5000L, probe.active.single().vsid)
    }

    @Test
    fun unmuteTupleIsThreeValuesAndLeavesDeviceMuteAlone() {
        val cmd = VoiceTxUnmute.unmuteCommand(0x11C05000)
        assertEquals("tinymix 'Voice Tx Mute' 0 297816064 0", cmd)
        assertFalse(cmd.contains("Device Mute"))
        assertFalse(cmd.contains("Voice Rx"))
    }

    @Test
    fun probeDoesNotWriteAMixerControl() {
        assertFalse(VoiceTxUnmute.probeScript.contains("tinymix"))
        assertFalse(VoiceTxUnmute.probeScript.contains("Voice Tx Mute"))
        assertTrue(VoiceTxUnmute.probeScript.contains("$(sed -n 's/^name: //p'"))
        assertTrue(VoiceTxUnmute.probeScript.contains("echo \"name=\${name} state="))
    }

    @Test
    fun eachTinymixStatementKeepsItsOwnExit() {
        val parts = TinymixTrace.statements(
            "tinymix 'Incall_Music Audio Mixer MultiMedia9' 1 2>/dev/null; tinymix 'Voice Tx Mute' 0",
        )
        assertEquals(2, parts.size)
        val (code, body) = TinymixTrace.exitOf("Error: bad session\ntinymix_exit:1")
        assertEquals(1, code)
        assertEquals("Error: bad session", body)
        val line = TinymixTrace.line(parts[1], code, body)
        assertTrue(line.startsWith("tinymix write exit=1 cmd=tinymix 'Voice Tx Mute' 0"))
    }
}
