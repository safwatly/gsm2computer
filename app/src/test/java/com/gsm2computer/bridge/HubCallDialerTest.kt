package com.gsm2computer.bridge

import com.gsm2computer.bridge.realtime.HandshakeGate
import com.gsm2computer.bridge.realtime.HubCallDialer
import com.gsm2computer.bridge.realtime.timeoutDecision
import okhttp3.OkHttpClient
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.IOException
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicReference

class HubCallDialerTest {

    private val secret = "device-token-test-value"

    @Test
    fun tokenAndWebsocketCarryTheDeviceTokenAndSession() {
        val server = MockWebServer()
        server.start()
        try {
            server.enqueue(MockResponse().setBody("""{"value":"hub-token","model":"hub"}"""))
            val opened = AtomicBoolean(false)
            server.enqueue(MockResponse().withWebSocketUpgrade(object : WebSocketListener() {
                override fun onOpen(webSocket: WebSocket, response: Response) {
                    opened.set(true)
                }
            }))
            val dialer = dialer()
            val response = dialer.dial(
                DialRequest(HubEndpoint(server.url("/").toString(), secret), "hub-session"),
                object : HubCallDialer.Live {
                    override fun onOpen(webSocket: WebSocket, request: DialRequest) = Unit
                    override fun onMessage(webSocket: WebSocket, text: String) = Unit
                    override fun onDead(webSocket: WebSocket, code: Int, detail: String) = Unit
                },
            )
            assertEquals(DialStatus.OPENED, response.status)
            assertFalse(response.detail.contains(secret))
            val tokenCall = checkNotNull(server.takeRequest(2, TimeUnit.SECONDS))
            val socketCall = checkNotNull(server.takeRequest(2, TimeUnit.SECONDS))
            assertTrue(tokenCall.path!!.endsWith("/token"))
            assertEquals(secret, tokenCall.getHeader(HubEndpoints.DEVICE_TOKEN_HEADER))
            assertEquals("hub-session", tokenCall.getHeader(HubEndpoints.CALL_SESSION_HEADER))
            assertEquals(secret, socketCall.getHeader(HubEndpoints.DEVICE_TOKEN_HEADER))
            assertEquals("hub-session", socketCall.getHeader(HubEndpoints.CALL_SESSION_HEADER))
            assertEquals("Bearer hub-token", socketCall.getHeader("Authorization"))
            assertTrue(socketCall.path == "/" || socketCall.path == "")
            assertTrue(opened.get())
        } finally {
            server.shutdown()
        }
    }

    @Test
    fun rejectedTokenIsScrubbedAndDoesNotOpenTheSocket() {
        val server = MockWebServer()
        server.start()
        try {
            server.enqueue(MockResponse().setResponseCode(401).setBody(secret))
            val dialer = dialer()
            val response = dialer.dial(
                DialRequest(HubEndpoint(server.url("/").toString(), secret), ""),
                object : HubCallDialer.Live {
                    override fun onOpen(webSocket: WebSocket, request: DialRequest) = Unit
                    override fun onMessage(webSocket: WebSocket, text: String) = Unit
                    override fun onDead(webSocket: WebSocket, code: Int, detail: String) = Unit
                },
            )
            assertEquals(DialStatus.RETRYABLE, response.status)
            assertEquals(401, response.httpStatus)
            assertFalse(response.detail.contains(secret))
            assertEquals(1, server.requestCount)
        } finally {
            server.shutdown()
        }
    }

    @Test
    fun conflictWithoutASessionStopsAndConflictWithASessionWaits() {
        val server = MockWebServer()
        server.start()
        try {
            server.enqueue(MockResponse().setResponseCode(409).setBody("busy"))
            val dialer = dialer()
            val fresh = dialer.dial(
                DialRequest(HubEndpoint(server.url("/").toString(), secret), ""),
                quietLive(),
            )
            assertEquals(DialStatus.STOP, fresh.status)
            server.enqueue(MockResponse().setResponseCode(409).setBody("grace"))
            val relink = dialer.dial(
                DialRequest(HubEndpoint(server.url("/").toString(), secret), "hub-live"),
                quietLive(),
            )
            assertEquals(DialStatus.LATER, relink.status)
            assertFalse(relink.detail.contains(secret))
        } finally {
            server.shutdown()
        }
    }

