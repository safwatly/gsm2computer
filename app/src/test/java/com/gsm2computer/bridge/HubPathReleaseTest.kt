package com.gsm2computer.bridge

import com.gsm2computer.bridge.realtime.HubCallDialer
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.WebSocket
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference

/**
 * Places one hub call on the public entrance and one on the Tailscale entrance,
 * then a failover and a session-id relink. Skipped unless
 * GSM2COMPUTER_BRIDGE_PATH_RELEASE=1. The drive script fails if this did not
 * write an ok evidence file, so a skip is not a pass.
 */
class HubPathReleaseTest {

    private val json = "application/json; charset=utf-8".toMediaType()
    private val sockets = mutableListOf<WebSocket>()

    @Test
    fun bothEntrancesFailoverAndRelink() {
        assumeTrue(System.getenv("GSM2COMPUTER_BRIDGE_PATH_RELEASE") == "1")
        val state = System.getenv("GSM2COMPUTER_VERIFY_STATE")
            ?: error("GSM2COMPUTER_VERIFY_STATE is required")
        val env = loadEnv(File(state, "runtime.env"))
        val token = env["DEVICE_TOKEN"].orEmpty()
        check(token.isNotBlank()) { "runtime.env has no DEVICE_TOKEN" }
        val publicUrl = env.getValue("PUBLIC_URL")
        val tailscaleUrl = env.getValue("TAILSCALE_URL")
        val public = HubEndpoint(publicUrl, token)
        val tailscale = HubEndpoint(tailscaleUrl, token)
        val dead = HubEndpoint("http://127.0.0.1:1", token)

        val http = OkHttpClient.Builder()
            .connectTimeout(3, TimeUnit.SECONDS)
            .readTimeout(5, TimeUnit.SECONDS)
            .build()
        val dialer = HubCallDialer(
            http = OkHttpClient.Builder()
                .connectTimeout(3, TimeUnit.SECONDS)
                .readTimeout(0, TimeUnit.MILLISECONDS)
                .build(),
            hubOwned = true,
            openAiTokenUrl = "",
            model = "hub",
            handshakeTimeoutMs = 8_000,
        )
        val evidence = linkedMapOf<String, String>()
        var ok = false
        try {
            val bare = httpGet(http, "$publicUrl/health", null)
            check(bare.first == 401) { "public /health without a token returned ${bare.first}" }
            val authed = httpGet(http, "$publicUrl/health", token)
            check(authed.first == 200) { "public /health with the device token returned ${authed.first}" }

            val publicCall = openCall(dialer, listOf(public), "")
            val publicSession = awaitSession(publicCall.messages)
            check(publicSession.entrance == "public") {
                "public call entrance=${publicSession.entrance} summary=${publicCall.result.summary()}"
            }
            val publicHealth = awaitHealth(http, tailscaleUrl, publicSession.id, "public")
            val publicReport = postCall(http, listOf(public), publicSession.id, "path-public")
            check(publicReport.contains("\"path\": \"public\"") || publicReport.contains("\"path\":\"public\"")) {
                "public call report was ${publicReport.take(240)}"
            }
            evidence["public"] = "session=${publicSession.id} health=$publicHealth"
            hangup(publicCall.socket)
            waitIdle(http, tailscaleUrl)

            val tailscaleCall = openCall(dialer, listOf(tailscale), "")
            val tailscaleSession = awaitSession(tailscaleCall.messages)
            check(tailscaleSession.entrance == "tailscale") {
                "tailscale call entrance=${tailscaleSession.entrance} summary=${tailscaleCall.result.summary()}"
            }
            val tailscaleHealth = awaitHealth(http, tailscaleUrl, tailscaleSession.id, "tailscale")
            val tailscaleReport = postCall(http, listOf(tailscale), tailscaleSession.id, "path-tailscale")
            check(
                tailscaleReport.contains("\"path\": \"tailscale\"") ||
                    tailscaleReport.contains("\"path\":\"tailscale\""),
            ) { "tailscale call report was ${tailscaleReport.take(240)}" }
            evidence["tailscale"] = "session=${tailscaleSession.id} health=$tailscaleHealth"
            hangup(tailscaleCall.socket)
            waitIdle(http, tailscaleUrl)

            val failover = openCall(dialer, listOf(dead, tailscale), "")
            check(failover.result.opened && failover.result.index == 1) {
                "failover did not land on the second endpoint: ${failover.result.summary()}"
            }
            check(failover.result.attempts.first().outcome == "retryable") {
                "dead endpoint was not a retryable failure: ${failover.result.summary()}"
            }
            val failoverSession = awaitSession(failover.messages)
            check(failoverSession.entrance == "tailscale") {
                "failover entrance=${failoverSession.entrance}"
            }
            awaitHealth(http, tailscaleUrl, failoverSession.id, "tailscale")
            evidence["failover"] = failover.result.summary()
            hangup(failover.socket)
            waitIdle(http, tailscaleUrl)

            val first = openCall(dialer, listOf(public), "")
            val original = awaitSession(first.messages)
            check(original.entrance == "public") { "relink setup entrance=${original.entrance}" }
            first.socket.cancel()
            awaitGap(http, tailscaleUrl)
            val relink = openCall(dialer, listOf(dead, tailscale), original.id, maxPasses = 8)
            check(relink.result.opened) { "relink failed: ${relink.result.summary()}" }
            check(relink.result.attempts.all { it.sessionId == original.id }) {
                "relink dropped the session id: ${relink.result.summary()}"
            }
            val resumed = awaitSession(relink.messages)
            check(resumed.id == original.id) {
                "relink session ${resumed.id} is not ${original.id}"
            }
            check(resumed.entrance == "tailscale") { "relink entrance=${resumed.entrance}" }
            val relinkHealth = awaitHealth(http, tailscaleUrl, original.id, "tailscale")
            val relinkReport = postCall(http, listOf(dead, tailscale), original.id, "path-relink")
            check(
                relinkReport.contains("\"path\": \"tailscale\"") ||
                    relinkReport.contains("\"path\":\"tailscale\""),
            ) { "relink call report was ${relinkReport.take(240)}" }
            evidence["relink"] = "session=${original.id} health=$relinkHealth ${relink.result.summary()}"
            hangup(relink.socket)
            waitIdle(http, tailscaleUrl)
            ok = true
        } finally {
            sockets.forEach { socket ->
                try {
                    socket.close(1000, "release test")
                } catch (_: Exception) {
                }
            }
            writeEvidence(evidence, ok, token)
        }
        check(ok) { "bridge path release test did not pass" }
    }

