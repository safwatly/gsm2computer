package com.gsm2computer.bridge

/**
 * Hub control URL injected by Cup over adb.
 *
 * The running app stores the value in [BridgeConfig.KEY_HUB_CONTROL_URL].
 * A copy lives at [DROP_FILE], which is outside the app data directory, so
 * an uninstall that wipes SharedPreferences can restore the tunnel URL.
 * The same file can carry `hub_endpoints` and `device_token`. Boot and
 * `MY_PACKAGE_REPLACED` apply it. A one-line URL still updates only the
 * legacy hub URL and does not replace the walk list.
 *
 * Cup command: `./scripts/set-hub-url.sh 'https://YOUR-TUNNEL.example'`
 * Moto entrances: `./scripts/set-bridge-entrances.sh` (token from a file).
 */
object HubControlUrl {

    const val DROP_FILE = "/data/local/tmp/gsm2computer-hub.url"
    const val ACTION_SET = "com.gsm2computer.bridge.SET_HUB_URL"
    const val EXTRA_URL = "url"

    private val SAFE = Regex(
        "^https?://[A-Za-z0-9._~%-]+(?::[0-9]{1,5})?(?:[/?#][A-Za-z0-9._~%:/?#+=&-]*)?$",
    )
    private val PORT = Regex("^https?://[A-Za-z0-9._~%-]+:(\\d+)")
    private val ENDPOINT = Regex(
        "^(?:https?|wss?)://[A-Za-z0-9._~%-]+(?::[0-9]{1,5})?(?:[/?#][A-Za-z0-9._~%:/?#+=&-]*)?$",
    )
    private val ENDPOINT_PORT = Regex("^(?:https?|wss?)://[A-Za-z0-9._~%-]+:(\\d+)")
    private val JSON_FIELD = Regex(""""hub_control_url"\s*:\s*"([^"]+)"""")
    private val TOKEN = Regex("^[A-Za-z0-9._~+/=-]{1,200}$")

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

    /**
     * Prefs the drop file is compared against. [endpoints] null means the
     * `hub_endpoints` key is absent (build 344). [toString] omits the token.
     */
    class Stored(
        val hubControlUrl: String?,
        val streamTokenUrl: String?,
        val endpoints: String?,
        val deviceToken: String?,
    ) {
        override fun toString(): String {
            val tokenState = if (deviceToken.isNullOrBlank()) "unset" else "set"
            return "Stored(endpoints=${endpoints != null}, token=$tokenState)"
        }
    }

    /** Parsed drop file. Null fields are absent and must not be written. */
    class DropFile(
        val hubControlUrl: String?,
        val endpoints: List<String>?,
        val deviceToken: String?,
    ) {
        override fun toString(): String {
            val tokenState = if (deviceToken.isNullOrEmpty()) "unset" else "set"
            return "DropFile(hub=${hubControlUrl != null}, endpoints=${endpoints?.size}, token=$tokenState)"
        }
    }

    /**
     * What [com.gsm2computer.bridge.config.HubControlInjector] writes.
     * [endpoints] or [deviceToken] null means leave that pref alone.
     */
    class DropPlan(
        val url: String?,
        val clearStreamToken: Boolean,
        val endpoints: List<String>?,
        val deviceToken: String?,
        val changed: Boolean,
    ) {
        override fun toString(): String {
            val tokenState = if (deviceToken.isNullOrEmpty()) "unset" else "set"
            return "DropPlan(url=${url != null}, clearStreamToken=$clearStreamToken, " +
                "endpoints=${endpoints?.size}, token=$tokenState, changed=$changed)"
        }
    }

    /**
     * One parser. A one-line URL or `{"hub_control_url":"..."}` is only the
     * legacy URL. `hub_endpoints=` / `device_token=` lines are the provision file.
     */
    fun parseDrop(raw: String): DropFile? {
        val text = raw.trim().removePrefix("\uFEFF")
        if (text.isEmpty()) return null
        if (text.startsWith("{")) {
            val url = parse(text) ?: return null
            return DropFile(url, null, null)
        }
        val lines = text.lineSequence()
            .map { it.trim() }
            .filter { it.isNotEmpty() && !it.startsWith("#") }
            .toList()
        if (lines.isEmpty()) return null
        if (lines.none { isProvisionKey(it) }) {
            val url = parse(text) ?: return null
            return DropFile(url, null, null)
        }
        var hub: String? = null
        val endpoints = mutableListOf<String>()
        var sawEndpoints = false
        var token: String? = null
        for (line in lines) {
            when {
                line.startsWith("hub_control_url=") -> {
                    hub = normalize(line.substringAfter("=")) ?: return null
                }
                line.startsWith("hub_endpoints=") -> {
                    sawEndpoints = true
                    val value = line.substringAfter("=").trim()
                    if (value.isEmpty()) continue
                    endpoints += normalizeEndpoint(value) ?: return null
                }
                line.startsWith("device_token=") -> {
                    val value = line.substringAfter("=").trim()
                    if (!TOKEN.matches(value)) return null
                    token = value
                }
                else -> return null
            }
        }
        if (sawEndpoints && endpoints.isEmpty()) return null
        if (hub == null && !sawEndpoints && token == null) return null
        return DropFile(hub, if (sawEndpoints) endpoints.toList() else null, token)
    }

    fun planDrop(stored: Stored, raw: String): DropPlan? {
        val drop = parseDrop(raw) ?: return null
        val urlPlan = drop.hubControlUrl?.let { plan(stored.hubControlUrl, stored.streamTokenUrl, it) }
        val endpointsChanged = drop.endpoints != null && drop.endpoints != normalizedStored(stored.endpoints)
        val tokenChanged = drop.deviceToken != null &&
            drop.deviceToken != stored.deviceToken?.trim().orEmpty()
        val urlChanged = urlPlan?.changed == true || urlPlan?.clearToken == true
        return DropPlan(
            url = urlPlan?.url,
            clearStreamToken = urlPlan?.clearToken == true,
            endpoints = drop.endpoints,
            deviceToken = drop.deviceToken,
            changed = urlChanged || endpointsChanged || tokenChanged,
        )
    }

    /** Canonical text. The device token is included only so the file can be written. */
    fun render(hubControlUrl: String?, endpoints: List<String>?, deviceToken: String?): String {
        val lines = mutableListOf("# gsm2computer hub entrances")
        if (!hubControlUrl.isNullOrBlank()) lines += "hub_control_url=$hubControlUrl"
        endpoints?.forEach { lines += "hub_endpoints=$it" }
        if (!deviceToken.isNullOrEmpty()) lines += "device_token=$deviceToken"
        return lines.joinToString("\n") + "\n"
    }

    /**
     * A new legacy URL keeps `hub_endpoints` and `device_token` already in the
     * drop file. A URL-only file stays a URL-only file.
     */
    fun renderForUrl(hubControlUrl: String, existing: DropFile?): String {
        val keepEndpoints = existing?.endpoints
        val keepToken = existing?.deviceToken
        if (keepEndpoints != null || !keepToken.isNullOrEmpty()) {
            return render(hubControlUrl, keepEndpoints, keepToken)
        }
        return hubControlUrl + "\n"
    }

    fun normalizeEndpoint(candidate: String): String? {
        val url = candidate.trim().trimEnd('/')
        if (url.length !in 8..300) return null
        if (!ENDPOINT.matches(url)) return null
        val port = ENDPOINT_PORT.find(url)?.groupValues?.get(1)?.toIntOrNull()
        if (port != null && port !in 1..65535) return null
        return url
    }

    /** Short id for logcat. The hub URL itself is not logged. */
    fun shortId(url: String): String =
        (url.hashCode() and 0xffff).toString(16).padStart(4, '0')

    private fun isProvisionKey(line: String): Boolean =
        line.startsWith("hub_control_url=") ||
            line.startsWith("hub_endpoints=") ||
            line.startsWith("device_token=")

    private fun normalizedStored(raw: String?): List<String>? {
        if (raw == null) return null
        return HubEndpoints.parseEndpointList(raw)
    }
}
