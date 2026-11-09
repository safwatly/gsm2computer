package com.gsm2computer.bridge

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

class HubControlUrlTest {

    @Test
    fun parsesTunnelAndStripsSlash() {
        assertEquals(
            "https://abc-def.trycloudflare.com",
            HubControlUrl.parse("https://abc-def.trycloudflare.com/\n"),
        )
    }

    @Test
    fun parsesHostPortAndJson() {
        val hub = "http://100.101.181.110:8787"
        assertEquals(hub, HubControlUrl.parse(hub))
        assertEquals(
            hub,
            HubControlUrl.parse("""{"hub_control_url":"$hub/"}"""),
        )
    }

    @Test
    fun skipsCommentsAndRewritesWebSocketScheme() {
        val raw = """
            # cup
            wss://hub.example.ts.net:443/bridge
        """.trimIndent()
        assertEquals("https://hub.example.ts.net:443/bridge", HubControlUrl.parse(raw))
    }

    @Test
    fun rejectsUserinfoShellAndBlank() {
        assertNull(HubControlUrl.parse(""))
        assertNull(HubControlUrl.parse("# only a comment"))
        assertNull(HubControlUrl.parse("https://user:pass@host.example"))
        assertNull(HubControlUrl.parse("file:///tmp/x"))
        assertNull(HubControlUrl.parse("https://host.example/$(id)"))
        assertNull(HubControlUrl.parse("https://host.example:99999"))
    }

    @Test
    fun tokenDerivedFromOldHubIsCleared() {
        val old = "https://old.trycloudflare.com"
        val plan = HubControlUrl.plan(old, "$old/token", "https://new.trycloudflare.com")
        assertEquals("https://new.trycloudflare.com", plan?.url)
        assertTrue(plan!!.clearToken)
        assertTrue(plan.changed)
    }

    @Test
    fun customTokenStays() {
        val plan = HubControlUrl.plan(
            "https://old.example",
            "https://worker.example/token",
            "https://new.example",
        )
        assertFalse(plan!!.clearToken)
    }

    @Test
    fun missingPrefsCountsAsAChange() {
        val plan = HubControlUrl.plan(null, null, "https://new.example")
        assertTrue(plan!!.changed)
        assertFalse(plan.clearToken)
    }

    @Test
    fun sameUrlIsUnchanged() {
        val plan = HubControlUrl.plan("https://new.example/", null, "https://new.example")
        assertFalse(plan!!.changed)
        assertFalse(plan.clearToken)
    }

    @Test
    fun cupScriptAndManifestUseTheSameContract() {
        val script = File("../scripts/set-hub-url.sh").readText()
        val manifest = File("src/main/AndroidManifest.xml").readText()
        assertTrue(script.contains(HubControlUrl.DROP_FILE))
        assertTrue(script.contains(HubControlUrl.ACTION_SET))
        assertTrue(manifest.contains(HubControlUrl.ACTION_SET))
        assertTrue(manifest.contains("HubControlReceiver"))
        assertTrue(manifest.contains("android.permission.DUMP"))
    }
}
