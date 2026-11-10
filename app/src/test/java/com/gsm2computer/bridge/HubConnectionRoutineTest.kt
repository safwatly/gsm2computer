package com.gsm2computer.bridge

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference

class HubConnectionRoutineTest {

    private val secret = "device-token-test-value"
    private val public = HubEndpoint("wss://gsm.ta3leam.com", secret)
    private val tailscale = HubEndpoint("ws://100.113.222.47:8787", secret)
    private val lan = HubEndpoint("ws://10.0.0.8:8787", secret)

    @Test
    fun walksEndpointsInOrderAndStopsOnTheFirstOpen() {
        val seen = mutableListOf<String>()
        val routine = routine(listOf(public, tailscale, lan)) { request ->
            seen += request.endpoint.url
            if (request.endpoint.url == tailscale.url) {
                DialResponse(DialStatus.OPENED, 101, "")
            } else {
                DialResponse(DialStatus.RETRYABLE, 0, "down")
            }
        }
        val result = routine.connect()
        assertEquals(listOf(public.url, tailscale.url), seen)
        assertTrue(result.opened)
        assertEquals(0, result.pass)
        assertEquals(1, result.index)
        assertEquals(tailscale.url, result.url)
        assertEquals("opened", result.attempts.last().outcome)
    }

    @Test
    fun failoverDoesNotSleepInsideAPass() {
        val sleeps = mutableListOf<Long>()
        val routine = HubConnectionRoutine(
            listOf(public, tailscale),
            backoff = Backoff(initialMs = 250, multiplier = 2.0, capMs = 4_000),
            sleep = { sleeps += it },
        ) { request ->
            if (request.endpoint.url == tailscale.url) DialResponse(DialStatus.OPENED, 101, "")
            else DialResponse(DialStatus.RETRYABLE, 0, "refused")
        }
        val result = routine.connect(maxPasses = 3)
        assertTrue(result.opened)
        assertTrue(sleeps.isEmpty())
        assertEquals(2, result.attempts.size)
    }

    @Test
    fun backoffSleepsBetweenFailedPassesAndCaps() {
        val sleeps = mutableListOf<Long>()
        var passSeen = -1
        val routine = HubConnectionRoutine(
            listOf(public, tailscale),
            backoff = Backoff(initialMs = 100, multiplier = 10.0, capMs = 250),
            sleep = { sleeps += it },
        ) { request ->
            passSeen = if (request.endpoint.url == public.url) passSeen else passSeen
            DialResponse(DialStatus.RETRYABLE, 0, "down")
        }
        val result = routine.connect(maxPasses = 3)
        assertFalse(result.opened)
        assertEquals(listOf(100L, 250L), sleeps)
        assertEquals(6, result.attempts.size)
    }

    @Test
    fun laterEndsThePassAndTheNextPassStartsAtTheFirstEndpoint() {
        val seen = mutableListOf<String>()
        val sleeps = mutableListOf<Long>()
        var calls = 0
        val routine = HubConnectionRoutine(
            listOf(public, tailscale),
            backoff = Backoff(initialMs = 40, multiplier = 2.0, capMs = 40),
            sleep = { sleeps += it },
        ) { request ->
            calls += 1
            seen += request.endpoint.url
            if (calls == 1) DialResponse(DialStatus.LATER, 409, "busy")
            else DialResponse(DialStatus.OPENED, 101, "")
        }
        val result = routine.connect(sessionId = "hub-live", maxPasses = 3)
        assertEquals(listOf(public.url, public.url), seen)
        assertEquals(listOf(40L), sleeps)
        assertTrue(result.opened)
        assertEquals(1, result.pass)
        assertTrue(result.attempts.all { it.sessionId == "hub-live" })
    }

