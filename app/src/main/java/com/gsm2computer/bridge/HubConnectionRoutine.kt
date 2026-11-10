package com.gsm2computer.bridge

import java.util.Collections
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicReference

/**
 * One walk over an ordered list of hub endpoints.
 *
 * The first connect and a mid-call reconnect both call [connect] or
 * [connectRacing]. A retryable failure tries the next endpoint in the same
 * pass. [DialStatus.LATER] ends the pass and the next pass starts after
 * [Backoff] — that is how a 409 is retried while the hub may be opening the
 * relink grace window, without opening a second socket in the same pass.
 * [DialStatus.STOP] ends the walk so a busy hub is not dialed again. Nothing
 * in this class treats one entrance differently from another: the dial
 * closure sees [DialRequest] only.
 *
 * [connectRacing] starts every endpoint in the pass at once and keeps the
 * first [DialStatus.OPENED]. A hung public socket cannot spend the whole
 * attempt budget before the Tailscale socket is tried.
 */
class HubConnectionRoutine(
    private val endpoints: List<HubEndpoint>,
    private val backoff: Backoff = Backoff(),
    private val sleep: (Long) -> Unit = { delay -> Thread.sleep(delay) },
    private val onAttempt: (AttemptRecord) -> Unit = { _ -> },
    private val onRace: (EndpointRace) -> Unit = { _ -> },
    private val dial: (DialRequest) -> DialResponse,
) {
    fun connect(
        sessionId: String = "",
        maxPasses: Int = 1,
        proceed: () -> Boolean = { true },
    ): ConnectResult {
        val secrets = endpoints.map { it.deviceToken }
        val attempts = mutableListOf<AttemptRecord>()
        if (endpoints.isEmpty() || maxPasses < 1) {
            return ConnectResult(
                opened = false,
                cancelled = false,
                stopped = true,
                pass = 0,
                index = -1,
                url = "",
                sessionId = sessionId,
                attempts = attempts,
                detail = scrubSecrets("no endpoints", secrets),
            )
        }
        var lastDetail = ""
        for (pass in 0 until maxPasses) {
            if (!proceed()) return cancelled(sessionId, attempts, secrets)
            for ((index, endpoint) in endpoints.withIndex()) {
                if (!proceed()) return cancelled(sessionId, attempts, secrets)
                val response = try {
                    dial(DialRequest(endpoint, sessionId))
                } catch (e: Exception) {
                    DialResponse(
                        DialStatus.RETRYABLE,
                        0,
                        e.message ?: e.javaClass.simpleName,
                    )
                }
                val detail = scrubSecrets(response.detail, secrets)
                lastDetail = detail
                val record = AttemptRecord(
                    pass = pass,
                    index = index,
                    url = endpoint.url,
                    sessionId = sessionId,
                    outcome = response.status.name.lowercase(),
                    httpStatus = response.httpStatus,
                    detail = detail,
                )
                attempts += record
                notifyAttempt(record)
                when (response.status) {
                    DialStatus.OPENED -> return ConnectResult(
                        opened = true,
                        cancelled = false,
                        stopped = false,
                        pass = pass,
                        index = index,
                        url = endpoint.url,
                        sessionId = sessionId,
                        attempts = attempts.toList(),
                        detail = detail,
                    )
                    DialStatus.STOP -> return ConnectResult(
                        opened = false,
                        cancelled = false,
                        stopped = true,
                        pass = pass,
                        index = index,
                        url = endpoint.url,
                        sessionId = sessionId,
                        attempts = attempts.toList(),
                        detail = detail,
                    )
                    DialStatus.LATER -> break
                    DialStatus.RETRYABLE -> Unit
                }
            }
            if (pass < maxPasses - 1) {
                if (!proceed()) return cancelled(sessionId, attempts, secrets)
                val delay = backoff.delayAfterPass(pass)
                if (delay > 0) sleep(delay)
            }
        }
        return ConnectResult(
            opened = false,
            cancelled = false,
            stopped = false,
            pass = maxPasses - 1,
            index = endpoints.lastIndex,
            url = "",
            sessionId = sessionId,
            attempts = attempts.toList(),
            detail = lastDetail,
        )
    }

    /**
     * Dial every endpoint in a pass concurrently. The first [DialStatus.OPENED]
     * wins. A [DialStatus.STOP] or [DialStatus.LATER] from one leg does not
     * seal or cancel the others while any leg is still inside [dial]: the
     * hub may already have admitted that leg and its 101 is only slow.
     * The pass seals when every leg has finished without a 101. [DialStatus.STOP]
     * then ends the walk, so a busy hub is not given another pass.
     */
    fun connectRacing(
        sessionId: String = "",
        maxPasses: Int = 1,
        proceed: () -> Boolean = { true },
    ): ConnectResult {
        val secrets = endpoints.map { it.deviceToken }
        if (endpoints.isEmpty() || maxPasses < 1) {
            return ConnectResult(
                opened = false,
                cancelled = false,
                stopped = true,
                pass = 0,
                index = -1,
                url = "",
                sessionId = sessionId,
                attempts = emptyList(),
                detail = scrubSecrets("no endpoints", secrets),
            )
        }
        val attempts = Collections.synchronizedList(mutableListOf<AttemptRecord>())
        var lastDetail = ""
        for (pass in 0 until maxPasses) {
            if (!proceed()) return cancelled(sessionId, copyAttempts(attempts), secrets)
            val race = EndpointRace()
            notifyRace(race)
            val opened = AtomicReference<ConnectResult?>(null)
            val terminal = AtomicReference<DialStatus?>(null)
            val signal = CountDownLatch(1)
            val finished = AtomicInteger(0)
            val threads = endpoints.mapIndexed { index, endpoint ->
                Thread({
                    try {
                        if (!proceed() || race.abandoned(endpoint.url)) {
                            race.seal()
                            return@Thread
                        }
                        val response = try {
                            dial(DialRequest(endpoint, sessionId, race))
                        } catch (e: Exception) {
                            DialResponse(
                                DialStatus.RETRYABLE,
                                0,
                                e.message ?: e.javaClass.simpleName,
                            )
                        }
                        val detail = scrubSecrets(response.detail, secrets)
                        val record = AttemptRecord(
                            pass = pass,
                            index = index,
                            url = endpoint.url,
                            sessionId = sessionId,
                            outcome = response.status.name.lowercase(),
                            httpStatus = response.httpStatus,
                            detail = detail,
                        )
                        attempts.add(record)
                        notifyAttempt(record)
                        when (response.status) {
                            DialStatus.OPENED -> {
                                val win = ConnectResult(
                                    opened = true,
                                    cancelled = false,
                                    stopped = false,
                                    pass = pass,
                                    index = index,
                                    url = endpoint.url,
                                    sessionId = sessionId,
                                    attempts = copyAttempts(attempts),
                                    detail = detail,
                                )
                                if (opened.compareAndSet(null, win)) signal.countDown()
                            }
                            DialStatus.STOP, DialStatus.LATER -> preferTerminal(terminal, response.status)
                            DialStatus.RETRYABLE -> Unit
                        }
                    } finally {
                        if (finished.incrementAndGet() == endpoints.size) signal.countDown()
                    }
                }, "hub-dial-$index").also { it.isDaemon = true }
            }
            threads.forEach { it.start() }
            while (opened.get() == null && finished.get() < endpoints.size) {
                if (!proceed()) {
                    race.seal()
                    break
                }
                signal.await(50, TimeUnit.MILLISECONDS)
            }
            while (opened.get() == null && race.winner() != null && finished.get() < endpoints.size) {
                if (!proceed()) {
                    race.seal()
                    break
                }
                signal.await(20, TimeUnit.MILLISECONDS)
            }
            opened.get()?.let { return it.copy(attempts = copyAttempts(attempts)) }
            if (!proceed()) {
                race.seal()
                return cancelled(sessionId, copyAttempts(attempts), secrets)
            }
            // Seal only after every leg has left dial(). A leg still pending
            // may be the 101 the hub already admitted.
            if (finished.get() == endpoints.size) {
                race.seal()
            }
            if (terminal.get() == DialStatus.STOP && finished.get() == endpoints.size) {
                val last = lastAttempt(attempts)
                return ConnectResult(
                    opened = false,
                    cancelled = false,
                    stopped = true,
                    pass = pass,
                    index = last?.index ?: -1,
                    url = last?.url.orEmpty(),
                    sessionId = sessionId,
                    attempts = copyAttempts(attempts),
                    detail = last?.detail ?: lastDetail,
                )
            }
            lastDetail = lastAttempt(attempts)?.detail ?: lastDetail
            if (pass < maxPasses - 1) {
                if (!proceed()) return cancelled(sessionId, copyAttempts(attempts), secrets)
                val delay = backoff.delayAfterPass(pass)
                if (delay > 0) sleep(delay)
            }
        }
        return ConnectResult(
            opened = false,
            cancelled = false,
            stopped = false,
            pass = (maxPasses - 1).coerceAtLeast(0),
            index = endpoints.lastIndex,
            url = "",
            sessionId = sessionId,
            attempts = copyAttempts(attempts),
            detail = lastDetail,
        )
    }

    /** [Collections.synchronizedList] does not lock its iterator. Copy under the list lock. */
    private fun copyAttempts(attempts: MutableList<AttemptRecord>): List<AttemptRecord> =
        synchronized(attempts) { ArrayList(attempts) }

    private fun lastAttempt(attempts: MutableList<AttemptRecord>): AttemptRecord? =
        synchronized(attempts) { attempts.lastOrNull() }

    private fun notifyAttempt(record: AttemptRecord) {
        try {
            onAttempt(record)
        } catch (_: Exception) {
        }
    }

    private fun notifyRace(race: EndpointRace) {
        try {
            onRace(race)
        } catch (_: Exception) {
        }
    }

    private fun cancelled(
        sessionId: String,
        attempts: List<AttemptRecord>,
        secrets: List<String>,
    ): ConnectResult = ConnectResult(
        opened = false,
        cancelled = true,
        stopped = false,
        pass = attempts.lastOrNull()?.pass ?: 0,
        index = attempts.lastOrNull()?.index ?: -1,
        url = "",
        sessionId = sessionId,
        attempts = attempts.toList(),
        detail = scrubSecrets("cancelled", secrets),
    )
}

