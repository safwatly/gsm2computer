package com.gsm2computer.bridge

import android.media.AudioManager
import android.telecom.Call
import android.telephony.TelephonyManager
import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test

class MotoInjectGuardTest {

    @Test
    fun `disconnected restores the prior mic and later hangup edges do not write again`() {
        val gate = MicRestoreGate()
        gate.open(true)
        val call = Any()
        var active: Any? = call
        val writes = ArrayList<Boolean?>()
        val reasons = ArrayList<String>()
        fun restore(reason: String) {
            reasons.add(reason)
            writes.add(gate.restore())
        }
        active = HangupRestore.disconnected(active, call, ::restore)
        assertNull(active)
        HangupRestore.removed(active, call, ::restore)
        restore("teardown")
        restore("destroy")
        assertEquals(listOf("disconnected", "removed", "teardown", "destroy"), reasons)
        assertEquals(listOf(true, null, null, null), writes)
    }

    @Test
    fun `a waiting call and a non-live disconnect do not restore`() {
        val live = Any()
        val waiting = Any()
        assertSame(live, HangupRestore.removed(live, waiting) { error("removed waiting") })
        assertSame(live, HangupRestore.disconnected(live, waiting) { error("disconnected other") })
    }

    @Test
    fun `hangup source calls restore on disconnect removal teardown and destroy`() {
        val gsm = File("src/main/java/com/gsm2computer/bridge/gsm/GsmCallManager.kt").readText()
        val orchestrator = File("src/main/java/com/gsm2computer/bridge/bridge/CallOrchestrator.kt").readText()
        val service = File("src/main/java/com/gsm2computer/bridge/service/GatewayService.kt").readText()
        val disconnected = gsm.substringAfter("STATE_DISCONNECTED ->").substringBefore("if (!waiting)")
        assertTrue(disconnected.contains("restoreBridgedAudio"))
        val removed = gsm.substringAfter("fun onCallRemoved").substringBefore("fun onCallStateChanged")
        assertTrue(removed.contains("restoreBridgedAudio"))
        assertTrue(orchestrator.contains("restoreBridgedAudio(\"teardown\")"))
        val destroy = service.substringAfter("fun onDestroy()").substringBefore("fun createNotificationChannel")
        assertTrue(destroy.contains("restoreBridgedAudio(\"destroy\")"))
    }

    @Test
    fun `startup unmute and mixer clear run only when no call exists`() {
        assertTrue(startup(emptyList(), TelephonyManager.CALL_STATE_IDLE, AudioManager.MODE_NORMAL))
        assertFalse(
            startup(listOf(Call.STATE_RINGING), TelephonyManager.CALL_STATE_RINGING, AudioManager.MODE_RINGTONE),
        )
        assertFalse(
            startup(listOf(Call.STATE_DIALING), TelephonyManager.CALL_STATE_OFFHOOK, AudioManager.MODE_IN_CALL),
        )
        assertFalse(
            startup(listOf(Call.STATE_HOLDING), TelephonyManager.CALL_STATE_OFFHOOK, AudioManager.MODE_IN_CALL),
        )
        assertFalse(
            startup(listOf(Call.STATE_ACTIVE), TelephonyManager.CALL_STATE_OFFHOOK, AudioManager.MODE_IN_CALL),
        )
        assertFalse(startup(emptyList(), TelephonyManager.CALL_STATE_OFFHOOK, AudioManager.MODE_NORMAL))
        assertFalse(startup(emptyList(), TelephonyManager.CALL_STATE_IDLE, AudioManager.MODE_IN_CALL))
        assertFalse(startup(emptyList(), -1, AudioManager.MODE_NORMAL))
        assertTrue(
            startup(
                listOf(Call.STATE_DISCONNECTED),
                TelephonyManager.CALL_STATE_IDLE,
                AudioManager.MODE_NORMAL,
            ),
        )
        assertFalse(
            MotoIdleMixers.shouldForceMicUnmute(
                "Pixel 7 (Tensor G1)",
                emptyList(),
                TelephonyManager.CALL_STATE_IDLE,
                AudioManager.MODE_NORMAL,
            ),
        )
    }

    @Test
    fun `api mute waits for a playing telephony track and a capture window`() {
        val active = "Output\nStandby: no\nINCALL_MUSIC\nTELEPHONY_TX\n"
        val standby = "Output\nStandby: yes\nINCALL_MUSIC\nTELEPHONY_TX\n"
        assertNull(
            ApiMuteTiming.decide(false, true, active, 1_000L, 5_000L, observedForMs = 2_000L),
        )
        assertNull(
            ApiMuteTiming.decide(true, false, active, 1_000L, 5_000L, observedForMs = 2_000L),
        )
        assertNull(
            ApiMuteTiming.decide(true, true, active, 1_000L, 1_200L, observedForMs = 200L),
        )
        assertEquals(
            ApiMuteTiming.AUDIOFLINGER,
            ApiMuteTiming.decide(true, true, active, 0L, 3_000L, observedForMs = 2_000L),
        )
        assertEquals(
            ApiMuteTiming.WRITE_DELAY,
            ApiMuteTiming.decide(true, true, standby, 1_000L, 3_000L, observedForMs = 2_000L),
        )
        assertNull(
            ApiMuteTiming.decide(true, true, standby, 1_000L, 1_200L, observedForMs = 2_000L),
        )
        assertFalse(ApiMuteTiming.audioFlingerIncallMusicActive("INCALL_MUSIC only"))
    }

    @Test
    fun `capture guard reverts when rms collapses and keeps a normal level`() {
        assertTrue(ApiMuteCaptureGuard.collapsed(List(50) { 1_000 }, List(50) { 0 }))
        assertTrue(ApiMuteCaptureGuard.collapsed(List(50) { 1_000 }, List(50) { 50 }))
        assertFalse(ApiMuteCaptureGuard.collapsed(List(50) { 1_000 }, List(50) { 100 }))
        assertFalse(ApiMuteCaptureGuard.collapsed(List(50) { 1_000 }, List(50) { 200 }))
        assertFalse(ApiMuteCaptureGuard.collapsed(List(50) { 0 }, List(50) { 0 }))
        assertEquals("api_mute reverted: capture collapsed", ApiMuteCaptureGuard.REVERT_LOG)

        val guard = ApiMuteCaptureGuard()
        var now = 0L
        repeat(100) {
            val step = guard.onFrame(1_000, now, muted = false)
            assertFalse(step.revert)
            now += 20
        }
        var reverted = false
        repeat(101) {
            val step = guard.onFrame(0, now, muted = true)
            if (step.revert) reverted = true
            now += 20
        }
        assertTrue(reverted)
        val kept = ApiMuteCaptureGuard()
        now = 0L
        repeat(100) {
            kept.onFrame(1_000, now, muted = false)
            now += 20
        }
        var keptRevert = false
        repeat(101) {
            if (kept.onFrame(800, now, muted = true).revert) keptRevert = true
            now += 20
        }
        assertFalse(keptRevert)
    }

    private fun startup(states: List<Int>, telephony: Int, mode: Int): Boolean =
        MotoIdleMixers.shouldForceMicUnmute("Moto g84 (holi)", states, telephony, mode) &&
            MotoIdleMixers.shouldClear("Moto g84 (holi)", states, telephony, mode)
}