    @Test
    fun stopDoesNotDialLaterEndpointsOrSleep() {
        val seen = mutableListOf<String>()
        val sleeps = mutableListOf<Long>()
        val routine = HubConnectionRoutine(
            listOf(public, tailscale),
            sleep = { sleeps += it },
        ) { request ->
            seen += request.endpoint.url
            DialResponse(DialStatus.STOP, 409, "call in progress")
        }
        val result = routine.connect(maxPasses = 4)
        assertEquals(listOf(public.url), seen)
        assertTrue(sleeps.isEmpty())
        assertTrue(result.stopped)
        assertFalse(result.opened)
    }

    @Test
    fun reconnectIsTheSameWalkWithTheSessionId() {
        val seen = mutableListOf<Pair<String, String>>()
        val routine = routine(listOf(public, tailscale)) { request ->
            seen += request.endpoint.url to request.sessionId
            DialResponse(DialStatus.RETRYABLE, 0, "down ${request.endpoint.deviceToken}")
        }
        routine.connect(sessionId = "", maxPasses = 1)
        routine.connect(sessionId = "hub-relink", maxPasses = 1)
        assertEquals(
            listOf(
                public.url to "",
                tailscale.url to "",
                public.url to "hub-relink",
                tailscale.url to "hub-relink",
            ),
            seen,
        )
    }

    @Test
    fun summaryAndEndpointStringOmitTheDeviceToken() {
        val routine = routine(listOf(public)) { request ->
            DialResponse(DialStatus.RETRYABLE, 401, "rejected ${request.endpoint.deviceToken}")
        }
        val result = routine.connect()
        assertFalse(result.summary().contains(secret))
        assertFalse(result.detail.contains(secret))
        assertFalse(result.attempts.single().detail.contains(secret))
        assertFalse(public.toString().contains(secret))
        assertFalse(DialRequest(public, "hub-1").toString().contains(secret))
        assertTrue(result.detail.contains("[redacted]"))
    }

    @Test
    fun emptyListDoesNotSleepOrDial() {
        var dialed = false
        var slept = false
        val routine = HubConnectionRoutine(
            emptyList(),
            sleep = { slept = true },
        ) {
            dialed = true
            DialResponse(DialStatus.OPENED)
        }
        val result = routine.connect(maxPasses = 3)
        assertFalse(dialed)
        assertFalse(slept)
        assertTrue(result.stopped)
        assertFalse(result.opened)
    }

    @Test
    fun proceedFalseSkipsTheRestOfTheList() {
        val seen = mutableListOf<String>()
        var checks = 0
        val routine = routine(listOf(public, tailscale)) { request ->
            seen += request.endpoint.url
            DialResponse(DialStatus.RETRYABLE, 0, "down")
        }
        val result = routine.connect(maxPasses = 3) {
            checks += 1
            checks < 3
        }
        assertEquals(listOf(public.url), seen)
        assertTrue(result.cancelled)
        assertFalse(result.opened)
    }

    @Test
    fun racingTakesTheFirstOpenWithoutWaitingForTheSlowEndpoint() {
        val entered = CountDownLatch(2)
        val fastGo = CountDownLatch(1)
        val slowRelease = CountDownLatch(1)
        val routine = routine(listOf(public, tailscale)) { request ->
            entered.countDown()
            if (request.endpoint.url == tailscale.url) {
                fastGo.await(2, TimeUnit.SECONDS)
                DialResponse(DialStatus.OPENED, 101, "")
            } else {
                slowRelease.await(2, TimeUnit.SECONDS)
                DialResponse(DialStatus.RETRYABLE, 0, "slow")
            }
        }
        val resultRef = AtomicReference<ConnectResult>()
        val done = CountDownLatch(1)
        Thread {
            resultRef.set(routine.connectRacing())
            done.countDown()
        }.start()
        assertTrue(entered.await(2, TimeUnit.SECONDS))
        fastGo.countDown()
        assertTrue(done.await(2, TimeUnit.SECONDS))
        val result = resultRef.get()
        assertTrue(result.opened)
        assertEquals(tailscale.url, result.url)
        assertEquals(1L, slowRelease.count)
        slowRelease.countDown()
    }

