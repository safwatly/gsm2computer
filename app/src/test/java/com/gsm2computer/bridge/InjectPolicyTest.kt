package com.gsm2computer.bridge

import android.media.AudioManager
import android.telecom.Call
import android.telephony.TelephonyManager
import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class InjectPolicyTest {

    @Test
    fun `blank and unknown values default to api mute`() {
        for (raw in listOf(null, "", "  ", "nope", "hal_owned_api")) {
            val policy = InjectPolicy.parse(raw)
            assertEquals(InjectPolicy.HAL_OWNED_API_MUTE, policy)
            assertEquals(InjectPolicy.DEFAULT, policy)
            assertFalse(policy.writesVoiceTxMute)
            assertEquals(MicApiAction.FORCE_TRUE, policy.micApi)
        }
        assertFalse(InjectPolicy.recognized("nope"))
        assertTrue(InjectPolicy.recognized(null))
        assertTrue(InjectPolicy.recognized(""))
        assertEquals(PolicyLogLevel.WARN, InjectPolicy.logLevel("nope"))
        assertEquals(PolicyLogLevel.INFO, InjectPolicy.logLevel(""))
        assertEquals(PolicyLogLevel.INFO, InjectPolicy.logLevel("hal_owned"))
    }

    @Test
    fun `hal_owned writes the session tuple and clears api mute`() {
        val policy = InjectPolicy.parse("hal_owned")
        assertEquals("hal_owned", policy.wire)
        assertTrue(policy.writesVoiceTxMute)
        assertEquals(MicApiAction.FORCE_FALSE, policy.micApi)
        assertEquals("set-false", policy.micApi.wire)
        assertTrue(InjectPolicy.recognized("HAL_OWNED"))
    }

    @Test
    fun `hal_owned_no_unmute leaves the mic and skips the tuple`() {
        val policy = InjectPolicy.parse(" hal_owned_no_unmute ")
        assertFalse(policy.writesVoiceTxMute)
        assertEquals(MicApiAction.LEAVE, policy.micApi)
        assertEquals("leave", policy.micApi.wire)
    }

    @Test
    fun `hal_owned_api_mute sets api mute and skips the tuple`() {
        val policy = InjectPolicy.parse("hal_owned_api_mute")
        assertEquals("hal_owned_api_mute", policy.wire)
        assertFalse(policy.writesVoiceTxMute)
        assertEquals(MicApiAction.FORCE_TRUE, policy.micApi)
        assertEquals("set-true", policy.micApi.wire)
    }

    @Test
    fun `idle MM1 and MM2 clear only runs for an idle moto`() {
        assertTrue(
            MotoIdleMixers.shouldClear(
                "Moto g84 (holi)",
                emptyList(),
                TelephonyManager.CALL_STATE_IDLE,
                AudioManager.MODE_NORMAL,
            ),
        )
        assertFalse(
            MotoIdleMixers.shouldClear(
                "Moto g84 (holi)",
                listOf(Call.STATE_ACTIVE),
                TelephonyManager.CALL_STATE_OFFHOOK,
                AudioManager.MODE_IN_CALL,
            ),
        )
        assertFalse(
            MotoIdleMixers.shouldClear(
                "Pixel 7 (Tensor G1)",
                emptyList(),
                TelephonyManager.CALL_STATE_IDLE,
                AudioManager.MODE_NORMAL,
            ),
        )
        assertTrue(MotoIdleMixers.CLEAR_CMD.contains("MultiMedia1' 0"))
        assertTrue(MotoIdleMixers.CLEAR_CMD.contains("MultiMedia2' 0"))
        assertFalse(MotoIdleMixers.CLEAR_CMD.contains("MultiMedia9"))
        assertFalse(MotoIdleMixers.READBACK_CMD.contains("MultiMedia9"))
        assertTrue(MotoIdleMixers.READBACK_CMD.contains("MultiMedia1"))
        assertTrue(MotoIdleMixers.READBACK_CMD.contains("MultiMedia2"))
        assertTrue(MotoIdleMixers.MUTE_PATH_CMD.contains("Voice Tx Device Mute"))
        assertTrue(MotoIdleMixers.MUTE_PATH_CMD.contains("Voice Tx Mute"))
        assertFalse(MotoIdleMixers.MUTE_PATH_CMD.contains("Mute' 0"))
        assertFalse(MotoIdleMixers.MUTE_PATH_CMD.contains("Mute' 1"))
    }

    @Test
    fun `phased injector stop does not reset Incall_Music`() {
        val source = File("src/main/java/com/gsm2computer/bridge/rtp/PhasedInjector.kt").readText()
        val stop = source.substringAfter("fun stop()").substringBefore("companion object")
        assertFalse(stop.contains("Incall_Music"))
    }
}
