package com.gsm2computer.bridge

/**
 * One walk over an ordered list of hub endpoints.
 *
 * The first connect and a mid-call reconnect both call [connect]. A retryable
 * failure tries the next endpoint in the same pass. [DialStatus.LATER] ends
 * the pass and the next pass starts after [Backoff] — that is how a 409 is
 * retried while the hub may be opening the relink grace window, without
 * opening a second socket in the same pass. [DialStatus.STOP] ends the walk
 * so a busy hub is not dialed again. Nothing in this class treats one
 * entrance differently from another: the dial closure sees [DialRequest] only.
 */
class HubConnectionRoutine(
    private val endpoints: List<HubEndpoint>,
    private val backoff: Backoff = Backoff(),
    private val sleep: (Long) -> Unit = { delay -> Thread.sleep(delay) },
    private val dial: (DialRequest) -> DialResponse,
) {
    fun connect(
        sessionId: String = "",
        maxPasses: Int = 1,
        proceed: () -> Boolean = { true },
    ): ConnectResult {
        val secrets = endpoints.map { it.deviceToken }
        val attempts = mutableListOf<AttemptRecord>()
        if (endpoints.isEmpty() || maxPasses < 1) {
            return ConnectResult(
                opened = false,
                cancelled = false,
                stopped = true,
                pass = 0,
                index = -1,
                url = "",
                sessionId = sessionId,
                attempts = attempts,
                detail = scrubSecrets("no endpoints", secrets),
            )
        }
        var lastDetail = ""
        for (pass in 0 until maxPasses) {
            if (!proceed()) return cancelled(sessionId, attempts, secrets)
            for ((index, endpoint) in endpoints.withIndex()) {
                if (!proceed()) return cancelled(sessionId, attempts, secrets)
                val response = try {
                    dial(DialRequest(endpoint, sessionId))
                } catch (e: Exception) {
                    DialResponse(
                        DialStatus.RETRYABLE,
                        0,
                        e.message ?: e.javaClass.simpleName,
                    )
                }
                val detail = scrubSecrets(response.detail, secrets)
                lastDetail = detail
                attempts += AttemptRecord(
                    pass = pass,
                    index = index,
                    url = endpoint.url,
                    sessionId = sessionId,
                    outcome = response.status.name.lowercase(),
                    httpStatus = response.httpStatus,
                    detail = detail,
                )
                when (response.status) {
                    DialStatus.OPENED -> return ConnectResult(
                        opened = true,
                        cancelled = false,
                        stopped = false,
                        pass = pass,
                        index = index,
                        url = endpoint.url,
                        sessionId = sessionId,
                        attempts = attempts.toList(),
                        detail = detail,
                    )
                    DialStatus.STOP -> return ConnectResult(
                        opened = false,
                        cancelled = false,
                        stopped = true,
                        pass = pass,
                        index = index,
                        url = endpoint.url,
                        sessionId = sessionId,
                        attempts = attempts.toList(),
                        detail = detail,
                    )
                    DialStatus.LATER -> break
                    DialStatus.RETRYABLE -> Unit
                }
            }
            if (pass < maxPasses - 1) {
                if (!proceed()) return cancelled(sessionId, attempts, secrets)
                val delay = backoff.delayAfterPass(pass)
                if (delay > 0) sleep(delay)
            }
        }
        return ConnectResult(
            opened = false,
            cancelled = false,
            stopped = false,
            pass = maxPasses - 1,
            index = endpoints.lastIndex,
            url = "",
            sessionId = sessionId,
            attempts = attempts.toList(),
            detail = lastDetail,
        )
    }

    private fun cancelled(
        sessionId: String,
        attempts: List<AttemptRecord>,
        secrets: List<String>,
    ): ConnectResult = ConnectResult(
        opened = false,
        cancelled = true,
        stopped = false,
        pass = attempts.lastOrNull()?.pass ?: 0,
        index = attempts.lastOrNull()?.index ?: -1,
        url = "",
        sessionId = sessionId,
        attempts = attempts.toList(),
        detail = scrubSecrets("cancelled", secrets),
    )
}

/**
 * One hub entrance. [deviceToken] is sent as `X-Gsm-Device-Token` by the dialer.
 * [toString] omits it so a log of the endpoint list cannot print the secret.
 */
data class HubEndpoint(
    val url: String,
    val deviceToken: String = "",
) {
    override fun toString(): String = "HubEndpoint(url=$url)"
}

data class DialRequest(
    val endpoint: HubEndpoint,
    val sessionId: String,
)

enum class DialStatus {
    OPENED,
    RETRYABLE,
    LATER,
    STOP,
}

data class DialResponse(
    val status: DialStatus,
    val httpStatus: Int = 0,
    val detail: String = "",
)

data class AttemptRecord(
    val pass: Int,
    val index: Int,
    val url: String,
    val sessionId: String,
    val outcome: String,
    val httpStatus: Int,
    val detail: String,
)

data class ConnectResult(
    val opened: Boolean,
    val cancelled: Boolean,
    val stopped: Boolean,
    val pass: Int,
    val index: Int,
    val url: String,
    val sessionId: String,
    val attempts: List<AttemptRecord>,
    val detail: String,
) {
    fun summary(): String = buildString {
        append("opened=").append(opened)
        append(" stopped=").append(stopped)
        append(" cancelled=").append(cancelled)
        append(" pass=").append(pass)
        append(" index=").append(index)
        append(" url=").append(url)
        append(" session=").append(sessionId.ifBlank { "-" })
        append(" attempts=")
        append(attempts.joinToString(",") { attempt ->
            "${attempt.pass}:${attempt.index}:${attempt.url}:${attempt.outcome}:${attempt.httpStatus}"
        })
        if (detail.isNotBlank()) append(" detail=").append(detail.take(160))
    }
}

data class HubLink(
    val endpointUrl: String = "",
    val entrance: String = "",
    val sessionId: String = "",
)

data class Backoff(
    val initialMs: Long = 250,
    val multiplier: Double = 2.0,
    val capMs: Long = 4_000,
) {
    fun delayAfterPass(failedPassIndex: Int): Long {
        var delay = initialMs.toDouble()
        var remaining = failedPassIndex
        while (remaining > 0) {
            delay *= multiplier
            remaining -= 1
        }
        if (delay > capMs) delay = capMs.toDouble()
        if (delay < 0) return 0
        return delay.toLong()
    }
}

fun scrubSecrets(text: String, secrets: Collection<String>): String {
    var out = text
    for (secret in secrets) {
        if (secret.isNotBlank()) out = out.replace(secret, "[redacted]")
    }
    return out
}

/**
 * Map an HTTP status from one endpoint attempt.
 *
 * 409 with a session id is [DialStatus.LATER]: the hub may be about to hold
 * the relink grace window, and another socket in this same pass could supersede
 * the live call. 409 without a session id is [DialStatus.STOP]: this connect is
 * not a relink, and a second socket must not take over someone else's call.
 * Every other non-success is [DialStatus.RETRYABLE] so the walk tries the next
 * endpoint. The entrance name is not an input.
 */
fun classifyHubStatus(httpStatus: Int, sessionId: String): DialStatus = when {
    httpStatus == 101 || httpStatus in 200..299 -> DialStatus.OPENED
    httpStatus == 409 -> if (sessionId.isNotBlank()) DialStatus.LATER else DialStatus.STOP
    else -> DialStatus.RETRYABLE
}
