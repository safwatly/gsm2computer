package com.gsm2computer.bridge.sms

import android.content.Context
import android.util.Log
import com.gsm2computer.bridge.BridgeConfig
import com.gsm2computer.bridge.HubEndpoint
import com.gsm2computer.bridge.HubEndpoints
import com.gsm2computer.bridge.HubHttpWalk
import com.gsm2computer.bridge.service.GatewayService
import com.gsm2computer.bridge.util.RedactingLogger
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.time.Instant
import java.util.UUID
import java.util.concurrent.TimeUnit

/**
 * POSTs inbound SMS to the hub control API. Does not parse the body —
 * the hub owns command handling.
 *
 * Runs on a background thread so [SmsReceiver] never blocks the GSM bridge.
 */
object SmsForwarder {

    private const val TAG = "SmsForwarder"
    private val JSON = "application/json; charset=utf-8".toMediaType()

    private val http = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(15, TimeUnit.SECONDS)
        .writeTimeout(15, TimeUnit.SECONDS)
        .build()

    fun forward(context: Context, from: String, body: String) {
        val endpoints = BridgeConfig.resolveEndpoints(BridgeConfig.openPrefs(context))
        if (endpoints.isEmpty()) {
            Log.w(TAG, "SMS skipped — hub control URL not set")
            GatewayService.appendLog(context, "SMS skipped — hub control URL not set")
            return
        }
        val receivedAt = Instant.now().toString()
        val app = context.applicationContext
        Thread({ post(app, endpoints, from, body, receivedAt) }, "Sms-Forward").start()
    }

    private fun post(
        context: Context,
        endpoints: List<HubEndpoint>,
        from: String,
        body: String,
        receivedAt: String,
    ) {
        // One id for every endpoint this walk tries. The hub stores the first
        // copy and treats a repeat of the same clientId as the same SMS.
        val payload = HubEndpoints.smsJson(from, body, receivedAt, UUID.randomUUID().toString())
        val result = HubHttpWalk(http).run(endpoints) { endpoint ->
            Request.Builder()
                .url(HubEndpoints.smsUrl(HubEndpoints.httpOrigin(endpoint.url)))
                .post(payload.toRequestBody(JSON))
                .build()
        }
        if (result.opened) {
            RedactingLogger.i(TAG, "SMS forwarded from $from → ${result.url}")
            GatewayService.appendLog(context, "SMS forwarded from $from via ${result.url}")
        } else {
            val msg = "SMS forward failed: ${result.detail.take(160)}"
            Log.e(TAG, msg)
            GatewayService.appendLog(context, msg)
        }
    }
}
