package com.gsm2computer.bridge

/**
 * Hangup edges that must restore the bridged mic. The first one that runs
 * does the work. Later edges still call in, and the gate makes that a no-op.
 * [onCallRemoved] sees a null live call after DISCONNECTED, so it cannot
 * require the live pointer.
 */
internal object HangupRestore {
    fun <T> disconnected(active: T?, call: T, restore: (String) -> Unit): T? {
        if (active !== call) return active
        restore("disconnected")
        return null
    }

    fun <T> removed(active: T?, call: T, restore: (String) -> Unit): T? {
        if (active != null && active !== call) return active
        restore("removed")
        return null
    }
}

/**
 * One saved mic value per bridged call. [restore] returns it once.
 * A second call returns null so a repeated hangup edge does not write again.
 */
internal class MicRestoreGate {
    var open: Boolean = false
        private set
    private var prior: Boolean? = null

    fun open(currentMute: Boolean) {
        if (open) return
        open = true
        prior = currentMute
    }

    fun restore(): Boolean? {
        if (!open) return null
        open = false
        val value = prior
        prior = null
        return value
    }
}
