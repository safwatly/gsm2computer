package com.gsm2computer.bridge.sms

import com.gsm2computer.bridge.HubEndpoints
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test

/**
 * No radio and no handset. The poller acks only with this decision, after
 * the sent-intent callback or its timeout. result 40 is the Moto MODEM_ERR
 * that build 346 stored as sent.
 */
class SmsSendOutcomeTest {

    @Test
    fun okIsSentWithNoError() {
        val ack = SmsSendOutcome.decide(listOf(SmsSendOutcome.OK), timedOut = false)
        assertEquals("sent", ack.status)
        assertEquals("", ack.error)
        assertEquals("""{"status":"sent"}""", HubEndpoints.outboxAckJson(ack.status, ack.error))
    }

    @Test
    fun modemError40IsFailedAndNamesTheCode() {
        val ack = SmsSendOutcome.decide(listOf(40), timedOut = false, rpErrors = listOf(38))
        assertEquals("failed", ack.status)
        assertEquals("result=40 MODEM_ERR rp=38", ack.error)
        assertFalse(ack.status == "sent")
        val json = HubEndpoints.outboxAckJson(ack.status, ack.error)
        assertEquals("""{"status":"failed","error":"result=40 MODEM_ERR rp=38"}""", json)
    }

    @Test
    fun anyMultipartPartFailingFailsTheMessage() {
        val ack = SmsSendOutcome.decide(
            listOf(SmsSendOutcome.OK, 40),
            timedOut = false,
            rpErrors = listOf(null, 38),
        )
        assertEquals("failed", ack.status)
        assertEquals("part 1 ok; part 2 result=40 MODEM_ERR rp=38", ack.error)
    }

    @Test
    fun missingCallbackIsTimeoutNotSent() {
        val ack = SmsSendOutcome.decide(listOf(null), timedOut = true)
        assertEquals("timeout", ack.status)
        assertEquals("send result timeout", ack.error)
    }

    @Test
    fun partialCallbackWithNoFailureIsStillTimeout() {
        val ack = SmsSendOutcome.decide(listOf(SmsSendOutcome.OK, null), timedOut = true)
        assertEquals("timeout", ack.status)
    }

    @Test
    fun knownRadioFailuresStayFailed() {
        assertEquals("failed", SmsSendOutcome.decide(listOf(1), timedOut = false).status)
        assertEquals(
            "result=4 NO_SERVICE",
            SmsSendOutcome.decide(listOf(4), timedOut = false).error,
        )
    }
}
