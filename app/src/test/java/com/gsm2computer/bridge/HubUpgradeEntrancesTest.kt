package com.gsm2computer.bridge

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Build 344 prefs have hub_control_url and no hub_endpoints or device_token.
 * Build 346 walks the ordered defaults and takes the token from the drop file.
 */
class HubUpgradeEntrancesTest {

    private val legacyHub = "http://hub.mining-ling.ts.net:8787"
    private val token = "upgrade-token-value"

    @Test
    fun upgradeFrom344PrefsGetsBothEntrancesInOrderAndTheToken() {
        val stored = HubControlUrl.Stored(
            hubControlUrl = legacyHub,
            streamTokenUrl = "$legacyHub/token",
            endpoints = null,
            deviceToken = null,
        )
        val before = HubEndpoints.resolveEndpointUrls(stored.endpoints, defaultList())
        assertEquals(listOf(PUBLIC, TAILSCALE), before)
        val bare = before.map { HubEndpoint(it, "") }
        val warning = HubEndpoints.missingDeviceTokenWarning(bare)
        assertTrue(warning!!.contains(PUBLIC))
        assertTrue(warning.contains(TAILSCALE))
        assertTrue(warning.contains("walk continues"))

        val drop = motoDrop(token)
        val applied = HubControlUrl.planDrop(stored, drop)
        assertTrue(applied!!.changed)
        assertTrue(applied.clearStreamToken)
        assertEquals(listOf(PUBLIC, TAILSCALE), applied.endpoints)
        assertEquals(token, applied.deviceToken)
        assertFalse(applied.toString().contains(token))
        assertFalse(stored.toString().contains(token))

        val afterUrls = HubEndpoints.resolveEndpointUrls(
            applied.endpoints!!.joinToString("\n"),
            legacyHub,
        )
        assertEquals(listOf(PUBLIC, TAILSCALE), afterUrls)
        val walk = afterUrls.map { HubEndpoint(it, applied.deviceToken!!) }
        assertEquals(token, walk[0].deviceToken)
        assertEquals(token, walk[1].deviceToken)
        assertFalse(walk.joinToString().contains(token))
        assertNull(HubEndpoints.missingDeviceTokenWarning(walk))

        val again = HubControlUrl.planDrop(
            HubControlUrl.Stored(
                hubControlUrl = applied.url,
                streamTokenUrl = "",
                endpoints = applied.endpoints!!.joinToString("\n"),
                deviceToken = applied.deviceToken,
            ),
            drop,
        )
        assertFalse(again!!.changed)
    }

    @Test
    fun legacyDropUrlDoesNotReplaceTheWalkOrTheToken() {
        val stored = HubControlUrl.Stored(
            hubControlUrl = legacyHub,
            streamTokenUrl = "",
            endpoints = null,
            deviceToken = null,
        )
        val plan = HubControlUrl.planDrop(stored, "$legacyHub/\n")
        assertNull(plan!!.endpoints)
        assertNull(plan.deviceToken)
        assertFalse(plan.changed)
        assertEquals(
            listOf(PUBLIC, TAILSCALE),
            HubEndpoints.resolveEndpointUrls(null, defaultList()),
        )
    }

    @Test
    fun missingTokenOnThePublicEntranceIsRetryable() {
        val detail = HubEndpoints.missingTokenRejection(PUBLIC, 401, tokenBlank = true)
        assertEquals(
            "device token unset; $PUBLIC rejected the call, trying the next endpoint",
            detail,
        )
        assertEquals(DialStatus.RETRYABLE, classifyHubStatus(401, ""))
        assertNull(HubEndpoints.missingTokenRejection(PUBLIC, 401, tokenBlank = false))
        assertNull(HubEndpoints.missingTokenRejection(TAILSCALE, 500, tokenBlank = true))
    }

    @Test
    fun blankTokenFallsThroughToTailscale() {
        val public = HubEndpoint(PUBLIC, "")
        val tailscale = HubEndpoint(TAILSCALE, "")
        val seen = mutableListOf<String>()
        val routine = HubConnectionRoutine(listOf(public, tailscale), sleep = {}) { request ->
            seen += request.endpoint.url
            val detail = HubEndpoints.missingTokenRejection(
                request.endpoint.url,
                401,
                request.endpoint.deviceToken.isBlank(),
            )
            if (request.endpoint.url == PUBLIC) {
                DialResponse(DialStatus.RETRYABLE, 401, detail!!)
            } else {
                DialResponse(DialStatus.OPENED, 101, "")
            }
        }
        val result = routine.connect()
        assertEquals(listOf(PUBLIC, TAILSCALE), seen)
        assertTrue(result.opened)
        assertEquals(TAILSCALE, result.url)
        assertTrue(result.attempts[0].detail.contains("device token unset"))
        assertTrue(result.attempts[0].detail.contains("trying the next endpoint"))
        assertFalse(result.summary().contains(token))
    }

    @Test
    fun rejectedTokenDoesNotApplyAPartialDrop() {
        val raw = """
            hub_endpoints=$PUBLIC/
            device_token=has space
        """.trimIndent()
        assertNull(HubControlUrl.parseDrop(raw))
    }

    @Test
    fun motoScriptWritesTheDropFileAsRootWithoutPrintingTheToken() {
        val script = File("../scripts/set-bridge-entrances.sh").readText()
        assertTrue(script.contains(HubControlUrl.DROP_FILE))
        assertTrue(script.contains(HubControlUrl.ACTION_SET))
        assertTrue(script.contains("su -c"))
        assertTrue(script.contains("wss://gsm.ta3leam.com/"))
        assertTrue(script.contains("ws://100.113.222.47:8787/"))
        assertFalse(script.contains("echo \"\$device_token_value\""))
        assertFalse(script.contains("set -x"))
        assertFalse(script.contains("--es url"))
    }

    private fun defaultList(): String = HubEndpoints.defaultEndpointUrls().joinToString("\n")

    /** Same lines scripts/set-bridge-entrances.sh writes. */
    private fun motoDrop(deviceToken: String): String = """
        # gsm2computer hub entrances
        hub_control_url=http://100.113.222.47:8787
        hub_endpoints=wss://gsm.ta3leam.com/
        hub_endpoints=ws://100.113.222.47:8787/
        device_token=$deviceToken
    """.trimIndent() + "\n"

    private companion object {
        const val PUBLIC = "wss://gsm.ta3leam.com"
        const val TAILSCALE = "ws://100.113.222.47:8787"
    }
}
