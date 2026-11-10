package com.gsm2computer.bridge

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class MotoMuteReadbackTest {

    @Test
    fun `reads pass no values so the helper prints the whole tuple`() {
        val tx = MotoMuteReadback.readArgv(MotoMuteReadback.VOICE_TX_MUTE)
        val rx = MotoMuteReadback.readArgv(MotoMuteReadback.VOICE_RX_DEVICE_MUTE)
        assertEquals("tinymix 'Voice Tx Mute'", tx)
        assertEquals("tinymix 'Voice Rx Device Mute'", rx)
        assertFalse(Regex("""\d""").containsMatchIn(tx))
        assertFalse(Regex("""\d""").containsMatchIn(rx))
        val script = MotoMuteReadback.script()
        assertTrue(script.contains("{ tinymix 'Voice Tx Mute'; } 2>&1"))
        assertTrue(script.contains("{ tinymix 'Voice Rx Device Mute'; } 2>&1"))
        assertFalse(Regex("""Mute'\s+\d""").containsMatchIn(script))
        assertFalse(script.contains("tinymix_exit"))
        val resolved = script.replace("tinymix", "/data/local/tmp/tinymix")
        assertTrue(resolved.contains("@@tx_exit:\$?"))
        assertTrue(resolved.contains("/data/local/tmp/tinymix 'Voice Tx Mute'"))
        assertFalse(resolved.contains("tinymix_exit"))
    }

    @Test
    fun `format keeps the raw three element tuple and a getter error`() {
        val raw = """
            @@tx
            Voice Tx Mute: 0 297816064 0
            @@tx_exit:0
            @@rx
            Error reading control: EPERM
            @@rx_exit:1
        """.trimIndent()
        val line = MotoMuteReadback.format(raw)
        assertEquals(
            "txMute=[exit=0 Voice Tx Mute: 0 297816064 0] " +
                "rxDevMute=[exit=1 Error reading control: EPERM]",
            line,
        )
        assertTrue(line.contains("0 297816064 0"))
        assertFalse(line.contains("tinymix write"))
        assertEquals(
            "txMute=[tinymix-missing] rxDevMute=[tinymix-missing]",
            MotoMuteReadback.missing(),
        )
    }
}
