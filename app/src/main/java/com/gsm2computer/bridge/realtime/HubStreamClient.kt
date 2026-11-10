package com.gsm2computer.bridge.realtime

import android.util.Base64
import android.util.Log
import com.gsm2computer.bridge.DialRequest
import com.gsm2computer.bridge.DialResponse
import com.gsm2computer.bridge.DialStatus
import com.gsm2computer.bridge.EndpointRace
import com.gsm2computer.bridge.HubConnectionRoutine
import com.gsm2computer.bridge.HubEndpoint
import com.gsm2computer.bridge.HubEndpoints
import com.gsm2computer.bridge.HubLink
import com.gsm2computer.bridge.diag.BridgeLog
import com.gsm2computer.bridge.diag.HubLinkGrace
import com.gsm2computer.bridge.diag.HubRetry
import com.gsm2computer.bridge.rtp.MediaTransport
import okhttp3.OkHttpClient
import okhttp3.WebSocket
import okio.ByteString.Companion.toByteString
import org.json.JSONObject
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference

/**
 * WebSocket transport for the GSM bridge.
 *
 * A mid-call socket failure does not end the GSM call while [offHook]
 * stays true. This client keeps retrying, with backoff, for that whole
 * time. [linkGraceMs] still shapes the backoff (1s, 2s, …, 8s). When that
 * window ends and the call is still off-hook, retries continue at the cap.
 * A null or throwing read counts as on-hook and the session stops, which
 * is what tears the bridge down. Close code 1000 is a real hub hangup and
 * is not retried. Silence is what the caller hears during the gap.
 *
 * Each connect races [endpoints] with [HubConnectionRoutine.connectRacing].
 * The first that opens is held until the socket dies. Uplink stays 16 kHz
 * s16le in a binary frame ([UplinkWire]). Downlink stays the JSON PCM the hub
 * already sends. Appends without format/rate stay 8 kHz μ-law.
 */
