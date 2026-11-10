package com.gsm2computer.bridge.bridge

import android.telecom.Call
import com.gsm2computer.bridge.HubEndpoint
import com.gsm2computer.bridge.realtime.HubStreamClient
import com.gsm2computer.bridge.rtp.MediaTransport
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Hangup must close the hub socket even when `call.state` throws.
 */
class CallOrchestratorHangupTest {

    @Test
    fun endedCallClosesTheHubSocketWhenCallStateThrows() {
        val server = MockWebServer()
        val track = Track()
        server.start()
        val client = HubStreamClient(
            endpoints = listOf(HubEndpoint(server.url("/").toString(), "device-token-test-value")),
            tokenUrl = "",
            model = "hub",
            voice = "marin",
            instructions = "",
            hubOwnedSession = true,
            linkGraceMs = 5_000,
        )
        try {
            server.enqueue(MockResponse().setBody("""{"value":"hub-token","model":"hub"}"""))
            server.enqueue(MockResponse().withWebSocketUpgrade(track))
            client.start(QuietSink())
            assertTrue(track.opened.await(3, TimeUnit.SECONDS))
            assertTrue(waitUntil(3_000) { client.linkState == "open" })

            val call = allocate(Call::class.java)
            val orchestrator = allocate(CallOrchestrator::class.java)
            orchestrator.readGsmState = { throw IllegalStateException("call.state") }
            setField(orchestrator, "activeGsmCall", call)
            setField(orchestrator, "bridgeState", CallOrchestrator.BridgeState.BRIDGED)
            setField(orchestrator, "activeClient", client)
            orchestrator.onGsmCallEnded(call)

            assertTrue(waitUntil(3_000) { track.closed.get() })
            assertEquals("closed", client.linkState)
        } finally {
            client.stop()
            server.shutdown()
        }
    }

    private fun <T> allocate(type: Class<T>): T {
        val unsafeClass = Class.forName("sun.misc.Unsafe")
        val theUnsafe = unsafeClass.getDeclaredField("theUnsafe")
        theUnsafe.isAccessible = true
        val unsafe = theUnsafe.get(null)
        val allocate = unsafeClass.getMethod("allocateInstance", Class::class.java)
        @Suppress("UNCHECKED_CAST")
        return allocate.invoke(unsafe, type) as T
    }

    private fun setField(target: Any, name: String, value: Any?) {
        val field = target.javaClass.getDeclaredField(name)
        field.isAccessible = true
        field.set(target, value)
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

        override fun onOpen(webSocket: WebSocket, response: Response) {
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

    private class QuietSink : MediaTransport.Sink {
        override fun onAudio(frame: ByteArray, format: String, rate: Int) = Unit
        override fun onFlushPlayback() = Unit
        override fun onStatus(msg: String) = Unit
        override fun onError(msg: String) = Unit
        override fun onLinkHold(holding: Boolean, detail: String) = Unit
    }
}
