package com.gsm2computer.bridge

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class InjectPolicyLatchTest {

    @Test
    fun `hangup then late flow sample then setprop then next active applies the new policy`() {
        val latch = InjectPolicyLatch()
        var prop = "hal_owned"
        val first = latch.latch(prop)
        assertTrue(first.fresh)
        assertEquals(InjectPolicy.HAL_OWNED, first.policy)
        assertTrue(first.policy.opensVoiceCapture)
        assertTrue(latch.latched)

        // Hangup has started and the session can still emit the 15s AUDIO-FLOW sample.
        // The sample reads the latched policy and must not take the latch again.
        assertEquals("", latch.flowNote())
        assertEquals(InjectPolicy.HAL_OWNED, latch.peek())
        assertTrue(latch.latched)

        prop = "hal_owned_no_capture"
        val pinned = latch.latch(prop)
        assertFalse(pinned.fresh)
        assertEquals(InjectPolicy.HAL_OWNED, pinned.policy)
        assertEquals("", latch.flowNote())
        assertTrue(latch.latched)

        // The session has stopped. Clear even when hangup restore took the skip path.
        latch.clear()
        assertFalse(latch.latched)
        latch.flowNote()
        assertFalse(latch.latched)

        val second = latch.latch(prop)
        assertTrue(second.fresh)
        assertEquals(InjectPolicy.HAL_OWNED_NO_CAPTURE, second.policy)
        assertFalse(second.policy.opensVoiceCapture)
        assertEquals(
            "AUDIO-FLOW: capture disabled by policy=hal_owned_no_capture",
            latch.flowNote(),
        )
    }
}
