package com.gsm2computer.bridge.config

import android.content.Context
import com.gsm2computer.bridge.BridgeConfig
import com.gsm2computer.bridge.HubControlUrl
import com.gsm2computer.bridge.RootShell
import com.gsm2computer.bridge.diag.BridgeLog
import com.gsm2computer.bridge.service.GatewayService
import java.io.File

/**
 * Writes the hub control URL into SharedPreferences and asks [GatewayService]
 * to pick it up. SMS polls re-read prefs; an idle bridge restarts so the next
 * call uses the new tunnel. An in-call bridge keeps the current audio socket.
 */
object HubControlInjector {

    private var lastRejectHash = 0

    fun applyUrl(context: Context, raw: String): Boolean {
        val url = HubControlUrl.parse(raw)
        if (url == null) {
            BridgeLog.w("hub url rejected")
            return false
        }
        if (!replaceDropFile(context, url)) {
            if (!dropKeepsProvision()) removeDropFile()
            BridgeLog.w("hub url file not updated")
        }
        return commitUrl(context, url, reload = true, reloadAsync = false)
    }

    /**
     * Read [HubControlUrl.DROP_FILE]. A one-line URL updates the legacy hub URL.
     * `hub_endpoints` and `device_token` lines update those prefs. No-op when
     * nothing changed. The device token is not logged.
     */
    fun applyDropFile(
        context: Context,
        reload: Boolean = true,
        reloadAsync: Boolean = true,
    ): Boolean {
        val raw = readDropFile()
        if (raw.isBlank()) return false
        val plan = HubControlUrl.planDrop(stored(context), raw)
        if (plan == null) {
            noteReject(raw)
            return false
        }
        return commitDrop(context, plan, reload, reloadAsync)
    }

    /**
     * Settings dialog save. Updates the drop file before the caller writes
     * prefs, so the poller does not put the previous tunnel back.
     */
    fun stageDropFile(context: Context, hubUrl: String) {
        val url = HubControlUrl.parse(hubUrl)
        if (url == null) {
            removeDropFile()
            return
        }
        if (!replaceDropFile(context, url)) {
            if (!dropKeepsProvision()) removeDropFile()
            BridgeLog.w("hub url file not updated")
        }
    }

    private fun dropKeepsProvision(): Boolean {
        val drop = HubControlUrl.parseDrop(readDropFile()) ?: return false
        return drop.endpoints != null || !drop.deviceToken.isNullOrEmpty()
    }

    fun replaceDropFile(context: Context, url: String): Boolean {
        val safe = HubControlUrl.parse(url) ?: return false
        val existing = HubControlUrl.parseDrop(readDropFile())
        return writeDrop(context, HubControlUrl.renderForUrl(safe, existing))
    }

    fun removeDropFile(): Boolean {
        return try {
            RootShell.init()
            RootShell.exec("rm -f ${HubControlUrl.DROP_FILE}") == 0
        } catch (_: Exception) {
            false
        }
    }

    private fun commitUrl(
        context: Context,
        url: String,
        reload: Boolean,
        reloadAsync: Boolean,
    ): Boolean {
        val current = stored(context)
        val plan = HubControlUrl.planDrop(
            HubControlUrl.Stored(current.hubControlUrl, current.streamTokenUrl, null, null),
            url,
        ) ?: return false
        return commitDrop(context, plan, reload, reloadAsync)
    }

    private fun commitDrop(
        context: Context,
        plan: HubControlUrl.DropPlan,
        reload: Boolean,
        reloadAsync: Boolean,
    ): Boolean {
        if (!plan.changed && !plan.clearStreamToken) return false
        val prefs = BridgeConfig.openPrefs(context)
        val editor = prefs.edit()
        if (plan.url != null) {
            editor.putString(BridgeConfig.KEY_HUB_CONTROL_URL, plan.url)
                .putString(BridgeConfig.KEY_HUB_URL_DROP, plan.url)
                .putBoolean(BridgeConfig.KEY_STREAM_ENABLED, true)
        }
        if (plan.clearStreamToken) editor.remove(BridgeConfig.KEY_STREAM_TOKEN_URL)
        if (plan.endpoints != null) {
            editor.putString(BridgeConfig.KEY_HUB_ENDPOINTS, plan.endpoints.joinToString("\n"))
        }
        if (plan.deviceToken != null) {
            editor.putString(BridgeConfig.KEY_DEVICE_TOKEN, plan.deviceToken)
        }
        editor.commit()
        val tokenState = if (plan.deviceToken == null) "unchanged" else "set"
        val endpoints = plan.endpoints?.size?.toString() ?: "unchanged"
        val id = plan.url?.let { HubControlUrl.shortId(it) } ?: "-"
        BridgeLog.i("hub drop applied id=$id endpoints=$endpoints token=$tokenState")
        GatewayService.appendLog(context, "Hub entrances updated")
        if (reload) GatewayService.requestHubReload(context, reloadAsync)
        return true
    }

    private fun stored(context: Context): HubControlUrl.Stored {
        val prefs = BridgeConfig.openPrefs(context)
        val hub = if (prefs.contains(BridgeConfig.KEY_HUB_CONTROL_URL)) {
            prefs.getString(BridgeConfig.KEY_HUB_CONTROL_URL, null)
        } else {
            null
        }
        val endpoints = if (prefs.contains(BridgeConfig.KEY_HUB_ENDPOINTS)) {
            prefs.getString(BridgeConfig.KEY_HUB_ENDPOINTS, "")
        } else {
            null
        }
        return HubControlUrl.Stored(
            hubControlUrl = hub,
            streamTokenUrl = prefs.getString(BridgeConfig.KEY_STREAM_TOKEN_URL, null),
            endpoints = endpoints,
            deviceToken = prefs.getString(BridgeConfig.KEY_DEVICE_TOKEN, null),
        )
    }

    private fun writeDrop(context: Context, body: String): Boolean {
        return try {
            val tmp = File(context.cacheDir, "hub-control.url")
            tmp.writeText(body)
            RootShell.init()
            val code = RootShell.exec(
                "cp ${tmp.absolutePath} ${HubControlUrl.DROP_FILE} && chmod 644 ${HubControlUrl.DROP_FILE}",
            )
            tmp.delete()
            code == 0
        } catch (e: Exception) {
            BridgeLog.w("hub url file not updated (${e.javaClass.simpleName})")
            false
        }
    }

    private fun readDropFile(): String {
        try {
            val file = File(HubControlUrl.DROP_FILE)
            if (file.canRead()) return file.readText()
        } catch (_: Exception) {
        }
        return try {
            RootShell.init()
            RootShell.execForOutput("cat ${HubControlUrl.DROP_FILE} 2>/dev/null")
        } catch (_: Exception) {
            ""
        }
    }

    private fun noteReject(raw: String) {
        val hash = raw.hashCode()
        if (hash == lastRejectHash) return
        lastRejectHash = hash
        BridgeLog.w("hub url rejected")
    }
}
