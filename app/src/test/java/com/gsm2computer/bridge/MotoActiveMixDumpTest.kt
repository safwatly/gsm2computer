package com.gsm2computer.bridge

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class MotoActiveMixDumpTest {

    @Test
    fun `listing is read only and keeps voice and incall controls`() {
        assertEquals("tinymix", MotoActiveMixDump.listArgv())
        assertFalse(Regex("""\d""").containsMatchIn(MotoActiveMixDump.listArgv()))
        assertEquals(5_000L, MotoActiveMixDump.DELAY_MS)
        val dump = """
            12  INT  1  RX_RX0 Digital Volume                    79
            40  BOOL 1  MultiMedia1 Mixer VOC_REC_DL             On
            41  BOOL 1  Incall_Music Audio Mixer MultiMedia9     On
            42  BOOL 1  Incall_Music Audio Mixer MultiMedia1     Off
            43  INT  3  Voice Tx Mute                            (read error)
            44  BOOL 1  MultiMedia2 Mixer                        Off
            45  BOOL 1  aw87xxx_profile_switch_0                 Off
        """.trimIndent()
        val lines = MotoActiveMixDump.logLines(dump)
        assertTrue(lines.any { it.contains("VOC_REC_DL") })
        assertTrue(lines.any { it.contains("MultiMedia9") })
        assertTrue(lines.any { it.contains("Incall_Music Audio Mixer MultiMedia1") })
        assertTrue(lines.any { it.contains("Voice Tx Mute") })
        assertTrue(lines.all { it.startsWith("TINYMIX-SNAPSHOT:") })
        assertFalse(lines.any { it.contains("RX_RX0") })
        assertFalse(lines.any { it.contains("MultiMedia2 Mixer") })
        assertFalse(lines.any { it.contains("aw87xxx") })
        assertEquals(
            listOf("TINYMIX-SNAPSHOT: (no VOC/Voice/MultiMedia9/Incall controls)"),
            MotoActiveMixDump.logLines("RX_RX0 Digital Volume 79"),
        )
        assertTrue(MotoActiveMixDump.helperMismatch("stock").contains("helper-mismatch stock"))
    }
}