class HubStreamClient(
    private val endpoints: List<HubEndpoint> = emptyList(),
    private val tokenUrl: String,
    private val webSocketUrl: String = "",
    private val model: String,
    private val voice: String,
    private val instructions: String,
    private val hubOwnedSession: Boolean = false,
    private val linkGraceMs: Long = HubLinkGrace.DEFAULT_GRACE_MS,
    private val onTrace: (kind: String, fields: Map<String, Any?>) -> Unit = { _, _ -> },
    private val offHook: () -> Boolean = { true },
) : MediaTransport {

    private val http = OkHttpClient.Builder()
        .connectTimeout(ATTEMPT_TIMEOUT_MS, TimeUnit.MILLISECONDS)
        // Default write timeout is 10s. A blocked audio write then fails the
        // socket on its own, with no log of the queue that caused it.
        // Liveness is the 20s ping plus [WsSendBudget] cancelling a wedged queue.
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .writeTimeout(0, TimeUnit.MILLISECONDS)
        .pingInterval(20, TimeUnit.SECONDS)
        .build()

    private val grace = HubLinkGrace(graceMs = linkGraceMs)

    @Volatile private var ws: WebSocket? = null
    @Volatile private var sink: MediaTransport.Sink? = null
    @Volatile private var closed = false
    @Volatile private var inputFormat = "audio/pcm"
    @Volatile private var inputRate = 8000
    @Volatile private var outputFormat = "audio/pcm"
    @Volatile private var outputRate = 48000

    @Volatile var linkState: String = "idle"
        private set

    @Volatile var lastRttMs: Long = -1L
        private set

    @Volatile var sessionId: String = ""
        private set

    @Volatile private var linkEndpoint: String = ""
    @Volatile private var linkEntrance: String = ""
    @Volatile private var activeRace: EndpointRace? = null
    @Volatile private var loggedGraceHold = false

    /** True while a dropped link is waiting to dial again. Cleared on open, [fail], and [stop]. */
    @Volatile private var retrying = false

    fun isRetrying(): Boolean = retrying

    override fun currentLink(): HubLink = HubLink(linkEndpoint, linkEntrance, sessionId)

    // Model actually used for the WS connect — the one the token was minted for
    // (returned by /token), which may differ from the configured default. The
    // ephemeral session's model and the ?model= param must agree (OpenAI path).
    @Volatile private var connectModel = model
    private var sessionThread: Thread? = null
    private var pingThread: Thread? = null
    private val sendBudget = WsSendBudget()
    private val reconnectArmed = AtomicBoolean(false)
    private var summaryAtMs = 0L
    private var summaryAccepted = 0L
    private var summaryDropped = 0L
    @Volatile private var openedAtMs = 0L

    /** Test seam. Production stays on [ATTEMPT_TIMEOUT_MS]. */
    @Volatile internal var attemptTimeoutMs: Long = ATTEMPT_TIMEOUT_MS

    /** Test seam. OkHttp thread, before this leg decides. Shared by every leg of one connect. */
    @Volatile internal var beforeLegDecision: ((LegOwnership) -> Unit)? = null

    /** Test seam. Dial thread, when this leg's budget expires, before the decision. */
    @Volatile internal var onAttemptTimeout: (() -> Unit)? = null

    /** Test seam. OkHttp thread, after claim and before [HubCallDialer.Live.onOpen]. */
    @Volatile internal var onClaimed: (() -> Unit)? = null

    override fun start(sink: MediaTransport.Sink) {
        this.sink = sink
        linkState = "connecting"
        sessionThread = Thread({ sessionLoop() }, "Hub-WS").apply {
            isDaemon = true
            start()
        }
        pingThread = Thread({ pingLoop() }, "Hub-WS-Ping").apply {
            isDaemon = true
            start()
        }
    }

    private fun sessionLoop() {
        var pending: String? = null
        try {
            while (!closed) {
                if (pending != null) {
                    if (pending.startsWith(FATAL_PREFIX)) {
                        fail(pending.removePrefix(FATAL_PREFIX))
                        return
                    }
                    if (linkGraceMs <= 0L) {
                        fail(pending)
                        return
                    }
                    val now = System.currentTimeMillis()
                    grace.markLost(now)
                    val stillOffHook = try {
                        offHook()
                    } catch (_: Exception) {
                        false
                    }
                    val baseDelay = HubRetry.delayWhileOffHook(
                        grace,
                        now,
                        offHook = stillOffHook && !closed,
                    )
                    if (baseDelay == null) {
                        val why = if (!stillOffHook) {
                            "hub link down and the call is not off-hook: ${safeLog(pending)}"
                        } else {
                            "hub link grace expired after ${linkGraceMs}ms: ${safeLog(pending)}"
                        }
                        fail(why)
                        return
                    }
                    if (grace.retryDelayMs(now) == null && !loggedGraceHold) {
                        loggedGraceHold = true
                        BridgeLog.w(
                            "hub grace elapsed after ${linkGraceMs}ms; telephony still off-hook, continuing",
                        )
                    }
                    retrying = true
                    val delay = baseDelay + ((grace.attempt * 173L) % 500L)
                    val reason = safeLog(pending)
                    linkState = "hold"
                    sink?.onLinkHold(true, reason)
                    trace(
                        "reconnect",
                        mapOf(
                            "attempt" to grace.attempt,
                            "delay_ms" to delay,
                            "message" to reason,
                            "session_id" to sessionId,
                        ),
                    )
                    BridgeLog.w(
                        "reconnect attempt=${grace.attempt} delayMs=$delay " +
                            "reason=${reason.take(160)}",
                    )
                    if (delay > 0 && !sleepMs(delay)) return
                    if (closed) return
                }
                val err = try {
                    openAndHold()
                } catch (e: Exception) {
                    "hub link error: ${e.message}"
                }
                if (closed || err == null) return
                pending = err
            }
        } catch (e: Exception) {
            if (!closed) fail("hub link loop failed: ${e.message}")
        } finally {
            retrying = false
        }
    }

    /**
     * Block until this socket dies.
     * Null means the client was stopped. A [FATAL_PREFIX] result is not retried.
     */
    private fun openAndHold(): String? {
        linkState = "connecting"
        if (closed) return null
        val targets = endpoints.ifEmpty {
            listOf(HubEndpoint(webSocketUrl))
        }.filter { it.url.isNotBlank() }

        val cleanClose = AtomicBoolean(false)
        val finished = AtomicBoolean(false)
        val failure = AtomicReference<String?>(null)
        val done = CountDownLatch(1)
        val socketRef = AtomicReference<WebSocket?>(null)

        fun closeSnapshot() {
            closeSocket(socketRef.get() ?: ws)
        }

        fun finish(message: String?, clean: Boolean) {
            if (!finished.compareAndSet(false, true)) return
            if (clean) cleanClose.set(true)
            failure.set(message)
            done.countDown()
        }

        val live = object : HubCallDialer.Live {
            override fun onOpen(webSocket: WebSocket, request: DialRequest) {
                socketRef.set(webSocket)
                if (closed) {
                    closeSocket(webSocket)
                    return
                }
                reconnectArmed.set(false)
                ws = webSocket
                if (closed) {
                    closeSocket(webSocket)
                    return
                }
                linkEndpoint = request.endpoint.url
                linkEntrance = ""
                linkState = "open"
                openedAtMs = System.currentTimeMillis()
                grace.markLinked()
                loggedGraceHold = false
                retrying = false
                sink?.onLinkHold(false, "hub ws open")
                val entrance = HubEndpoints.entranceKind(request.endpoint.url)
                trace(
                    "ws_open",
                    mapOf("session_id" to sessionId, "entrance" to entrance),
                )
                BridgeLog.i(
                    "ws open entrance=$entrance session=${sessionId.ifBlank { "-" }}",
                )
                val openAi = request.endpoint.url.startsWith("wss://api.openai.com")
                if (hubOwnedSession && !openAi) {
                    Log.i(TAG, "WS OPEN — hub owns session (skip session.update / greeting)")
                    sendClientAudio(webSocket)
                    sink?.onStatus("Hub WS connected $entrance")
                } else {
                    Log.i(TAG, "WS OPEN — sending session.update")
                    sink?.onStatus("OpenAI WS connected ($voice)")
                    webSocket.send(sessionUpdateJson())
                }
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                handleEvent(text, sink)
            }

            override fun onDead(webSocket: WebSocket, code: Int, detail: String) {
                // A sibling leg has its own listener. Only the socket onOpen installed
                // can end the session. A loser that arrives before ws is set must not
                // count the latch down or hang up the call.
                if (webSocket !== ws) return
                if (closed) {
                    finish(null, false)
                    return
                }
                val safeDetail = safeLog(detail)
                if (code == 1000) {
                    BridgeLog.i("ws closed code=$code reason=${safeDetail.take(120)}")
                    trace("ws_closed", mapOf("code" to code, "reason" to safeDetail, "local" to false))
                    finish("$FATAL_PREFIX Hub WS closed ($code $safeDetail)", true)
                } else if (code <= 0) {
                    val msg = "Hub WS failure (code=$code): $safeDetail"
                    Log.e(TAG, msg)
                    BridgeLog.e("ws failure code=$code msg=${safeDetail.take(160)}")
                    trace("ws_failure", mapOf("code" to code, "message" to safeDetail))
                    finish(msg, false)
                } else {
                    BridgeLog.i("ws closed code=$code reason=${safeDetail.take(120)}")
                    trace("ws_closed", mapOf("code" to code, "reason" to safeDetail, "local" to false))
                    finish("Hub WS closed ($code $safeDetail)", false)
                }
            }
        }

        val dialer = HubCallDialer(
            http = http,
            hubOwned = hubOwnedSession,
            openAiTokenUrl = tokenUrl,
            model = connectModel,
            handshakeTimeoutMs = attemptTimeoutMs,
        )
        dialer.beforeOwnSocket = beforeLegDecision
        dialer.onAttemptTimeout = onAttemptTimeout
        dialer.onClaimed = onClaimed
        val routine = HubConnectionRoutine(
            targets,
            onAttempt = { attempt ->
                BridgeLog.i(
                    "hub dial pass=${attempt.pass} index=${attempt.index} " +
                        "outcome=${attempt.outcome} http=${attempt.httpStatus} " +
                        "detail=${safeLog(attempt.detail).take(120)}",
                )
            },
            onRace = { activeRace = it },
        ) { request ->
            if (closed) {
                DialResponse(DialStatus.STOP, 0, "stopped")
            } else {
                BridgeLog.i("hub dial start")
                dialer.dial(request, live)
            }
        }
        val result = routine.connectRacing(sessionId, maxPasses = 1) { !closed }
        BridgeLog.i(
            "connect opened=${result.opened} stopped=${result.stopped} " +
                "cancelled=${result.cancelled} pass=${result.pass} index=${result.index} " +
                "attempts=${result.attempts.size}",
        )
        if (!result.opened) {
            if (closed || result.cancelled) {
                closeSnapshot()
                return null
            }
            if (result.stopped) {
                val detail = result.detail.ifBlank { "hub stopped the dial" }
                trace("ws_failure", mapOf("code" to 409, "message" to detail))
                return "$FATAL_PREFIX$detail"
            }
            trace("ws_failure", mapOf("code" to -1, "message" to result.detail))
            return result.detail.ifBlank { "hub connect failed" }
        }
        connectModel = dialer.mintedModel()
        val socket = socketRef.get() ?: ws

        while (!closed) {
            try {
                if (done.await(1, TimeUnit.SECONDS)) break
            } catch (_: InterruptedException) {
                if (closed) {
                    closeSnapshot()
                    return null
                }
            }
            if (closed) {
                closeSnapshot()
                return null
            }
        }
        if (closed) {
            closeSnapshot()
            return null
        }
        linkState = "hold"
        ws = null
        try {
            socket?.cancel()
        } catch (_: Exception) {
        }
        if (cleanClose.get()) {
            return failure.get() ?: "${FATAL_PREFIX}Hub WS closed (1000)"
        }
        return failure.get() ?: "Hub WS failure"
    }

    private fun pingLoop() {
        var ticks = 0
        while (!closed) {
            if (!sleepMs(1_000)) return
            ticks++
            logUplinkSummary()
            if (closed || linkState != "open") continue
            val opened = openedAtMs
            if (opened != 0L && System.currentTimeMillis() - opened >= STABLE_LINK_MS) {
                grace.markStable()
            }
            if (ticks % 5 != 0) continue
            val socket = ws ?: continue
            val sentAt = System.currentTimeMillis()
            try {
                val queued = socket.queueSize()
                when (sendBudget.decide(queued, sentAt)) {
                    WsSendBudget.Decision.WEDGE ->
                        wedgeSocket("send queue ${queued}B")
                    WsSendBudget.Decision.DROP -> Unit
                    WsSendBudget.Decision.SEND -> {
                        val ok = socket.send(
                            JSONObject().put("type", "client.ping").put("t", sentAt).toString(),
                        )
                        if (!ok) wedgeSocket("client.ping rejected")
                    }
                }
            } catch (e: Exception) {
                BridgeLog.w("client.ping failed: ${e.javaClass.simpleName}")
            }
        }
    }

    private fun logUplinkSummary() {
        val now = System.currentTimeMillis()
        val (accepted, dropped) = sendBudget.counts()
        if (summaryAtMs == 0L) {
            summaryAtMs = now
            summaryAccepted = accepted
            summaryDropped = dropped
            return
        }
        val dt = (now - summaryAtMs).coerceAtLeast(1L)
        val sentPerS = (accepted - summaryAccepted) * 1000L / dt
        val dropPerS = (dropped - summaryDropped) * 1000L / dt
        summaryAtMs = now
        summaryAccepted = accepted
        summaryDropped = dropped
        val queued = try {
            ws?.queueSize() ?: -1L
        } catch (_: Exception) {
            -1L
        }
        BridgeLog.i(
            "uplink sent=$sentPerS/s dropped=$dropPerS/s queue=$queued " +
                "rate=$inputRate state=$linkState rtt=$lastRttMs",
        )
    }

    /**
     * OkHttp's writer is blocked or has given up. cancel() unblocks it and
     * [sessionLoop] reconnects. One shot per socket; [onOpen] arms the next.
     */
    private fun wedgeSocket(reason: String) {
        if (closed) return
        if (!reconnectArmed.compareAndSet(false, true)) return
        BridgeLog.w("ws wedge: $reason")
        try {
            ws?.cancel()
        } catch (e: Exception) {
            BridgeLog.e("ws cancel failed: ${e.javaClass.simpleName}")
        }
    }

    private fun fail(message: String) {
        val safe = safeLog(message)
        retrying = false
        if (closed) return
        linkState = "closed"
        Log.e(TAG, safe)
        BridgeLog.e("ws give up: ${safe.take(160)}")
        trace("ws_give_up", mapOf("message" to safe, "session_id" to sessionId))
        sink?.onError(safe)
    }

    private fun safeLog(text: String): String = HubEndpoints.redactHubHosts(text)

    private fun trace(kind: String, fields: Map<String, Any?>) {
        try {
            onTrace(kind, fields)
        } catch (e: Exception) {
            Log.w(TAG, "trace $kind failed: ${e.message}")
        }
    }

    private fun sleepMs(delay: Long): Boolean {
        return try {
            Thread.sleep(delay)
            !closed
        } catch (_: InterruptedException) {
            false
        }
    }

    /**
     * OkHttp calls this on its reader thread. It parses one JSON event and
     * queues PCM. It does not take the playback lock, write the uplink, or
     * wait on a socket. A block here would stop the reader, close the TCP
     * window, and hold the hub's downlink in the kernel buffer.
     */
    private fun handleEvent(text: String, sink: MediaTransport.Sink?) {
        if (sink == null) return
        val json = try {
            JSONObject(text)
        } catch (_: Exception) {
            return
        }
        when (val type = json.optString("type")) {
            "response.output_audio.delta" -> {
                val b64 = json.optString("delta")
                if (b64.isNotEmpty()) {
                    try {
                        val fmt = json.optString("format").ifBlank { "audio/pcmu" }
                        val rate = json.optInt("rate", 8000)
                        sink.onSeq(json.optInt("seq", -1))
                        sink.onAudio(Base64.decode(b64, Base64.DEFAULT), fmt, rate)
                    } catch (e: Exception) {
                        Log.w(TAG, "audio delta decode failed: ${e.message}")
                    }
                }
            }
            "input_audio_buffer.speech_started" -> sink.onFlushPlayback()
            "session.updated" -> {
                val session = json.optJSONObject("session")
                val id = session?.optString("id").orEmpty()
                if (id.isNotBlank()) sessionId = id
                val entrance = session?.optString("entrance").orEmpty()
                if (entrance.isNotBlank()) linkEntrance = entrance
                Log.i(
                    TAG,
                    "session.updated id=$sessionId entrance=${linkEntrance.ifBlank { "-" }} " +
                        "— wire $inputFormat/$inputRate in, $outputFormat/$outputRate out",
                )
                sink.onStatus("Hub session ready")
                sendClientAudio(ws)
                if (!hubOwnedSession) {
                    ws?.send(JSONObject().put("type", "response.create").toString())
                }
            }
            "client.pong" -> {
                val sentAt = json.optLong("t", 0L)
                if (sentAt > 0L) {
                    lastRttMs = (System.currentTimeMillis() - sentAt).coerceAtLeast(0L)
                }
            }
            "error" -> {
                val err = json.optJSONObject("error")?.toString() ?: text
                Log.e(TAG, "WS error event: ${err.take(300)}")
                sink.onStatus("Hub error: ${err.take(160)}")
            }
            "response.output_audio_transcript.done" -> {
                val said = json.optString("transcript")
                if (said.isNotBlank()) Log.i(TAG, "agent said: $said")
            }
            else -> {
                if (type.isNotBlank() && type != "client.ping") {
                    Log.d(TAG, "ws event $type")
                }
            }
        }
    }

    override fun configureWire(
        inputFormat: String,
        inputRate: Int,
        outputFormat: String,
        outputRate: Int,
    ) {
        this.inputFormat = inputFormat
        this.inputRate = inputRate
        this.outputFormat = outputFormat
        this.outputRate = outputRate
        sendClientAudio(ws)
    }

    override fun sendAudio(frame: ByteArray) {
        val socket = ws ?: return
        if (linkState != "open") return
        val now = System.currentTimeMillis()
        val queued = try {
            socket.queueSize()
        } catch (_: Exception) {
            return
        }
        when (sendBudget.decide(queued, now)) {
            WsSendBudget.Decision.DROP -> {
                sendBudget.noteDropped()
                return
            }
            WsSendBudget.Decision.WEDGE -> {
                sendBudget.noteDropped()
                wedgeSocket("send queue ${queued}B for ${sendBudget.wedgeAfterMs}ms")
                return
            }
            WsSendBudget.Decision.SEND -> Unit
        }
        val ok = try {
            socket.send(frame.toByteString())
        } catch (e: Exception) {
            BridgeLog.e("uplink send threw ${e.javaClass.simpleName}")
            wedgeSocket("send threw ${e.javaClass.simpleName}")
            false
        }
        if (ok) {
            sendBudget.noteSent()
        } else {
            sendBudget.noteDropped()
            wedgeSocket("send rejected queue=$queued")
        }
    }

    private fun sendClientAudio(socket: WebSocket?) {
        if (socket == null) return
        // Plain text so a throw from the SDK's org.json stub cannot drop the socket.
        val msg = buildString {
            append("""{"type":"client.audio","input":{"format":""")
            append(jsonString(inputFormat))
            append(""","rate":""")
            append(inputRate)
            append("""},"output":{"format":""")
            append(jsonString(outputFormat))
            append(""","rate":""")
            append(outputRate)
            append("}}")
        }
        socket.send(msg)
        Log.i(TAG, "client.audio in=$inputFormat/$inputRate out=$outputFormat/$outputRate")
    }

    private fun jsonString(value: String): String = buildString {
        append('"')
        for (ch in value) {
            when (ch) {
                '\\' -> append("\\\\")
                '"' -> append("\\\"")
                else -> append(ch)
            }
        }
        append('"')
    }

    override fun stop() {
        if (closed) return
        closed = true
        linkState = "closed"
        retrying = false
        activeRace?.cancelAll()
        trace("ws_closed", mapOf("code" to 1000, "reason" to "call ended", "local" to true))
        sessionThread?.interrupt()
        pingThread?.interrupt()
        closeSocket(ws)
        ws = null
        try {
            http.dispatcher.executorService.shutdown()
        } catch (_: Exception) {
        }
    }

    private fun closeSocket(socket: WebSocket?) {
        try {
            socket?.close(1000, "call ended")
        } catch (_: Exception) {
        }
    }

    /**
     * GA session config: μ-law both ways, configured voice, server VAD.
     * Only used on the OpenAI path; a custom hub owns instructions/greeting.
     */
    private fun sessionUpdateJson(): String {
        val inputFmt = JSONObject().put("type", "audio/pcmu")
        val outputFmt = JSONObject().put("type", "audio/pcmu")
        val input = JSONObject()
            .put("format", inputFmt)
            .put("turn_detection", JSONObject().put("type", "server_vad"))
        val output = JSONObject()
            .put("format", outputFmt)
            .put("voice", voice)
        val audio = JSONObject().put("input", input).put("output", output)
        val session = JSONObject()
            .put("type", "realtime")
            .put("model", connectModel)
            .put("output_modalities", org.json.JSONArray().put("audio"))
            .put("audio", audio)
            .put("instructions", instructions)
        return JSONObject().put("type", "session.update").put("session", session).toString()
    }

    companion object {
        private const val TAG = "HubStream"
        private const val FATAL_PREFIX = "fatal:"
        private const val STABLE_LINK_MS = 20_000L

        const val DEFAULT_INSTRUCTIONS =
            "You are a friendly voice assistant on a phone call bridged from a " +
                "cellular caller. Greet the caller briefly, then converse naturally " +
                "and keep responses short. Do not hang up."
    }
}
