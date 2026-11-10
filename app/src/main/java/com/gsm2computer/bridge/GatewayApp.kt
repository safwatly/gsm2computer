package com.gsm2computer.bridge

import android.app.Application
import android.content.Context
import android.os.StrictMode
import android.util.Log
import com.gsm2computer.bridge.BuildConfig

class GatewayApp : Application() {
    override fun onCreate() {
        super.onCreate()
        app = this
        // Allow network on main thread — this is a headless gateway service,
        // not a UI app.  GSM callbacks arrive on the main thread and need to
        // trigger short UDP sends (SIP INVITE / BYE).
        StrictMode.setThreadPolicy(
            StrictMode.ThreadPolicy.Builder().permitAll().build()
        )
        Log.i("GatewayApp", "GSM2Computer Bridge v${BuildConfig.VERSION_NAME} started")
    }

    companion object {
        @Volatile
        private var app: GatewayApp? = null

        /** Process context. Null in a unit test that never starts the app. */
        fun context(): Context? = app
    }
}
