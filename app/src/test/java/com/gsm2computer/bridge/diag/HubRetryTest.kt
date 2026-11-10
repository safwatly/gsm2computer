package com.gsm2computer.bridge.diag

import android.telecom.Call
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

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
    fun giveUpTearsDownAndAnUnreadableCallStateDoesNotHold() {
        val src = File("src/main/java/com/gsm2computer/bridge/bridge/CallOrchestrator.kt").readText()
        val timeout = src.substringAfter("override fun onRtpTimeout").substringBefore("override fun onRtpStats")
        assertTrue(timeout.contains("HubRetry.holdLink"))
        assertTrue(timeout.contains("isRetrying()"))
        assertTrue(timeout.indexOf("holdLink") < timeout.indexOf("tearDown("))
        val state = src.substringAfter("private fun gsmCallState").substringBefore("private fun tearDown")
        assertTrue(state.contains("return null"))
        assertFalse(state.contains("return true"))
        val client = File("src/main/java/com/gsm2computer/bridge/realtime/HubStreamClient.kt").readText()
        val fail = client.substringAfter("private fun fail(").substringBefore("private fun trace")
        assertTrue(fail.contains("retrying = false"))
        assertTrue(fail.indexOf("retrying = false") < fail.indexOf("onError"))
        val loop = client.substringAfter("private fun sessionLoop()").substringBefore("private fun openAndHold")
        assertTrue(loop.contains("retrying = true"))
        assertTrue(loop.indexOf("FATAL_PREFIX") < loop.indexOf("retrying = true"))
        assertTrue(client.contains("connectRacing"))
        assertTrue(client.contains("delayWhileOffHook"))
    }
}
