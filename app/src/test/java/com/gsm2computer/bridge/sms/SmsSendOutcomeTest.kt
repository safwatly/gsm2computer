package com.gsm2computer.bridge.sms

import com.gsm2computer.bridge.HubEndpoints
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

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
    fun sentReceiverIsExplicitAndNotExported() {
        val manifest = listOf(File("src/main/AndroidManifest.xml"), File("app/src/main/AndroidManifest.xml"))
            .firstOrNull { it.isFile }
            ?.readText()
            ?: error("AndroidManifest.xml not found from ${File(".").absolutePath}")
        val receiver = Regex(
            """<receiver\b[^>]*android:name="\.sms\.SmsSentReceiver"[^>]*>""",
            setOf(RegexOption.DOT_MATCHES_ALL),
        ).find(manifest) ?: error("SmsSentReceiver is missing from the manifest")
        assertTrue(receiver.value.contains("""android:exported="false""""))
        assertFalse(receiver.value.contains("intent-filter"))
    }

    @Test
    fun sentHubRecordsTheResultCodeForTheMatchingPart() {
        val slot = SmsSentHub.register("bf07cf40", 2)
        SmsSentHub.record("bf07cf40", part = 1, resultCode = 40, rp = 38)
        assertEquals(40, slot.results[1])
        assertEquals(38, slot.rpErrors[1])
        assertEquals(1, slot.latch.count)
        SmsSentHub.record("someone-else", part = 0, resultCode = 1, rp = null)
        assertEquals(1, slot.latch.count)
        SmsSentHub.finish("bf07cf40")
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
