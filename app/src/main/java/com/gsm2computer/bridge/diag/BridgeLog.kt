package com.gsm2computer.bridge.diag

import android.util.Log

/**
 * One logcat tag for the call audio path. Filter with `adb logcat -s GSM2Bridge`.
 * Messages stay short: no tokens, no hub URLs.
 */
object BridgeLog {
    const val TAG = "GSM2Bridge"

    fun i(message: String) {
        Log.i(TAG, message)
    }

    fun w(message: String) {
        Log.w(TAG, message)
    }

    fun e(message: String) {
        Log.e(TAG, message)
    }
}
