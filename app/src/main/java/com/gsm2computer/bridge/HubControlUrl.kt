package com.gsm2computer.bridge

/**
 * Hub control URL injected by Cup over adb.
 *
 * The running app stores the value in [BridgeConfig.KEY_HUB_CONTROL_URL].
 * A copy lives at [DROP_FILE], which is outside the app data directory, so
 * an uninstall that wipes SharedPreferences can restore the tunnel URL.
 *
 * Cup command: `./scripts/set-hub-url.sh 'https://YOUR-TUNNEL.example'`
 */
object HubControlUrl {

    const val DROP_FILE = "/data/local/tmp/gsm2computer-hub.url"
    const val ACTION_SET = "com.gsm2computer.bridge.SET_HUB_URL"
    const val EXTRA_URL = "url"

    private val SAFE = Regex(
        "^https?://[A-Za-z0-9._~%-]+(?::[0-9]{1,5})?(?:[/?#][A-Za-z0-9._~%:/?#+=&-]*)?$",
    )
    private val PORT = Regex("^https?://[A-Za-z0-9._~%-]+:(\\d+)")
    private val JSON_FIELD = Regex(""""hub_control_url"\s*:\s*"([^"]+)"""")

    data class Plan(
        val url: String,
        val clearToken: Boolean,
        val changed: Boolean,
    )

    /** Accept a one-line URL, a `#` comment header, or `{"hub_control_url":"..."}`. */
    fun parse(raw: String): String? {
        val text = raw.trim().removePrefix("\uFEFF")
        if (text.isEmpty()) return null
        val candidate = if (text.startsWith("{")) {
            JSON_FIELD.find(text)?.groupValues?.get(1)
        } else {
            text.lineSequence()
                .map { it.trim() }
                .firstOrNull { it.isNotEmpty() && !it.startsWith("#") }
        } ?: return null
        return normalize(candidate)
    }

    fun normalize(candidate: String): String? {
        var url = candidate.trim().trimEnd('/')
        if (url.startsWith("wss://")) url = "https://" + url.removePrefix("wss://")
        else if (url.startsWith("ws://")) url = "http://" + url.removePrefix("ws://")
        if (url.length !in 8..300) return null
        if (!SAFE.matches(url)) return null
        val port = PORT.find(url)?.groupValues?.get(1)?.toIntOrNull()
        if (port != null && port !in 1..65535) return null
        return url
    }

    /**
     * True when [token] is the hub origin or a path under it (`{hub}/token`),
     * so a new tunnel should drop the override and follow `{newHub}/token`.
     */
    fun tokenFollowsHub(hub: String, token: String): Boolean {
        val base = hub.trim().trimEnd('/')
        val value = token.trim()
        if (base.isEmpty() || value.isEmpty()) return false
        return value == base || value.startsWith("$base/")
    }

    fun plan(storedHub: String?, storedToken: String?, raw: String): Plan? {
        val url = parse(raw) ?: return null
        val current = storedHub?.trim()?.trimEnd('/')?.ifEmpty { null }
        val token = storedToken?.trim().orEmpty()
        val changed = current != url
        val clear = changed && current != null && tokenFollowsHub(current, token)
        return Plan(url, clear, changed)
    }

    /** Short id for logcat. The hub URL itself is not logged. */
    fun shortId(url: String): String =
        (url.hashCode() and 0xffff).toString(16).padStart(4, '0')
}