    @Test
    fun timeoutCancelsOnlyWhenAbandonWins() {
        val won = HandshakeGate()
        assertTrue(won.onOpen())
        var cancelled = false
        assertEquals(DialStatus.OPENED, timeoutDecision(won) { cancelled = true })
        assertFalse(cancelled)

        val lost = HandshakeGate()
        var cancels = 0
        assertEquals(DialStatus.RETRYABLE, timeoutDecision(lost) { cancels += 1 })
        assertEquals(1, cancels)
    }

    @Test
    fun abandonAfterOpenKeepsTheSocketAndALateOpenIsIgnored() {
        val gate = HandshakeGate()
        assertTrue(gate.onOpen())
        assertFalse(gate.abandon())
        assertTrue(gate.opened())

        val failed = HandshakeGate()
        assertTrue(failed.abandon())
        assertFalse(failed.onOpen())
        assertFalse(failed.opened())
    }

    @Test
    fun handshakeThatOpensAfterCancelDoesNotInstallTheSocket() {
        val server = MockWebServer()
        server.start()
        try {
            server.enqueue(MockResponse().setBody("""{"value":"hub-token","model":"hub"}"""))
            server.enqueue(
                MockResponse()
                    .setHeadersDelay(2, TimeUnit.SECONDS)
                    .withWebSocketUpgrade(object : WebSocketListener() {}),
            )
            val installed = AtomicBoolean(false)
            val callFailed = CountDownLatch(1)
            val dialer = HubCallDialer(
                http = OkHttpClient.Builder()
                    .connectTimeout(2, TimeUnit.SECONDS)
                    .readTimeout(2, TimeUnit.SECONDS)
                    .addInterceptor { chain ->
                        try {
                            chain.proceed(chain.request())
                        } catch (e: IOException) {
                            if (chain.call().isCanceled()) callFailed.countDown()
                            throw e
                        }
                    }
                    .build(),
                hubOwned = true,
                openAiTokenUrl = "",
                model = "hub",
                handshakeTimeoutMs = 200,
            )
            val response = dialer.dial(
                DialRequest(HubEndpoint(server.url("/").toString(), secret), "hub-session"),
                object : HubCallDialer.Live {
                    override fun onOpen(webSocket: WebSocket, request: DialRequest) {
                        installed.set(true)
                    }
                    override fun onMessage(webSocket: WebSocket, text: String) = Unit
                    override fun onDead(webSocket: WebSocket, code: Int, detail: String) = Unit
                },
            )
            assertEquals(DialStatus.RETRYABLE, response.status)
            assertTrue(callFailed.await(2, TimeUnit.SECONDS))
            Thread.sleep(2_500)
            assertFalse(installed.get())
            assertFalse(response.detail.contains(secret))
        } finally {
            server.shutdown()
        }
    }

