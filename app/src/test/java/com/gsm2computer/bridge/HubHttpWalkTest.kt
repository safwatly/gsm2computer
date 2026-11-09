package com.gsm2computer.bridge

import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.concurrent.TimeUnit

class HubHttpWalkTest {

    @Test
    fun ackUsesTheSameEndpointAndDeviceTokenAsTheGet() {
        val secret = "device-token-test-value"
        val server = MockWebServer()
        server.start()
        try {
            server.enqueue(MockResponse().setBody("""{"ok":true,"items":[]}"""))
            val origin = server.url("/").toString()
            val endpoint = HubEndpoint(origin, secret)
            val http = OkHttpClient.Builder()
                .connectTimeout(2, TimeUnit.SECONDS)
                .readTimeout(2, TimeUnit.SECONDS)
                .build()
            val got = HubHttpWalk(http).run(listOf(endpoint)) { target ->
                Request.Builder()
                    .url(HubEndpoints.smsOutboxUrl(HubEndpoints.httpOrigin(target.url)))
                    .get()
                    .build()
            }
            assertTrue(got.opened)
            assertEquals(secret, got.endpoint?.deviceToken)
            val get = checkNotNull(server.takeRequest(2, TimeUnit.SECONDS))
            assertEquals(secret, get.getHeader(HubEndpoints.DEVICE_TOKEN_HEADER))

            server.enqueue(MockResponse().setBody("""{"ok":true}"""))
            val ack = HubHttpWalk(http).run(listOf(checkNotNull(got.endpoint))) {
                Request.Builder()
                    .url(HubEndpoints.smsOutboxAckUrl(HubEndpoints.httpOrigin(origin), "row-1"))
                    .post("""{"status":"sent"}""".toRequestBody(null))
                    .build()
            }
            assertTrue(ack.opened)
            val posted = checkNotNull(server.takeRequest(2, TimeUnit.SECONDS))
            assertEquals(secret, posted.getHeader(HubEndpoints.DEVICE_TOKEN_HEADER))
            assertTrue(posted.path!!.endsWith("/sms/outbox/row-1/ack"))
            assertEquals(get.requestUrl?.host, posted.requestUrl?.host)
        } finally {
            server.shutdown()
        }
    }
}
