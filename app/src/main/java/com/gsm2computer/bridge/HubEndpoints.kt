package com.gsm2computer.bridge

/**
 * Hub control-plane URL helpers.
 *
 * [hubControlUrl] is the Tailscale (or LAN) origin, e.g. `http://hub.mining-ling.ts.net:8787`.
 * Token, health, SMS, and the μ-law WebSocket upgrade all live on that host.
 * When the hub URL is blank the phone falls back to OpenAI Realtime.
 */
object HubEndpoints {

    const val OPENAI_REALTIME_WS = "wss://api.openai.com/v1/realtime"
    const val DEFAULT_HUB_CONTROL_URL = "http://hub.mining-ling.ts.net:8787"
    const val DEFAULT_PUBLIC_WS = "wss://gsm.ta3leam.com/"
    const val DEFAULT_TAILSCALE_WS = "ws://100.113.222.47:8787/"
    const val DEVICE_TOKEN_HEADER = "X-Gsm-Device-Token"
    const val CALL_SESSION_HEADER = "X-Gsm-Call-Session"

    fun normalizeBase(url: String): String = url.trim().trimEnd('/')

    fun defaultEndpointUrls(): List<String> = listOf(DEFAULT_PUBLIC_WS, DEFAULT_TAILSCALE_WS)

    /** Newline- or comma-separated hub URLs. Blank entries are dropped. */
    fun parseEndpointList(raw: String): List<String> =
        raw.split('\n', ',')
            .map { normalizeBase(it) }
            .filter { it.isNotEmpty() }

    /**
     * [explicitList] set (even to empty) is the walk. A saved `hub_control_url`
     * is not a list: build 344 stored that and nothing else, and treating it as
     * the list kept the phone on one Tailscale origin. Absent means [defaults],
     * public then Tailscale.
     */
    fun resolveEndpointUrls(explicitList: String?, defaults: String): List<String> {
        if (explicitList != null) return parseEndpointList(explicitList)
        return parseEndpointList(defaults).ifEmpty { defaultEndpointUrls() }
    }

    /**
     * Logged before a walk when an entrance has no device token. Names that
     * entrance and the next one, so a public 401 is visibly not the end of the walk.
     */
    fun missingDeviceTokenWarning(endpoints: List<HubEndpoint>): String? {
        val index = endpoints.indexOfFirst { it.deviceToken.isBlank() }
        if (index < 0) return null
        val bare = endpoints[index]
        val next = endpoints.getOrNull(index + 1)
        return if (next == null) {
            "device token unset; ${bare.url} will reject the call"
        } else {
            "device token unset; ${bare.url} will reject the call and the walk continues to ${next.url}"
        }
    }

    /** Detail for a 401 when this entrance was dialed with no device token. */
    fun missingTokenRejection(url: String, httpStatus: Int, tokenBlank: Boolean): String? {
        if (httpStatus != 401 || !tokenBlank) return null
        return "device token unset; $url rejected the call, trying the next endpoint"
    }

    /** `ws`/`wss` back to the HTTP origin used for `/token`, `/sms`, and `/calls`. */
    fun httpOrigin(url: String): String {
        val base = normalizeBase(url)
        return when {
            base.startsWith("wss://") -> "https://" + base.removePrefix("wss://")
            base.startsWith("ws://") -> "http://" + base.removePrefix("ws://")
            else -> base
        }
    }

    /** Device token is omitted when blank. The session header is omitted when blank. */
    fun hubHeaders(endpoint: HubEndpoint, sessionId: String): List<Pair<String, String>> {
        val headers = mutableListOf<Pair<String, String>>()
        if (endpoint.deviceToken.isNotBlank()) {
            headers += DEVICE_TOKEN_HEADER to endpoint.deviceToken
        }
        if (sessionId.isNotBlank()) headers += CALL_SESSION_HEADER to sessionId
        return headers
    }

    fun isCustomHub(hubControlUrl: String): Boolean =
        normalizeBase(hubControlUrl).isNotEmpty()

    /** Token mint: explicit override, else `{hub}/token`. */
    fun tokenUrl(hubControlUrl: String, streamTokenUrl: String): String {
        val explicit = streamTokenUrl.trim()
        if (explicit.isNotEmpty()) return explicit
        val hub = normalizeBase(hubControlUrl)
        return if (hub.isEmpty()) "" else "$hub/token"
    }

