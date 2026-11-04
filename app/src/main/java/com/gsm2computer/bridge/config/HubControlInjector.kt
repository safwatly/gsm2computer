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
            removeDropFile()
            BridgeLog.w("hub url file not updated")
        }
        return commitUrl(context, url, reload = true, reloadAsync = false)
    }

    /** Read [HubControlUrl.DROP_FILE]. No-op when it matches the stored URL. */
    fun applyDropFile(
        context: Context,
        reload: Boolean = true,
        reloadAsync: Boolean = true,
    ): Boolean {
        val raw = readDropFile()
        if (raw.isBlank()) return false
        val url = HubControlUrl.parse(raw)
        if (url == null) {
            noteReject(raw)
            return false
        }
        return commitUrl(context, url, reload, reloadAsync)
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
            removeDropFile()
            BridgeLog.w("hub url file not updated")
        }
    }

    fun replaceDropFile(context: Context, url: String): Boolean {
        val safe = HubControlUrl.parse(url) ?: return false
        return try {
            val tmp = File(context.cacheDir, "hub-control.url")
            tmp.writeText(safe + "\n")
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
        val prefs = BridgeConfig.openPrefs(context)
        val stored = if (prefs.contains(BridgeConfig.KEY_HUB_CONTROL_URL)) {
            prefs.getString(BridgeConfig.KEY_HUB_CONTROL_URL, null)
        } else {
            null
        }
        val token = prefs.getString(BridgeConfig.KEY_STREAM_TOKEN_URL, null)
        val plan = HubControlUrl.plan(stored, token, url) ?: return false
        if (!plan.changed && !plan.clearToken) return false
        val editor = prefs.edit()
            .putString(BridgeConfig.KEY_HUB_CONTROL_URL, plan.url)
            .putString(BridgeConfig.KEY_HUB_URL_DROP, plan.url)
            .putBoolean(BridgeConfig.KEY_STREAM_ENABLED, true)
        if (plan.clearToken) editor.remove(BridgeConfig.KEY_STREAM_TOKEN_URL)
        editor.commit()
        BridgeLog.i("hub url applied id=${HubControlUrl.shortId(plan.url)}")
        GatewayService.appendLog(context, "Hub control URL updated")
        if (reload) GatewayService.requestHubReload(context, reloadAsync)
        return true
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