    @Test
    fun racing409ArrivesBefore101AndTheCallProceedsOnTheWinner() {
        val loserDone = CountDownLatch(1)
        val routine = routine(listOf(public, tailscale)) { request ->
            if (request.endpoint.url == tailscale.url) {
                loserDone.countDown()
                DialResponse(DialStatus.STOP, 409, "call in progress")
            } else {
                assertTrue(loserDone.await(2, TimeUnit.SECONDS))
                val race = checkNotNull(request.race)
                assertFalse(race.abandoned(request.endpoint.url))
                DialResponse(DialStatus.OPENED, 101, "")
            }
        }
        val result = routine.connectRacing(maxPasses = 3)
        assertTrue(result.opened)
        assertFalse(result.stopped)
        assertFalse(result.cancelled)
        assertEquals(public.url, result.url)
        assertEquals(0, result.pass)
        assertEquals(
            "opened",
            result.attempts.first { it.url == public.url }.outcome,
        )
        assertEquals(101, result.attempts.first { it.url == public.url }.httpStatus)
    }

    @Test
    fun racingLaterDoesNotCancelTheLegStillWaitingFor101() {
        val loserDone = CountDownLatch(1)
        val routine = routine(listOf(public, tailscale)) { request ->
            if (request.endpoint.url == tailscale.url) {
                loserDone.countDown()
                DialResponse(DialStatus.LATER, 409, "relink grace")
            } else {
                assertTrue(loserDone.await(2, TimeUnit.SECONDS))
                assertFalse(checkNotNull(request.race).abandoned(request.endpoint.url))
                DialResponse(DialStatus.OPENED, 101, "")
            }
        }
        val result = routine.connectRacing(maxPasses = 3)
        assertTrue(result.opened)
        assertEquals(public.url, result.url)
        assertEquals(0, result.pass)
    }

    @Test
    fun racingStopWhenEveryLegFinishedEndsTheWalk() {
        var dials = 0
        val routine = routine(listOf(public, tailscale)) {
            dials += 1
            DialResponse(DialStatus.STOP, 409, "call in progress")
        }
        val result = routine.connectRacing(maxPasses = 3)
        assertTrue(result.stopped)
        assertFalse(result.opened)
        assertEquals(2, dials)
    }

    @Test
    fun racingProceedFalseCancelsBeforeAnOpen() {
        val release = CountDownLatch(1)
        val routine = routine(listOf(public, tailscale)) {
            release.await(2, TimeUnit.SECONDS)
            DialResponse(DialStatus.OPENED, 101, "")
        }
        val resultRef = AtomicReference<ConnectResult>()
        val done = CountDownLatch(1)
        Thread {
            resultRef.set(routine.connectRacing { false })
            done.countDown()
        }.start()
        assertTrue(done.await(2, TimeUnit.SECONDS))
        assertTrue(resultRef.get().cancelled)
        assertFalse(resultRef.get().opened)
        release.countDown()
    }

    @Test
    fun classify409KeepsALiveCallFromBeingDialedTwice() {
        assertEquals(DialStatus.STOP, classifyHubStatus(409, ""))
        assertEquals(DialStatus.LATER, classifyHubStatus(409, "hub-live"))
        assertEquals(DialStatus.RETRYABLE, classifyHubStatus(401, "hub-live"))
        assertEquals(DialStatus.RETRYABLE, classifyHubStatus(0, "hub-live"))
        assertEquals(DialStatus.OPENED, classifyHubStatus(101, ""))
        assertEquals(DialStatus.OPENED, classifyHubStatus(201, "hub-live"))
    }

    private fun routine(
        endpoints: List<HubEndpoint>,
        dial: (DialRequest) -> DialResponse,
    ): HubConnectionRoutine = HubConnectionRoutine(
        endpoints = endpoints,
        backoff = Backoff(initialMs = 10, multiplier = 2.0, capMs = 10),
        sleep = {},
        dial = dial,
    )
}
