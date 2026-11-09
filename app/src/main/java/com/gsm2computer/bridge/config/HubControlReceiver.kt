package com.gsm2computer.bridge.config

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import com.gsm2computer.bridge.HubControlUrl
import kotlin.concurrent.thread

/**
 * Shell-only entry for Cup. The manifest requires [android.permission.DUMP],
 * which `adb shell` holds and third-party apps do not.
 *
 * `adb shell am broadcast -a com.gsm2computer.bridge.SET_HUB_URL
 *   -n com.gsm2computer.bridge/.config.HubControlReceiver
 *   --es url 'https://YOUR-TUNNEL.example'`
 *
 * With no `url` extra, the receiver reads [HubControlUrl.DROP_FILE].
 */
class HubControlReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != HubControlUrl.ACTION_SET) return
        val pending = goAsync()
        val app = context.applicationContext
        val extra = intent.getStringExtra(HubControlUrl.EXTRA_URL)
        thread(name = "hub-url") {
            try {
                if (!extra.isNullOrBlank()) {
                    HubControlInjector.applyUrl(app, extra)
                } else {
                    HubControlInjector.applyDropFile(app, reload = true, reloadAsync = false)
                }
            } finally {
                pending.finish()
            }
        }
    }
}