    private fun openCall(
        dialer: HubCallDialer,
        endpoints: List<HubEndpoint>,
        sessionId: String,
        maxPasses: Int = 2,
    ): OpenedCall {
        val messages = LinkedBlockingQueue<String>()
        val socketRef = AtomicReference<WebSocket?>(null)
        val routine = HubConnectionRoutine(
            endpoints = endpoints,
            backoff = Backoff(initialMs = 200, multiplier = 2.0, capMs = 500),
            sleep = { delay -> Thread.sleep(delay) },
        ) { request ->
            dialer.dial(request, object : HubCallDialer.Live {
                override fun onOpen(webSocket: WebSocket, request: DialRequest) {
                    socketRef.set(webSocket)
                }

                override fun onMessage(webSocket: WebSocket, text: String) {
                    messages.add(text)
                }

                override fun onDead(webSocket: WebSocket, code: Int, detail: String) = Unit
            })
        }
        val result = routine.connect(sessionId, maxPasses)
        val socket = socketRef.get()
        if (socket != null) sockets += socket
        check(result.opened && socket != null) { "connect failed: ${result.summary()}" }
        return OpenedCall(result, socket, messages)
    }

    private fun awaitSession(messages: LinkedBlockingQueue<String>): SessionBits {
        val deadline = System.currentTimeMillis() + 25_000
        val seen = StringBuilder()
        while (System.currentTimeMillis() < deadline) {
            val text = messages.poll(250, TimeUnit.MILLISECONDS) ?: continue
            seen.append(text)
            if (text.contains("\"error\"")) {
                error("hub error before session.updated: ${text.take(300)}")
            }
            if (!text.contains("session.updated")) continue
            val id = jsonString(text, "id") ?: continue
            val entrance = jsonString(text, "entrance") ?: continue
            if (id.startsWith("hub-") && (entrance == "public" || entrance == "tailscale")) {
                return SessionBits(id, entrance)
            }
        }
        error("no session.updated; saw ${seen.take(300)}")
    }

