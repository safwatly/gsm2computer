package com.gsm2computer.bridge

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class VoiceTxDebounceTest {
    /** Epoch from the build 352 call whose call-active job never armed. */
    private val now = 1_794_312_519_326L

    @Test
    fun `Long MIN_VALUE subtraction overflows and the null stamp does not`() {
        @Suppress("INTEGER_OVERFLOW")
        val wrapped = now - Long.MIN_VALUE
        assertEquals(-9_223_370_242_542_256_482L, wrapped)
        assertTrue(wrapped < 500L)
        assertEquals(Long.MAX_VALUE, VoiceTxDebounce.elapsed(Long.MIN_VALUE, now))
        assertTrue(VoiceTxDebounce.allows(null, now, 500L))
        assertTrue(VoiceTxDebounce.elapsed(Long.MIN_VALUE, now) >= 500L)
    }

    @Test
    fun `first trigger runs and a repeat under 500 ms does not move the window`() {
        val gate = VoiceTxDebounce()
        assertTrue(gate.allow(VoiceTxMuteJob.CALL_ACTIVE, now, 500L))
        assertFalse(gate.allow(VoiceTxMuteJob.CALL_ACTIVE, now + 100L, 500L))
        assertFalse(gate.allow(VoiceTxMuteJob.CALL_ACTIVE, now + 499L, 500L))
        assertTrue(gate.allow(VoiceTxMuteJob.CALL_ACTIVE, now + 500L, 500L))
        assertEquals(499L, VoiceTxDebounce.elapsed(now, now + 499L))
        assertEquals(0L, VoiceTxDebounce.elapsed(now + 10L, now))
    }

    @Test
    fun `ended call drops the stamp so the next active trigger is not debounced`() {
        val gate = VoiceTxDebounce()
        assertTrue(gate.allow(VoiceTxMuteJob.CALL_ACTIVE, now, 500L))
        gate.forget(VoiceTxMuteJob.CALL_ACTIVE)
        assertTrue(gate.allow(VoiceTxMuteJob.CALL_ACTIVE, now + 100L, 500L))
        assertFalse(VoiceTxDebounce.allows(now, now + 100L, 500L))
    }
}
