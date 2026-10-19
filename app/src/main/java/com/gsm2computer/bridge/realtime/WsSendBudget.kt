package com.gsm2computer.bridge.realtime

/**
 * Whether one uplink frame may join the OkHttp WebSocket send queue.
 *
 * OkHttp queues until 16 MiB, then starts a close that sits behind that data.
 * Protocol pings share the same writer task, so a TCP window stuck at zero
 * blocks the missed-pong check (ping interval 20 s, fail on the next ping,
 * about 40 s). Past [maxQueuedBytes] we drop audio. Cancel only when that
 * queue does not drain for [wedgeAfterMs]; a hiccup that still ACKs must
 * not reconnect.
 */
class WsSendBudget(
    val maxQueuedBytes: Long = DEFAULT_MAX_QUEUED_BYTES,
    val wedgeAfterMs: Long = DEFAULT_WEDGE_MS,
) {
    var droppedFrames: Long = 0
        private set

    var acceptedFrames: Long = 0
        private set

    private var overSinceMs: Long? = null
    private var highWater: Long = 0
    private val lock = Any()

    enum class Decision {
        SEND,
        DROP,
        WEDGE,
    }

    fun decide(queuedBytes: Long, nowMs: Long): Decision = synchronized(lock) {
        if (queuedBytes <= maxQueuedBytes) {
            overSinceMs = null
            highWater = queuedBytes
            return Decision.SEND
        }
        val since = overSinceMs
        if (since == null || queuedBytes < highWater) {
            overSinceMs = nowMs
            highWater = queuedBytes
            return Decision.DROP
        }
        if (queuedBytes > highWater) highWater = queuedBytes
        if (nowMs - since >= wedgeAfterMs) Decision.WEDGE else Decision.DROP
    }

    fun noteSent() = synchronized(lock) {
        acceptedFrames++
    }

    fun noteDropped() = synchronized(lock) {
        droppedFrames++
    }

    fun counts(): Pair<Long, Long> = synchronized(lock) {
        acceptedFrames to droppedFrames
    }

    companion object {
        /**
         * About 16 s of 16 kHz s16 mono (~32 KB/s). A short Wi-Fi stall fits
         * under this; OkHttp's own close is still 16 MiB.
         */
        const val DEFAULT_MAX_QUEUED_BYTES = 512L * 1024L

        /** Drop while a full queue is not draining. Cancel only after this. */
        const val DEFAULT_WEDGE_MS = 15_000L
    }
}
