package com.gsm2computer.bridge.realtime

/**
 * Whether one uplink frame may join the OkHttp WebSocket send queue.
 *
 * OkHttp queues until 16 MiB, then starts a close that sits behind that data.
 * Protocol pings share the same writer task, so a TCP window stuck at zero
 * blocks the missed-pong check (ping interval 20 s, fail on the next ping,
 * about 40 s). Past [maxQueuedBytes] we drop audio instead. If the queue
 * stays over the cap for [wedgeAfterMs], the socket is wedged and the caller
 * should cancel it so the reconnect loop runs.
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
    private val lock = Any()

    enum class Decision {
        SEND,
        DROP,
        WEDGE,
    }

    fun decide(queuedBytes: Long, nowMs: Long): Decision = synchronized(lock) {
        if (queuedBytes <= maxQueuedBytes) {
            overSinceMs = null
            return Decision.SEND
        }
        val since = overSinceMs ?: nowMs.also { overSinceMs = it }
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
        /** About two seconds of 48 kHz PCM JSON, far under OkHttp's 16 MiB close. */
        const val DEFAULT_MAX_QUEUED_BYTES = 256L * 1024L
        const val DEFAULT_WEDGE_MS = 3_000L
    }
}
