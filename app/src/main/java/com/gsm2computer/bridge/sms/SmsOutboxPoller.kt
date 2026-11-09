package com.gsm2computer.bridge.sms

import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
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
    private const val SENT_TIMEOUT_MS = 45_000L
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
        val outcome = try {
            awaitRadioResult(context, id, to, body)
        } catch (e: Exception) {
            SmsSendOutcome.Ack("failed", "send threw ${e.javaClass.simpleName}")
        }
        if (outcome.status == "sent") {
            RedactingLogger.i(TAG, "SMS sent to $to")
            GatewayService.appendLog(context, "SMS sent to $to")
        } else {
            val msg = "SMS ${outcome.status} to $to: ${outcome.error}"
            RedactingLogger.e(TAG, msg)
            GatewayService.appendLog(context, msg)
        }
        ack(endpoint, id, HubEndpoints.outboxAckJson(outcome.status, outcome.error))
    }

    /**
     * SmsManager returns before the carrier accepts the message. The sent
     * PendingIntent is the result. Ack only after every part reports, or
     * after [SENT_TIMEOUT_MS] with no callback.
     */
    private fun awaitRadioResult(context: Context, id: String, to: String, body: String): SmsSendOutcome.Ack {
        val sms = contextSmsManager()
        val parts = sms.divideMessage(body)
        val count = if (parts.size <= 1) 1 else parts.size
        val slot = SmsSentHub.register(id, count)
        try {
            val flags = PendingIntent.FLAG_ONE_SHOT or PendingIntent.FLAG_UPDATE_CURRENT or
                if (Build.VERSION.SDK_INT >= 31) PendingIntent.FLAG_MUTABLE else 0
            val intents = ArrayList<PendingIntent>(count)
            for (index in 0 until count) {
                val broadcast = Intent(context, SmsSentReceiver::class.java)
                    .putExtra("id", id)
                    .putExtra("part", index)
                val request = (id.hashCode() xor index) and 0x7fffffff
                intents += PendingIntent.getBroadcast(context, request, broadcast, flags)
            }
            if (count == 1) {
                sms.sendTextMessage(to, null, body, intents[0], null)
            } else {
                sms.sendMultipartTextMessage(to, null, parts, intents, null)
            }
            val finished = slot.latch.await(SENT_TIMEOUT_MS, TimeUnit.MILLISECONDS)
            return SmsSendOutcome.decide(
                slot.results.toList(),
                timedOut = !finished,
                rpErrors = slot.rpErrors.toList(),
            )
        } finally {
            SmsSentHub.finish(id)
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
