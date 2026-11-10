package com.gsm2computer.bridge.realtime

import com.gsm2computer.bridge.HubEndpoint
import com.gsm2computer.bridge.rtp.MediaTransport
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Two real sockets on one [HubStreamClient]. The shared session is the
 * production [HubCallDialer.Live] inside openAndHold, not a stand-in dial closure.
 */
class HubStreamRaceTest {

    private val token = "device-token-test-value"

    @Test
    fun simultaneous101sInstallOneSocketAndDoNotHangUp() {
        val left = MockWebServer()
        val right = MockWebServer()
        val leftTrack = Track()
        val rightTrack = Track()
        left.start()
        right.start()
        val client = client(
            left.url("/").toString(),
            right.url("/").toString(),
        )
        val sink = RecordingSink()
        try {
            enqueueUpgrade(left, leftTrack)
            enqueueUpgrade(right, rightTrack)
            val arrived = CountDownLatch(2)
            client.beforeLegDecision = {
                arrived.countDown()
                assertTrue(arrived.await(3, TimeUnit.SECONDS))
            }
            client.start(sink)
            assertTrue(leftTrack.opened.await(3, TimeUnit.SECONDS))
            assertTrue(rightTrack.opened.await(3, TimeUnit.SECONDS))
            assertTrue(waitUntil(3_000) { client.linkState == "open" })
            val winner = client.currentLink().endpointUrl
            val winnerTrack = if (winner == left.url("/").toString()) leftTrack else rightTrack
            val loserTrack = if (winnerTrack === leftTrack) rightTrack else leftTrack
            assertTrue(winner == left.url("/").toString() || winner == right.url("/").toString())
            assertTrue(waitUntil(2_000) { loserTrack.closed.get() })
            loserTrack.socket?.close(1000, "hub hangup")
            Thread.sleep(250)
            assertEquals("open", client.linkState)
            assertFalse(winnerTrack.closed.get())
            assertTrue(sink.errors.isEmpty())
            assertFalse(sink.holds.contains(true))
            assertEquals(1, listOf(leftTrack, rightTrack).count { !it.closed.get() })
        } finally {
            client.stop()
            left.shutdown()
            right.shutdown()
        }
    }

    @Test
    fun a101AtTheTimeoutBoundaryIsHeldAndDoesNotHangUp() {
        val left = MockWebServer()
        val right = MockWebServer()
        val leftTrack = Track()
        val rightTrack = Track()
        left.start()
        right.start()
        val release = CountDownLatch(1)
        val client = client(
            left.url("/").toString(),
            right.url("/").toString(),
        )
        val sink = RecordingSink()
        try {
            holdUpgrade(left, leftTrack, release)
            holdUpgrade(right, rightTrack, release)
            client.attemptTimeoutMs = 400
            client.onAttemptTimeout = {
                release.countDown()
                waitUntil(3_000) { client.linkState == "open" }
            }
            client.start(sink)
            assertTrue(waitUntil(4_000) { client.linkState == "open" })
            val winner = client.currentLink().endpointUrl
            assertTrue(winner == left.url("/").toString() || winner == right.url("/").toString())
            val winnerTrack = if (winner == left.url("/").toString()) leftTrack else rightTrack
            val loserTrack = if (winnerTrack === leftTrack) rightTrack else leftTrack
            assertTrue(waitUntil(2_000) { loserTrack.closed.get() || loserTrack.opened.count > 0L })
            Thread.sleep(250)
            assertEquals("open", client.linkState)
            assertFalse(winnerTrack.closed.get())
            assertTrue(sink.errors.isEmpty())
            assertFalse(sink.holds.contains(true))
            assertEquals(1, listOf(leftTrack, rightTrack).count { it.opened.count == 0L && !it.closed.get() })
        } finally {
            release.countDown()
            client.stop()
            left.shutdown()
            right.shutdown()
        }
    }

    @Test
    fun aStoppedDialHangsUpInsteadOfRetrying() {
        val server = MockWebServer()
        server.start()
        server.enqueue(MockResponse().setResponseCode(409).setBody("busy"))
        val client = HubStreamClient(
            endpoints = listOf(HubEndpoint(server.url("/").toString(), token)),
            tokenUrl = "",
            model = "hub",
            voice = "marin",
            instructions = "",
            hubOwnedSession = true,
            linkGraceMs = 5_000,
        )
        val sink = RecordingSink()
        try {
            client.start(sink)
            assertTrue(waitUntil(3_000) { sink.errors.isNotEmpty() })
            Thread.sleep(400)
            assertEquals(1, server.requestCount)
            assertTrue(sink.errors.first().contains("409") || sink.errors.first().isNotBlank())
            assertFalse(sink.holds.contains(true))
        } finally {
            client.stop()
            server.shutdown()
        }
    }

    private fun client(left: String, right: String) = HubStreamClient(
        endpoints = listOf(HubEndpoint(left, token), HubEndpoint(right, token)),
        tokenUrl = "",
        model = "hub",
        voice = "marin",
        instructions = "",
        hubOwnedSession = true,
        linkGraceMs = 5_000,
    )

    private fun enqueueUpgrade(server: MockWebServer, track: Track) {
        server.enqueue(MockResponse().setBody("""{"value":"hub-token","model":"hub"}"""))
        server.enqueue(MockResponse().withWebSocketUpgrade(track))
    }

    private fun holdUpgrade(server: MockWebServer, track: Track, release: CountDownLatch) {
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                if (request.path?.contains("token") == true) {
                    return MockResponse().setBody("""{"value":"hub-token","model":"hub"}""")
                }
                release.await(3, TimeUnit.SECONDS)
                return MockResponse().withWebSocketUpgrade(track)
            }
        }
    }

    private fun waitUntil(timeoutMs: Long, ready: () -> Boolean): Boolean {
        val deadline = System.nanoTime() + timeoutMs * 1_000_000L
        while (System.nanoTime() < deadline) {
            if (ready()) return true
            Thread.sleep(15)
        }
        return ready()
    }

    private class Track : WebSocketListener() {
        val opened = CountDownLatch(1)
        val closed = AtomicBoolean(false)
        @Volatile var socket: WebSocket? = null

        override fun onOpen(webSocket: WebSocket, response: Response) {
            socket = webSocket
            opened.countDown()
        }

        override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
            closed.set(true)
            webSocket.close(code, reason)
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
            closed.set(true)
        }

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
            closed.set(true)
        }
    }

    private class RecordingSink : MediaTransport.Sink {
        val errors = CopyOnWriteArrayList<String>()
        val holds = CopyOnWriteArrayList<Boolean>()

        override fun onAudio(frame: ByteArray, format: String, rate: Int) = Unit
        override fun onFlushPlayback() = Unit
        override fun onStatus(msg: String) = Unit
        override fun onError(msg: String) {
            errors.add(msg)
        }
        override fun onLinkHold(holding: Boolean, detail: String) {
            holds.add(holding)
        }
    }
}
