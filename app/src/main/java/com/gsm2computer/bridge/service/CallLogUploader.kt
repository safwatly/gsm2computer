package com.gsm2computer.bridge.service

import android.content.Context
import android.util.Log
import com.gsm2computer.bridge.BridgeConfig
import com.gsm2computer.bridge.HubEndpoints
import com.gsm2computer.bridge.HubHttpWalk
import com.gsm2computer.bridge.util.RedactingLogger
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.time.Instant
import java.util.concurrent.TimeUnit

/**
 * POSTs a bridged-call record to the hub after hangup. Hub fills
 * switchboard_mode / tap_summary from last_call_tap when omitted.
 */
object CallLogUploader {

    private const val TAG = "CallLogUploader"
    private val JSON = "application/json; charset=utf-8".toMediaType()

    private val http = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(15, TimeUnit.SECONDS)
        .writeTimeout(15, TimeUnit.SECONDS)
        .build()

    fun post(
        context: Context,
        incoming: Boolean,
        number: String,
        startedAtMs: Long,
        durationSec: Long,
        sessionId: String = "",
    ) {
        val endpoints = BridgeConfig.resolveEndpoints(BridgeConfig.openPrefs(context))
        if (endpoints.isEmpty()) return
        val payload = HubEndpoints.callJson(
            direction = if (incoming) "in" else "out",
            number = number,
            startedAt = Instant.ofEpochMilli(startedAtMs).toString(),
            durationSec = durationSec,
            sessionId = sessionId,
        )
        val result = HubHttpWalk(http).run(endpoints, sessionId) { endpoint ->
            Request.Builder()
                .url(HubEndpoints.callsUrl(HubEndpoints.httpOrigin(endpoint.url)))
                .post(payload.toRequestBody(JSON))
                .build()
        }
        if (result.opened) {
            RedactingLogger.i(TAG, "call record posted $number via ${result.url}")
            GatewayService.appendLog(
                context,
                "Call record uploaded via ${result.url} session=${sessionId.ifBlank { "-" }}",
            )
        } else {
            Log.w(TAG, "call record failed HTTP ${result.code} ${result.detail.take(160)}")
        }
    }
}