    private fun awaitHealth(http: OkHttpClient, tailscaleUrl: String, sessionId: String, entrance: String): String {
        val deadline = System.currentTimeMillis() + 8_000
        var last = ""
        while (System.currentTimeMillis() < deadline) {
            val (code, body) = httpGet(http, "$tailscaleUrl/health", null)
            last = body
            if (code == 200 && body.contains(sessionId) && jsonString(body, "path") == entrance &&
                jsonString(body, "entrance") == entrance
            ) {
                return "path=$entrance"
            }
            Thread.sleep(100)
        }
        error("health did not report $entrance for $sessionId: ${last.take(400)}")
    }

    private fun awaitGap(http: OkHttpClient, tailscaleUrl: String) {
        val deadline = System.currentTimeMillis() + 8_000
        var last = ""
        while (System.currentTimeMillis() < deadline) {
            val (code, body) = httpGet(http, "$tailscaleUrl/health", null)
            last = body
            if (code == 200 && Regex(""""link_gap_open"\s*:\s*true""").containsMatchIn(body)) return
            Thread.sleep(100)
        }
        error("link gap did not open: ${last.take(400)}")
    }

    private fun waitIdle(http: OkHttpClient, tailscaleUrl: String) {
        val deadline = System.currentTimeMillis() + 8_000
        var last = ""
        while (System.currentTimeMillis() < deadline) {
            val (code, body) = httpGet(http, "$tailscaleUrl/health", null)
            last = body
            if (code == 200 && Regex(""""busy"\s*:\s*false""").containsMatchIn(body)) return
            Thread.sleep(100)
        }
        error("call slot stayed busy: ${last.take(300)}")
    }

    private fun postCall(
        http: OkHttpClient,
        endpoints: List<HubEndpoint>,
        sessionId: String,
        number: String,
    ): String {
        val payload = HubEndpoints.callJson(
            direction = "in",
            number = number,
            startedAt = "2026-11-09T00:00:00Z",
            durationSec = 1,
            sessionId = sessionId,
        )
        val result = HubHttpWalk(http).run(endpoints, sessionId) { endpoint ->
            Request.Builder()
                .url(HubEndpoints.callsUrl(HubEndpoints.httpOrigin(endpoint.url)))
                .post(payload.toRequestBody(json))
                .build()
        }
        check(result.opened) { "POST /calls failed HTTP ${result.code} ${result.detail.take(160)}" }
        return result.body
    }

    private fun hangup(socket: WebSocket) {
        socket.close(1000, "call ended")
        sockets.remove(socket)
    }

    private fun httpGet(http: OkHttpClient, url: String, token: String?): Pair<Int, String> {
        val builder = Request.Builder().url(url).get()
        if (!token.isNullOrBlank()) builder.header(HubEndpoints.DEVICE_TOKEN_HEADER, token)
        http.newCall(builder.build()).execute().use { resp ->
            return resp.code to resp.body?.string().orEmpty()
        }
    }

    private fun jsonString(json: String, key: String): String? {
        val match = Regex(""""$key"\s*:\s*"([^"\\]*)"""").find(json) ?: return null
        return match.groupValues[1]
    }

    private fun loadEnv(file: File): Map<String, String> {
        check(file.isFile) { "missing ${file.path}" }
        val out = linkedMapOf<String, String>()
        for (line in file.readLines()) {
            if (line.isBlank() || line.startsWith("#") || !line.contains("=")) continue
            val key = line.substringBefore("=")
            out[key] = line.substringAfter("=")
        }
        return out
    }

    private fun writeEvidence(fields: Map<String, String>, ok: Boolean, token: String) {
        val dir = File(System.getenv("GSM2COMPUTER_VERIFY_EVIDENCE") ?: "/opt/cursor/artifacts/verify-gsm2computer")
        dir.mkdirs()
        val body = buildString {
            append("{\n")
            append("  \"ok\": ").append(ok).append(",\n")
            val keys = listOf("public", "tailscale", "failover", "relink")
            keys.forEachIndexed { index, key ->
                append("  \"").append(key).append("\": \"")
                append(jsonEscape(fields[key] ?: ""))
                append("\"")
                if (index != keys.lastIndex) append(",")
                append("\n")
            }
            append("}\n")
        }
        check(!body.contains(token)) { "evidence included the device token" }
        File(dir, "bridge-paths.json").writeText(body)
    }

    private fun jsonEscape(value: String): String = value
        .replace("\\", "\\\\")
        .replace("\"", "\\\"")
        .replace("\n", "\\n")

    private data class SessionBits(val id: String, val entrance: String)

    private data class OpenedCall(
        val result: ConnectResult,
        val socket: WebSocket,
        val messages: LinkedBlockingQueue<String>,
    )
}