    @Test
    fun timeoutWinsTheGateBeforeOnOpenAndDoesNotCancelTheSibling() {
        val server = MockWebServer()
        server.start()
        try {
            server.enqueue(MockResponse().setBody("""{"value":"hub-token","model":"hub"}"""))
            server.enqueue(MockResponse().withWebSocketUpgrade(object : WebSocketListener() {}))
            val siblingCancelled = AtomicBoolean(false)
            val race = EndpointRace()
            race.track("ws://100.113.222.47:8787") { siblingCancelled.set(true) }
            val installed = AtomicBoolean(false)
            val dialer = HubCallDialer(
                http = OkHttpClient.Builder()
                    .connectTimeout(2, TimeUnit.SECONDS)
                    .readTimeout(2, TimeUnit.SECONDS)
                    .build(),
                hubOwned = true,
                openAiTokenUrl = "",
                model = "hub",
                handshakeTimeoutMs = 3_000,
            )
            dialer.beforeOwnSocket = { gate ->
                val status = AtomicReference<DialStatus?>(null)
                val abandoned = CountDownLatch(1)
                Thread {
                    status.set(timeoutDecision(gate) { })
                    abandoned.countDown()
                }.apply { isDaemon = true }.start()
                assertTrue(abandoned.await(2, TimeUnit.SECONDS))
                assertEquals(DialStatus.RETRYABLE, status.get())
            }
            val response = dialer.dial(
                DialRequest(HubEndpoint(server.url("/").toString(), secret), "hub-session", race),
                object : HubCallDialer.Live {
                    override fun onOpen(webSocket: WebSocket, request: DialRequest) {
                        installed.set(true)
                    }
                    override fun onMessage(webSocket: WebSocket, text: String) = Unit
                    override fun onDead(webSocket: WebSocket, code: Int, detail: String) = Unit
                },
            )
            assertEquals(DialStatus.RETRYABLE, response.status)
            assertEquals("handshake timeout", response.detail)
            assertFalse(installed.get())
            assertFalse(siblingCancelled.get())
            assertNull(race.winner())
        } finally {
            server.shutdown()
        }
    }

    @Test
    fun parallelEntrancesTakeTheFirstOpenInsideOneAttemptBudget() {
        val slow = MockWebServer()
        val fast = MockWebServer()
        slow.start()
        fast.start()
        try {
            val token = MockResponse().setBody("""{"value":"hub-token","model":"hub"}""")
            slow.enqueue(token)
            slow.enqueue(
                MockResponse()
                    .setHeadersDelay(2, TimeUnit.SECONDS)
                    .withWebSocketUpgrade(object : WebSocketListener() {}),
            )
            fast.enqueue(MockResponse().setBody("""{"value":"hub-token","model":"hub"}"""))
            fast.enqueue(MockResponse().withWebSocketUpgrade(object : WebSocketListener() {}))
            val installed = AtomicInteger(0)
            val dialer = dialer(handshakeTimeoutMs = 1_500)
            val routine = HubConnectionRoutine(
                listOf(
                    HubEndpoint(slow.url("/").toString(), secret),
                    HubEndpoint(fast.url("/").toString(), secret),
                ),
            ) { request ->
                dialer.dial(request, object : HubCallDialer.Live {
                    override fun onOpen(webSocket: WebSocket, request: DialRequest) {
                        installed.incrementAndGet()
                    }
                    override fun onMessage(webSocket: WebSocket, text: String) = Unit
                    override fun onDead(webSocket: WebSocket, code: Int, detail: String) = Unit
                })
            }
            val started = System.currentTimeMillis()
            val result = routine.connectRacing()
            val elapsed = System.currentTimeMillis() - started
            assertTrue(result.opened)
            assertTrue(result.url.startsWith(fast.url("/").toString().substringBefore("/")))
            assertEquals(fast.url("/").toString(), result.url)
            assertEquals(1, installed.get())
            assertTrue("elapsed=$elapsed", elapsed < 1_200)
            Thread.sleep(500)
            assertEquals(1, installed.get())
        } finally {
            slow.shutdown()
            fast.shutdown()
        }
    }

    private fun dialer(handshakeTimeoutMs: Long = 3_000): HubCallDialer = HubCallDialer(
        http = OkHttpClient.Builder()
            .connectTimeout(2, TimeUnit.SECONDS)
            .readTimeout(2, TimeUnit.SECONDS)
            .build(),
        hubOwned = true,
        openAiTokenUrl = "",
        model = "hub",
        handshakeTimeoutMs = handshakeTimeoutMs,
    )

    private fun quietLive(): HubCallDialer.Live = object : HubCallDialer.Live {
        override fun onOpen(webSocket: WebSocket, request: DialRequest) = Unit
        override fun onMessage(webSocket: WebSocket, text: String) = Unit
        override fun onDead(webSocket: WebSocket, code: Int, detail: String) = Unit
    }
}