/**
 * One hub entrance. [deviceToken] is sent as `X-Gsm-Device-Token` by the dialer.
 * [toString] omits it so a log of the endpoint list cannot print the secret.
 */
data class HubEndpoint(
    val url: String,
    val deviceToken: String = "",
) {
    override fun toString(): String = "HubEndpoint(url=$url)"
}

data class DialRequest(
    val endpoint: HubEndpoint,
    val sessionId: String,
    val race: EndpointRace? = null,
)

/** [DialStatus.STOP] outranks [DialStatus.LATER]. A later 409 must not reopen the walk. */
internal fun preferTerminal(terminal: AtomicReference<DialStatus?>, next: DialStatus) {
    while (true) {
        val current = terminal.get()
        if (current == DialStatus.STOP || current == next) return
        if (terminal.compareAndSet(current, next)) return
    }
}

/**
 * One pass racing every entrance. [claim] is the only way an [onOpen] may
 * install the socket. [seal] abandons everyone who has not won. Hangup and
 * a pass that finished with no 101 seal the race. One leg's 409 does not,
 * and neither does the clock while a sibling is still inside dial().
 */
class EndpointRace {
    private val lock = Any()
    private var winnerUrl: String? = null
    private var sealed = false
    private val cancels = ConcurrentHashMap<String, () -> Unit>()

