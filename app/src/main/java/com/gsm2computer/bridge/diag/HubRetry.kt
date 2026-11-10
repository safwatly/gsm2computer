package com.gsm2computer.bridge.diag

/**
 * How a live GSM call treats a hub websocket that is not up yet.
 *
 * [HubLinkGrace] still schedules the 1s / 2s / … / 8s backoff and still
 * expires on its own clock. While telephony is off-hook that expiry is not
 * a hangup: the next delay stays at the cap until the call itself ends.
 * [graceMs] of 0 still means do not retry.
 */
object HubRetry {
    /** [android.telecom.Call.STATE_DISCONNECTED]. */
    const val STATE_DISCONNECTED = 7

    /** [android.telecom.Call.STATE_DISCONNECTING]. */
    const val STATE_DISCONNECTING = 10

    fun keepsRetrying(callState: Int): Boolean =
        callState != STATE_DISCONNECTED && callState != STATE_DISCONNECTING

    /**
     * True when [callState] is a live telephony state. Null is unreadable
     * and counts as on-hook, the same as a throw at the call site.
     */
    fun isOffHook(callState: Int?): Boolean =
        callState != null && keepsRetrying(callState)

    /**
     * Milliseconds before the next dial. Null when the call is on-hook or
     * retrying is disabled. After the grace window, the capped backoff.
     */
    fun delayWhileOffHook(grace: HubLinkGrace, nowMs: Long, offHook: Boolean): Long? {
        if (!offHook || grace.graceMs <= 0L) return null
        return grace.retryDelayMs(nowMs) ?: maxOf(grace.maxDelayMs, grace.initialDelayMs)
    }

    /**
     * Keep the GSM call up only while the hub transport is still going to dial.
     * A deliberate close, [stop], or an exhausted retry reports [stillRetrying]
     * false and the bridge tears down. A null [callState] is unreadable and
     * counts as on-hook.
     */
    fun holdLink(stillRetrying: Boolean, callState: Int?): Boolean {
        if (!stillRetrying || callState == null) return false
        return keepsRetrying(callState)
    }
}
