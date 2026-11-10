package com.gsm2computer.bridge.realtime

import com.gsm2computer.bridge.DialRequest
import com.gsm2computer.bridge.DialResponse
import com.gsm2computer.bridge.DialStatus
import com.gsm2computer.bridge.EndpointRace
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
    private val handshakeTimeoutMs: Long = ATTEMPT_TIMEOUT_MS,
) {
    @Volatile private var activeModel: String = model

    /**
     * Test seam: OkHttp thread, inside this leg's [onOpen], before the
     * ownership decision. The leg has not touched [Live].
     */
    internal var beforeOwnSocket: ((LegOwnership) -> Unit)? = null

    /**
     * Test seam: dial thread, after the attempt budget, before [LegOwnership.lose].
     * A 101 that wins the decision while this runs is the result [dial] returns.
     */
    internal var onAttemptTimeout: (() -> Unit)? = null

    /**
     * Test seam: OkHttp thread, after this leg has claimed the race and before
     * [Live.onOpen]. Hangup here is the window where the socket is not yet `ws`.
     */
    internal var onClaimed: (() -> Unit)? = null

    fun mintedModel(): String = activeModel

    interface Live {
        fun onOpen(webSocket: WebSocket, request: DialRequest)
        fun onMessage(webSocket: WebSocket, text: String)
        fun onDead(webSocket: WebSocket, code: Int, detail: String)
    }

    fun dial(request: DialRequest, live: Live): DialResponse {
        val secrets = listOf(request.endpoint.deviceToken)
        val race = request.race
        if (race != null && race.abandoned(request.endpoint.url)) {
            return DialResponse(DialStatus.RETRYABLE, 0, "lost race")
        }
        val legId = race?.begin(request.endpoint.url)
        val startedNs = System.nanoTime()
        fun remainingMs(): Long {
            val spent = (System.nanoTime() - startedNs) / 1_000_000L
            return (handshakeTimeoutMs - spent).coerceAtLeast(1L)
        }
        val minted = try {
            mint(request, legId) { remainingMs() }
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
        if (race != null && race.abandoned(request.endpoint.url)) {
            return DialResponse(DialStatus.RETRYABLE, 0, "lost race")
        }
        val leg = Leg(request, live, secrets, legId)
        if (legId != null) race.track(legId) { leg.socketRef.get()?.cancel() }
        val socket = http.newWebSocket(builder.build(), leg)
        leg.socketRef.compareAndSet(null, socket)
        if (race != null && race.abandoned(request.endpoint.url)) {
            socket.cancel()
        }
        return leg.await(remainingMs())
    }

    /**
     * One entrance's socket. Nothing here is shared with a sibling leg.
     * [Live] is called only after [LegOwnership.win] has claimed the race.
     */
    private inner class Leg(
        private val request: DialRequest,
        private val live: Live,
        private val secrets: List<String>,
        legId: Long?,
    ) : WebSocketListener() {
        val socketRef = AtomicReference<WebSocket?>(null)
        private val ownership = LegOwnership(request.race, legId)
        private val failure = AtomicReference<DialResponse?>(null)
        private val latch = CountDownLatch(1)

        fun await(timeoutMs: Long): DialResponse {
            if (!latch.await(timeoutMs, TimeUnit.MILLISECONDS)) {
                onAttemptTimeout?.invoke()
                if (!ownership.lose()) return DialResponse(DialStatus.OPENED, 101, "")
                socketRef.get()?.cancel()
                return failure.get() ?: DialResponse(DialStatus.RETRYABLE, 0, "handshake timeout")
            }
            if (ownership.hasWon()) return DialResponse(DialStatus.OPENED, 101, "")
            return failure.get() ?: DialResponse(DialStatus.RETRYABLE, 0, "handshake ended")
        }

        override fun onOpen(webSocket: WebSocket, response: Response) {
            socketRef.compareAndSet(null, webSocket)
            try {
                beforeOwnSocket?.invoke(ownership)
                val owned = ownership.win(
                    install = { live.onOpen(webSocket, request) },
                    onClaimed = onClaimed,
                )
                if (!owned) {
                    note(DialResponse(DialStatus.RETRYABLE, 0, lostDetail()))
                    webSocket.cancel()
                }
            } finally {
                latch.countDown()
            }
        }

        override fun onMessage(webSocket: WebSocket, text: String) {
            if (!ownership.hasWon()) return
            live.onMessage(webSocket, text)
        }

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
            if (!ownership.hasWon()) {
                val responseCode = response?.code ?: 0
                val failed = failureResponse(
                    responseCode,
                    t.message ?: "websocket failure",
                    secrets,
                    request.sessionId,
                )
                // A failed upgrade can still carry an HTTP 2xx. That is not a win.
                note(
                    if (failed.status == DialStatus.OPENED) {
                        failed.copy(status = DialStatus.RETRYABLE)
                    } else {
                        failed
                    },
                )
                return
            }
            live.onDead(
                webSocket,
                response?.code ?: -1,
                scrubSecrets(t.message ?: "websocket failure", secrets),
            )
        }

        override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
            try {
                webSocket.close(code, reason.ifBlank { "closed" })
            } catch (_: Exception) {
            }
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
            if (!ownership.hasWon()) {
                note(
                    failureResponse(
                        code,
                        reason.ifBlank { "websocket closed" },
                        secrets,
                        request.sessionId,
                    ),
                )
                return
            }
            live.onDead(webSocket, code, scrubSecrets(reason, secrets))
        }

        private fun lostDetail(): String {
            val winner = request.race?.winner()
            return if (winner != null && winner != request.endpoint.url) "lost race" else "handshake timeout"
        }

        private fun note(response: DialResponse) {
            failure.compareAndSet(null, response)
            latch.countDown()
        }
    }

    private fun mint(request: DialRequest, legId: Long?, remainingMs: () -> Long): String {
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
        if (legId != null) request.race?.track(legId) { call.cancel() }
        call.timeout().timeout(remainingMs(), TimeUnit.MILLISECONDS)
        call.execute().use { resp ->
            val body = resp.body?.string().orEmpty()
            if (!resp.isSuccessful) {
                val rejected = HubEndpoints.missingTokenRejection(
                    request.endpoint.url,
                    resp.code,
                    request.endpoint.deviceToken.isBlank(),
                )
                throw DialFailed(
                    failureResponse(
                        resp.code,
                        rejected ?: body.take(160),
                        secrets,
                        request.sessionId,
                    ),
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
 * One leg's handshake and [EndpointRace.claim], as one decision.
 * [win] publishes the win under the lock, then installs the socket before it
 * returns. [lose] is the attempt budget. The two cannot disagree: a timeout
 * that returns after [win] observes the win, and a [win] that arrives after
 * [lose] does not install.
 *
 * Sibling [EndpointRace.cancelLosers] and [install] run after the lock is
 * released. The win flag is already visible, so the attempt budget cannot
 * report a timeout for a leg that is about to install, and a sibling
 * `onFailure` does not need this leg's lock.
 */
internal class LegOwnership(
    private val race: EndpointRace?,
    private val leg: Long?,
) {
    private val lock = Any()
    private var won = false
    private var lost = false

    fun hasWon(): Boolean = synchronized(lock) { won }

    fun win(install: () -> Unit, onClaimed: (() -> Unit)? = null): Boolean {
        synchronized(lock) {
            if (lost) return false
            if (won) return true
            if (race != null && (leg == null || !race.claim(leg))) {
                lost = true
                return false
            }
            won = true
        }
        race?.cancelLosers()
        onClaimed?.invoke()
        install()
        return true
    }

    /** True when this caller lost and should cancel. False when [win] already installed. */
    fun lose(): Boolean {
        synchronized(lock) {
            if (won) return false
            lost = true
            return true
        }
    }
}

/** One attempt covers the token POST and the websocket upgrade together. */
internal const val ATTEMPT_TIMEOUT_MS = 6_000L