    /** SMS ingest: `{hub}/sms`. Empty when no hub is configured. */
    fun smsUrl(hubControlUrl: String): String {
        val hub = normalizeBase(hubControlUrl)
        return if (hub.isEmpty()) "" else "$hub/sms"
    }

    /** Outbound SMS poll: `{hub}/sms/outbox`. */
    fun smsOutboxUrl(hubControlUrl: String): String {
        val hub = normalizeBase(hubControlUrl)
        return if (hub.isEmpty()) "" else "$hub/sms/outbox"
    }

    /** Send-complete ack: `{hub}/sms/outbox/{id}/ack`. */
    fun smsOutboxAckUrl(hubControlUrl: String, id: String): String {
        val hub = normalizeBase(hubControlUrl)
        return if (hub.isEmpty() || id.isBlank()) "" else "$hub/sms/outbox/$id/ack"
    }

    /** Call log ingest: `{hub}/calls`. */
    fun callsUrl(hubControlUrl: String): String {
        val hub = normalizeBase(hubControlUrl)
        return if (hub.isEmpty()) "" else "$hub/calls"
    }

    /**
     * WebSocket origin. Custom hub → `http`/`https` rewritten to `ws`/`wss`.
     * OpenAI fallback when hub URL is blank.
     */
    fun webSocketUrl(hubControlUrl: String): String {
        val hub = normalizeBase(hubControlUrl)
        if (hub.isEmpty()) return OPENAI_REALTIME_WS
        return toWebSocket(hub)
    }

    /** OpenAI WS needs `?model=`; a custom hub is upgraded on the same origin. */
    fun connectUrl(webSocketUrl: String, model: String, customHub: Boolean): String {
        if (customHub) return webSocketUrl
        val sep = if (webSocketUrl.contains('?')) '&' else '?'
        return "$webSocketUrl${sep}model=$model"
    }

    /**
     * Greeting (`response.create`) and OpenAI `session.update` are hub-owned
     * whenever a custom hub URL is set, unless [storedOverride] is present.
     */
    fun hubOwnedSession(hubControlUrl: String, storedOverride: Boolean?): Boolean =
        storedOverride ?: isCustomHub(hubControlUrl)

    fun smsJson(from: String, body: String, receivedAt: String, clientId: String = ""): String =
        buildString {
            append('{')
            append("\"from\":").append(jsonString(from)).append(',')
            append("\"body\":").append(jsonString(body)).append(',')
            append("\"receivedAt\":").append(jsonString(receivedAt))
            if (clientId.isNotEmpty()) {
                append(',').append("\"clientId\":").append(jsonString(clientId))
            }
            append('}')
        }

    fun callJson(
        direction: String,
        number: String,
        startedAt: String,
        durationSec: Long,
        switchboardMode: String = "",
        sessionId: String = "",
        tapSummary: String = "",
    ): String = buildString {
        append('{')
        append("\"direction\":").append(jsonString(direction)).append(',')
        append("\"number\":").append(jsonString(number)).append(',')
        append("\"started_at\":").append(jsonString(startedAt)).append(',')
        append("\"duration_sec\":").append(durationSec)
        if (switchboardMode.isNotEmpty()) {
            append(',').append("\"switchboard_mode\":").append(jsonString(switchboardMode))
        }
        if (sessionId.isNotEmpty()) {
            append(',').append("\"session_id\":").append(jsonString(sessionId))
        }
        if (tapSummary.isNotEmpty()) {
            append(',').append("\"tap_summary\":").append(jsonString(tapSummary))
        }
        append('}')
    }

    fun outboxAckJson(status: String, error: String = ""): String = buildString {
        append('{')
        append("\"status\":").append(jsonString(status))
        if (error.isNotEmpty()) {
            append(',').append("\"error\":").append(jsonString(error))
        }
        append('}')
    }

    internal fun jsonString(value: String): String = buildString {
        append('"')
        for (c in value) {
            when (c) {
                '\\' -> append("\\\\")
                '"' -> append("\\\"")
                '\n' -> append("\\n")
                '\r' -> append("\\r")
                '\t' -> append("\\t")
                else -> append(c)
            }
        }
        append('"')
    }

    private fun toWebSocket(httpUrl: String): String = when {
        httpUrl.startsWith("https://") -> "wss://" + httpUrl.removePrefix("https://")
        httpUrl.startsWith("http://") -> "ws://" + httpUrl.removePrefix("http://")
        httpUrl.startsWith("ws://") || httpUrl.startsWith("wss://") -> httpUrl
        else -> httpUrl
    }
}