    fun winner(): String? = synchronized(lock) { winnerUrl }

    fun claim(url: String): Boolean {
        val drop = synchronized(lock) {
            if (sealed || winnerUrl != null) return false
            winnerUrl = url
            pendingCancels(url)
        }
        drop.forEach { invoke(it) }
        return true
    }

    fun seal() {
        val drop = synchronized(lock) {
            sealed = true
            pendingCancels(winnerUrl)
        }
        drop.forEach { invoke(it) }
    }

    fun abandoned(url: String): Boolean = synchronized(lock) { abandonedLocked(url) }

    fun track(url: String, cancel: () -> Unit) {
        val runNow = synchronized(lock) {
            cancels[url] = cancel
            abandonedLocked(url)
        }
        if (runNow) invoke(cancel)
    }

    private fun abandonedLocked(url: String): Boolean {
        if (winnerUrl == url) return false
        if (sealed) return true
        val winner = winnerUrl ?: return false
        return winner != url
    }

    private fun pendingCancels(keep: String?): List<() -> Unit> =
        cancels.filterKeys { it != keep }.values.toList()

    private fun invoke(cancel: () -> Unit) {
        try {
            cancel()
        } catch (_: Exception) {
        }
    }
}

enum class DialStatus {
    OPENED,
    RETRYABLE,
    LATER,
    STOP,
}

