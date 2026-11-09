package com.gsm2computer.bridge.sms

import android.content.Context
import android.content.pm.PackageManager
import android.telephony.SmsManager
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
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/**
 * Polls the hub SMS outbox and sends via [SmsManager]. Runs off the audio
 * WebSocket thread — [GatewayService] calls [pollOnce] from a dedicated loop.
 */
object SmsOutboxPoller {

    private const val TAG = "SmsOutbox"
    private val JSON = "application/json; charset=utf-8".toMediaType()
    private val pollLock = Any()

    private val http = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(15, TimeUnit.SECONDS)
        .writeTimeout(15, TimeUnit.SECONDS)
        .build()

    fun pollOnce(context: Context) {
        synchronized(pollLock) {
            pollOnceLocked(context)
        }
    }

    private fun pollOnceLocked(context: Context) {
        val endpoints = BridgeConfig.resolveEndpoints(BridgeConfig.openPrefs(context))
        if (endpoints.isEmpty()) return
        if (context.checkSelfPermission(android.Manifest.permission.SEND_SMS)
            != PackageManager.PERMISSION_GRANTED
        ) {
            Log.w(TAG, "SEND_SMS not granted — skip outbox poll")
            return
        }
        val result = HubHttpWalk(http).run(endpoints) { endpoint ->
            Request.Builder()
                .url(HubEndpoints.smsOutboxUrl(HubEndpoints.httpOrigin(endpoint.url)))
                .get()
                .build()
        }
        if (!result.opened) {
            Log.w(TAG, "outbox GET HTTP ${result.code} ${result.detail.take(120)}")
            return
        }
        val endpoint = result.endpoint ?: return
        val items = JSONObject(result.body.ifBlank { "{}" }).optJSONArray("items") ?: JSONArray()
        for (i in 0 until items.length()) {
            val item = items.optJSONObject(i) ?: continue
            val id = item.optString("id")
            val to = item.optString("to")
            val body = item.optString("body")
            if (id.isBlank() || to.isBlank()) continue
            sendAndAck(context, endpoint, id, to, body)
        }
    }

    private fun sendAndAck(
        context: Context,
        endpoint: HubEndpoint,
        id: String,
        to: String,
        body: String,
    ) {
        try {
            sendSms(to, body)
            RedactingLogger.i(TAG, "SMS sent to $to")
            GatewayService.appendLog(context, "SMS sent to $to")
            ack(endpoint, id, HubEndpoints.outboxAckJson("sent"))
        } catch (e: Exception) {
            val msg = "SMS send failed: ${e.message}"
            Log.e(TAG, msg)
            GatewayService.appendLog(context, msg)
            ack(endpoint, id, HubEndpoints.outboxAckJson("failed", e.message.orEmpty()))
        }
    }

    private fun sendSms(to: String, body: String) {
        val sms = contextSmsManager()
        val parts = sms.divideMessage(body)
        if (parts.size <= 1) {
            sms.sendTextMessage(to, null, body, null, null)
        } else {
            sms.sendMultipartTextMessage(to, null, parts, null, null)
        }
    }

    @Suppress("DEPRECATION")
    private fun contextSmsManager(): SmsManager = SmsManager.getDefault()

    /**
     * Ack on the same entrance that claimed the row. [HubHttpWalk] adds
     * `X-Gsm-Device-Token` for that endpoint. A one-element list does not
     * try another origin.
     */
    private fun ack(endpoint: HubEndpoint, id: String, payload: String) {
        val url = HubEndpoints.smsOutboxAckUrl(HubEndpoints.httpOrigin(endpoint.url), id)
        if (url.isEmpty()) return
        val result = HubHttpWalk(http).run(listOf(endpoint)) {
            Request.Builder()
                .url(url)
                .post(payload.toRequestBody(JSON))
                .build()
        }
        if (!result.opened) {
            Log.w(TAG, "outbox ack HTTP ${result.code} ${result.detail.take(120)}")
        }
    }
}
