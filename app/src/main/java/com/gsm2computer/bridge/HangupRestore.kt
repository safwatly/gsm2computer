package com.gsm2computer.bridge

/**
 * Hangup edges that must restore the bridged mic on the Moto profile.
 * [onCallRemoved] sees a null live call after DISCONNECTED, so it cannot
 * require the live pointer. Other profiles keep the pre-352 onCallRemoved path.
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
 * One saved mic value per bridged call. The value stays available until
 * [commit], which runs only after AudioManager accepts the write.
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

    fun pending(): Boolean? = if (open) prior else null

    /**
     * Writes [pending] when [canWrite] is true. A throw from [setter] leaves
     * the value in place for the next hangup edge.
     */
    fun write(canWrite: Boolean, setter: (Boolean) -> Unit): Boolean {
        val value = pending() ?: return true
        if (!canWrite) return false
        setter(value)
        commit()
        return true
    }

    fun commit() {
        open = false
        prior = null
    }
}
