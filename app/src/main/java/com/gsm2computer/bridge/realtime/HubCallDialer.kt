package com.gsm2computer.bridge.realtime

import com.gsm2computer.bridge.DialRequest
import com.gsm2computer.bridge.DialResponse
import com.gsm2computer.bridge.DialStatus
import com.gsm2computer.bridge.HubEndpoints
import com.gsm2computer.bridge.classifyHubStatus
import com.gsm2computer.bridge.scrubSecrets
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicReference

/**
 * Dials exactly one [DialRequest]: `POST /token`, then the WebSocket upgrade,
 * both on that endpoint's origin. It does not try another endpoint.
 */
class HubCallDialer(
    private val http: OkHttpClient,
    private val hubOwned: Boolean,
    private val openAiTokenUrl: String,
    private val model: String,
    private val handshakeTimeoutMs: Long = 4_000,
) {
    @Volatile private var activeModel: String = model

    fun mintedModel(): String = activeModel

    interface Live {
        fun onOpen(webSocket: WebSocket, request: DialRequest)
        fun onMessage(webSocket: WebSocket, text: String)
        fun onDead(webSocket: WebSocket, code: Int, detail: String)
    }

    fun dial(request: DialRequest, live: Live): DialResponse {
        val secrets = listOf(request.endpoint.deviceToken)
        val minted = try {
            mint(request)
        } catch (failed: DialFailed) {
            return failed.response
        } catch (e: Exception) {
            return DialResponse(
                DialStatus.RETRYABLE,
                0,
                scrubSecrets(e.message ?: e.javaClass.simpleName, secrets),
            )
        }
        val wsUrl = HubEndpoints.connectUrl(
            HubEndpoints.webSocketUrl(request.endpoint.url),
            activeModel,
            hubOwned,
        )
        val builder = Request.Builder().url(wsUrl)
        if (minted.isNotBlank()) builder.header("Authorization", "Bearer $minted")
        for ((name, value) in HubEndpoints.hubHeaders(request.endpoint, request.sessionId)) {
            builder.header(name, value)
        }
        val gate = HandshakeGate()
        val failure = AtomicReference<DialResponse?>(null)
        val latch = CountDownLatch(1)
        val socket = http.newWebSocket(builder.build(), object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                if (!gate.onOpen()) {
                    webSocket.cancel()
                    return
                }
                latch.countDown()
                live.onOpen(webSocket, request)
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                live.onMessage(webSocket, text)
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                if (!gate.opened()) {
                    failure.set(
                        failureResponse(
                            response?.code ?: 0,
                            t.message ?: "websocket failure",
                            secrets,
                            request.sessionId,
                        ),
                    )
                    latch.countDown()
                    return
                }
                live.onDead(
                    webSocket,
                    response?.code ?: -1,
                    scrubSecrets(t.message ?: "websocket failure", secrets),
                )
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                if (!gate.opened()) {
                    failure.set(
                        failureResponse(
                            code,
                            reason.ifBlank { "websocket closed" },
                            secrets,
                            request.sessionId,
                        ),
                    )
                    latch.countDown()
                    return
                }
                live.onDead(webSocket, code, scrubSecrets(reason, secrets))
            }
        })
        if (!latch.await(handshakeTimeoutMs, TimeUnit.MILLISECONDS)) {
            val status = timeoutDecision(gate) { socket.cancel() }
            if (status == DialStatus.OPENED) return DialResponse(DialStatus.OPENED, 101, "")
            return failure.get() ?: DialResponse(DialStatus.RETRYABLE, 0, "handshake timeout")
        }
        if (gate.opened()) return DialResponse(DialStatus.OPENED, 101, "")
        return failure.get() ?: DialResponse(DialStatus.RETRYABLE, 0, "handshake ended")
    }

    private fun mint(request: DialRequest): String {
        val secrets = listOf(request.endpoint.deviceToken)
        val url = if (hubOwned) {
            HubEndpoints.tokenUrl(HubEndpoints.httpOrigin(request.endpoint.url), "")
        } else {
            openAiTokenUrl.trim()
        }
        if (url.isBlank()) {
            throw DialFailed(DialResponse(DialStatus.RETRYABLE, 0, "token url empty"))
        }
        val builder = Request.Builder().url(url).post(ByteArray(0).toRequestBody(null))
        for ((name, value) in HubEndpoints.hubHeaders(request.endpoint, request.sessionId)) {
            builder.header(name, value)
        }
        val call = http.newCall(builder.build())
        call.timeout().timeout(10, TimeUnit.SECONDS)
        call.execute().use { resp ->
            val body = resp.body?.string().orEmpty()
            if (!resp.isSuccessful) {
                throw DialFailed(
                    failureResponse(resp.code, body.take(160), secrets, request.sessionId),
                )
            }
            val value = jsonStringField(body, "value")
            if (value.isBlank()) {
                throw DialFailed(DialResponse(DialStatus.RETRYABLE, resp.code, "no token in response"))
            }
            jsonStringField(body, "model").takeIf { it.isNotBlank() }?.let { activeModel = it }
            return value
        }
    }

    /** Token mint is a flat object. Avoid org.json so the JVM release test can run this dialer. */
    private fun jsonStringField(body: String, key: String): String {
        val match = Regex(""""$key"\s*:\s*"((?:\\.|[^"\\])*)"""").find(body) ?: return ""
        return match.groupValues[1].replace("\\\"", "\"").replace("\\\\", "\\")
    }

    private fun failureResponse(
        code: Int,
        detail: String,
        secrets: List<String>,
        sessionId: String,
    ): DialResponse = DialResponse(
        classifyHubStatus(code, sessionId),
        code,
        scrubSecrets(detail, secrets),
    )

    private class DialFailed(val response: DialResponse) : Exception(response.detail)
}

/**
 * One handshake. [onOpen] and [abandon] cannot both win.
 * The loser of [onOpen] must not install the socket. The loser of [abandon]
 * means the socket is already open: [timeoutDecision] returns OPENED and
 * does not cancel it.
 */
internal class HandshakeGate {
    private val state = AtomicInteger(PENDING)

    fun onOpen(): Boolean = state.compareAndSet(PENDING, OPENED)

    fun abandon(): Boolean = state.compareAndSet(PENDING, FAILED)

    fun opened(): Boolean = state.get() == OPENED

    private companion object {
        const val PENDING = 0
        const val OPENED = 1
        const val FAILED = 2
    }
}

/** Timeout outcome. [cancel] runs only when this attempt is the one that failed. */
internal fun timeoutDecision(gate: HandshakeGate, cancel: () -> Unit): DialStatus {
    if (gate.opened()) return DialStatus.OPENED
    if (!gate.abandon()) return DialStatus.OPENED
    cancel()
    return DialStatus.RETRYABLE
}