data class DialResponse(
    val status: DialStatus,
    val httpStatus: Int = 0,
    val detail: String = "",
)

data class AttemptRecord(
    val pass: Int,
    val index: Int,
    val url: String,
    val sessionId: String,
    val outcome: String,
    val httpStatus: Int,
    val detail: String,
)

data class ConnectResult(
    val opened: Boolean,
    val cancelled: Boolean,
    val stopped: Boolean,
    val pass: Int,
    val index: Int,
    val url: String,
    val sessionId: String,
    val attempts: List<AttemptRecord>,
    val detail: String,
) {
    fun summary(): String = buildString {
        append("opened=").append(opened)
        append(" stopped=").append(stopped)
        append(" cancelled=").append(cancelled)
        append(" pass=").append(pass)
        append(" index=").append(index)
        append(" url=").append(url)
        append(" session=").append(sessionId.ifBlank { "-" })
        append(" attempts=")
        append(attempts.joinToString(",") { attempt ->
            "${attempt.pass}:${attempt.index}:${attempt.url}:${attempt.outcome}:${attempt.httpStatus}"
        })
        if (detail.isNotBlank()) append(" detail=").append(detail.take(160))
    }
}

data class HubLink(
    val endpointUrl: String = "",
    val entrance: String = "",
    val sessionId: String = "",
)

data class Backoff(
    val initialMs: Long = 250,
    val multiplier: Double = 2.0,
    val capMs: Long = 4_000,
) {
    fun delayAfterPass(failedPassIndex: Int): Long {
        var delay = initialMs.toDouble()
        var remaining = failedPassIndex
        while (remaining > 0) {
            delay *= multiplier
            remaining -= 1
        }
        if (delay > capMs) delay = capMs.toDouble()
        if (delay < 0) return 0
        return delay.toLong()
    }
}

fun scrubSecrets(text: String, secrets: Collection<String>): String {
    var out = text
    for (secret in secrets) {
        if (secret.isNotBlank()) out = out.replace(secret, "[redacted]")
    }
    return out
}

/**
 * Map an HTTP status from one endpoint attempt.
 *
 * 409 with a session id is [DialStatus.LATER]: the hub may be about to hold
 * the relink grace window, and another socket in this same pass could supersede
 * the live call. 409 without a session id is [DialStatus.STOP]: this connect is
 * not a relink, and a second socket must not take over someone else's call.
 * Every other non-success is [DialStatus.RETRYABLE] so the walk tries the next
 * endpoint. The entrance name is not an input.
 */
fun classifyHubStatus(httpStatus: Int, sessionId: String): DialStatus = when {
    httpStatus == 101 || httpStatus in 200..299 -> DialStatus.OPENED
    httpStatus == 409 -> if (sessionId.isNotBlank()) DialStatus.LATER else DialStatus.STOP
    else -> DialStatus.RETRYABLE
}
