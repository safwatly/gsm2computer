package com.gsm2computer.bridge

import okhttp3.OkHttpClient
import okhttp3.Request
import java.util.concurrent.TimeUnit

/**
 * One HTTP request per endpoint, walked by [HubConnectionRoutine].
 * SMS, the outbox, and the call report use this. They do not have their own
 * public-then-Tailscale loop.
 */
class HubHttpWalk(
    private val http: OkHttpClient,
    private val backoff: Backoff = Backoff(),
    private val sleep: (Long) -> Unit = { delay -> Thread.sleep(delay) },
) {
    data class Result(
        val opened: Boolean,
        val code: Int,
        val body: String,
        val url: String,
        val detail: String,
    )

    fun run(
        endpoints: List<HubEndpoint>,
        sessionId: String = "",
        maxPasses: Int = 1,
        requestFor: (HubEndpoint) -> Request,
    ): Result {
        var code = 0
        var body = ""
        var url = ""
        val routine = HubConnectionRoutine(endpoints, backoff, sleep) { request ->
            val secrets = listOf(request.endpoint.deviceToken)
            try {
                val built = requestFor(request.endpoint).newBuilder()
                for ((name, value) in HubEndpoints.hubHeaders(request.endpoint, request.sessionId)) {
                    built.header(name, value)
                }
                val call = http.newCall(built.build())
                call.timeout().timeout(15, TimeUnit.SECONDS)
                call.execute().use { resp ->
                    val text = resp.body?.string().orEmpty()
                    val status = classifyHubStatus(resp.code, request.sessionId)
                    if (status == DialStatus.OPENED) {
                        code = resp.code
                        body = text
                        url = request.endpoint.url
                    }
                    DialResponse(status, resp.code, scrubSecrets(text.take(160), secrets))
                }
            } catch (e: Exception) {
                DialResponse(
                    DialStatus.RETRYABLE,
                    0,
                    scrubSecrets(e.message ?: e.javaClass.simpleName, secrets),
                )
            }
        }
        val result = routine.connect(sessionId, maxPasses)
        return Result(
            opened = result.opened,
            code = if (result.opened) code else result.attempts.lastOrNull()?.httpStatus ?: 0,
            body = if (result.opened) body else "",
            url = if (result.opened) url else "",
            detail = result.detail,
        )
    }
}
