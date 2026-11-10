package com.gsm2computer.bridge.diag

import android.telecom.Call
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class HubRetryTest {

    @Test
    fun offHookKeepsTheCappedBackoffAfterTheGraceWindow() {
        val grace = HubLinkGrace(graceMs = 1_000L, initialDelayMs = 100L, maxDelayMs = 400L)
        grace.markLost(0L)
        assertEquals(100L, HubRetry.delayWhileOffHook(grace, 0L, offHook = true))
        assertNull(grace.retryDelayMs(1_000L))
        assertEquals(400L, HubRetry.delayWhileOffHook(grace, 1_000L, offHook = true))
        assertEquals(400L, HubRetry.delayWhileOffHook(grace, 90_000L, offHook = true))
        assertNull(HubRetry.delayWhileOffHook(grace, 500L, offHook = false))
    }

    @Test
    fun zeroGraceDoesNotRetryEvenWhileOffHook() {
        val grace = HubLinkGrace(graceMs = 0L, initialDelayMs = 1_000L, maxDelayMs = 8_000L)
        grace.markLost(0L)
        assertNull(HubRetry.delayWhileOffHook(grace, 0L, offHook = true))
    }

    @Test
    fun onlyDisconnectStatesStopRetries() {
        assertEquals(HubRetry.STATE_DISCONNECTED, Call.STATE_DISCONNECTED)
        assertEquals(HubRetry.STATE_DISCONNECTING, Call.STATE_DISCONNECTING)
        assertTrue(HubRetry.keepsRetrying(Call.STATE_ACTIVE))
        assertTrue(HubRetry.keepsRetrying(Call.STATE_DIALING))
        assertTrue(HubRetry.keepsRetrying(Call.STATE_HOLDING))
        assertFalse(HubRetry.keepsRetrying(Call.STATE_DISCONNECTED))
        assertFalse(HubRetry.keepsRetrying(Call.STATE_DISCONNECTING))
    }

    @Test
    fun holdOnlyWhileTheTransportIsStillRetrying() {
        assertTrue(HubRetry.holdLink(stillRetrying = true, callState = Call.STATE_ACTIVE))
        assertTrue(HubRetry.holdLink(stillRetrying = true, callState = Call.STATE_DIALING))
        assertFalse(HubRetry.holdLink(stillRetrying = false, callState = Call.STATE_ACTIVE))
        assertFalse(HubRetry.holdLink(stillRetrying = false, callState = Call.STATE_DIALING))
        assertFalse(HubRetry.holdLink(stillRetrying = true, callState = null))
        assertFalse(HubRetry.holdLink(stillRetrying = true, callState = Call.STATE_DISCONNECTED))
        assertFalse(HubRetry.holdLink(stillRetrying = true, callState = Call.STATE_DISCONNECTING))
    }

    @Test
    fun giveUpAndAnUnreadableStateDoNotHoldTheCall() {
        val grace = HubLinkGrace(graceMs = 1_000L, initialDelayMs = 100L, maxDelayMs = 400L)
        grace.markLost(0L)
        val whileDialing = HubRetry.delayWhileOffHook(grace, 0L, offHook = true)
        assertEquals(100L, whileDialing)
        assertTrue(HubRetry.holdLink(stillRetrying = true, callState = Call.STATE_ACTIVE))
        val afterWindow = HubRetry.delayWhileOffHook(grace, 5_000L, offHook = true)
        assertEquals(400L, afterWindow)
        assertTrue(HubRetry.holdLink(stillRetrying = true, callState = Call.STATE_HOLDING))
        assertNull(HubRetry.delayWhileOffHook(grace, 5_000L, offHook = false))
        assertFalse(HubRetry.holdLink(stillRetrying = false, callState = Call.STATE_ACTIVE))
        assertFalse(HubRetry.holdLink(stillRetrying = true, callState = null))
        val stopped = HubLinkGrace(graceMs = 0L, initialDelayMs = 1_000L, maxDelayMs = 8_000L)
        stopped.markLost(0L)
        assertNull(HubRetry.delayWhileOffHook(stopped, 0L, offHook = true))
        assertFalse(HubRetry.holdLink(stillRetrying = false, callState = Call.STATE_ACTIVE))
    }
}
