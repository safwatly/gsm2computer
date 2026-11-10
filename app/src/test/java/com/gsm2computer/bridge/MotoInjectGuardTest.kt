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
    fun `mic restore keeps the prior until the write returns`() {
        val gate = MicRestoreGate()
        gate.open(true)
        assertEquals(true, gate.pending())
        assertFalse(gate.write(canWrite = false) { error("no audio manager") })
        assertEquals(true, gate.pending())
        try {
            gate.write(canWrite = true) { throw IllegalStateException("setter failed") }
            error("setter throw should escape")
        } catch (_: IllegalStateException) {
        }
        assertEquals(true, gate.pending())
        val written = ArrayList<Boolean>()
        assertTrue(gate.write(canWrite = true) { written.add(it) })
        assertEquals(listOf(true), written)
        assertNull(gate.pending())
        assertTrue(gate.write(canWrite = false) { error("nothing pending") })
    }

    @Test
    fun `moto hangup edges still run after the live pointer is cleared`() {
        val call = Any()
        var active: Any? = call
        val reasons = ArrayList<String>()
        fun restore(reason: String) {
            reasons.add(reason)
        }
        active = HangupRestore.disconnected(active, call, ::restore)
        assertNull(active)
        HangupRestore.removed(active, call, ::restore)
        assertEquals(listOf("disconnected", "removed"), reasons)
    }

    @Test
    fun `a waiting call and a non-live disconnect do not restore`() {
        val live = Any()
        val waiting = Any()
        assertSame(live, HangupRestore.removed(live, waiting) { error("removed waiting") })
        assertSame(live, HangupRestore.disconnected(live, waiting) { error("disconnected other") })
    }

    @Test
    fun `new hangup restores are gated to the moto profile`() {
        val gsm = File("src/main/java/com/gsm2computer/bridge/gsm/GsmCallManager.kt").readText()
        val orchestrator = File("src/main/java/com/gsm2computer/bridge/bridge/CallOrchestrator.kt").readText()
        val service = File("src/main/java/com/gsm2computer/bridge/service/GatewayService.kt").readText()
        val session = File("src/main/java/com/gsm2computer/bridge/rtp/RtpSession.kt").readText()
        val disconnected = gsm.substringAfter("STATE_DISCONNECTED ->").substringBefore("if (!waiting)")
        val motoDisconnect = disconnected.substringAfter("if (isMotoProfile())").substringBefore("} else {")
        val otherDisconnect = disconnected.substringAfter("} else {")
        assertTrue(motoDisconnect.contains("restoreBridgedAudio"))
        assertFalse(otherDisconnect.contains("restore"))
        val removed = gsm.substringAfter("fun onCallRemoved").substringBefore("fun onCallStateChanged")
        val otherRemoved = removed.substringAfter("if (!isMotoProfile())").substringBefore("val live")
        assertTrue(otherRemoved.contains("isLiveGsmCall"))
        assertTrue(otherRemoved.contains("restoreAudio()"))
        assertFalse(otherRemoved.contains("restoreBridgedAudio"))
        assertTrue(removed.contains("HangupRestore.removed"))
        val bridged = gsm.substringAfter("fun restoreBridgedAudio").substringBefore("fun trackedCallStates")
        assertTrue(bridged.contains("if (!isMotoProfile()) return"))
        val mixerTry = bridged.substringAfter("try {").substringBefore("} catch")
        assertTrue(mixerTry.contains("batchMixerRestore()"))
        assertFalse(mixerTry.contains("restoreMotoMicrophone"))
        val mixerCatch = bridged.indexOf("Failed to restore mixer")
        val micCall = bridged.lastIndexOf("restoreMotoMicrophone(reason)")
        assertTrue(mixerCatch in 0 until micCall)
        val teardown = orchestrator.substringAfter("fun tearDown").substringBefore("val party")
        assertTrue(teardown.contains("if (GsmCallManager.isMotoProfile())"))
        assertTrue(teardown.contains("restoreBridgedAudio(\"teardown\")"))
        val destroy = service.substringAfter("fun onDestroy()").substringBefore("fun createNotificationChannel")
        assertTrue(destroy.contains("if (GsmCallManager.isMotoProfile())"))
        assertTrue(destroy.contains("restoreBridgedAudio(\"destroy\")"))
        assertFalse(session.contains("api_mute\", \"reverted\""))
        assertFalse(session.contains("inject_policy_effective"))
        assertFalse(gsm.contains("revertApiMute"))
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
    fun `api mute reads only the incall-music output thread`() {
        val active = """
            Output thread 0x1, name AudioOut_15:
              Standby: no
              Output devices: TELEPHONY_TX
              flags (INCALL_MUSIC)
        """.trimIndent()
        val split = """
            Output thread 0x1, name AudioOut_15:
              Standby: no
              Output devices: speaker
            Output thread 0x2, name AudioOut_D:
              Standby: yes
              Output devices: TELEPHONY_TX
              flags (INCALL_MUSIC)
        """.trimIndent()
        val musicOnly = """
            Output thread 0x1, name AudioOut_15:
              Standby: no
              flags (INCALL_MUSIC)
        """.trimIndent()
        assertNull(ApiMuteTiming.decide(false, true, active, 1_000L, 5_000L))
        assertNull(ApiMuteTiming.decide(true, false, active, 1_000L, 5_000L))
        assertEquals(ApiMuteTiming.AUDIOFLINGER, ApiMuteTiming.decide(true, true, active, 0L, 3_000L))
        assertEquals(ApiMuteTiming.AUDIOFLINGER, ApiMuteTiming.decide(true, true, musicOnly, 1_000L, 1_200L))
        assertFalse(ApiMuteTiming.audioFlingerIncallMusicActive(split))
        assertEquals(ApiMuteTiming.WRITE_DELAY, ApiMuteTiming.decide(true, true, split, 1_000L, 1_400L))
        assertNull(ApiMuteTiming.decide(true, true, split, 1_000L, 1_399L))
        assertFalse(ApiMuteTiming.audioFlingerIncallMusicActive("INCALL_MUSIC\nTELEPHONY_TX\nStandby: no\n"))
        assertEquals("api_mute fallback: no usecase signal", ApiMuteTiming.FALLBACK_LOG)
        val audioService = """
            Legacy stream types:
            - STREAM_VOICE_CALL:
               Muted: false
               Current: 1
               Max: 8
            - STREAM_MUSIC:
               Current: 9
            Mode: MODE_IN_CALL
            INCALL_MUSIC
            Standby: no
        """.trimIndent()
        assertFalse(ApiMuteTiming.audioFlingerIncallMusicActive(audioService))
        assertEquals(ApiMuteTiming.WRITE_DELAY, ApiMuteTiming.decide(true, true, audioService, 1_000L, 1_400L))
        val gsm = File("src/main/java/com/gsm2computer/bridge/gsm/GsmCallManager.kt").readText()
        assertTrue(gsm.contains("appLog(ApiMuteTiming.FALLBACK_LOG)"))
        assertTrue(ApiMuteTiming.DUMP_CMD.startsWith("dumpsys media.audio_flinger"))
        assertTrue(ApiMuteTiming.DUMP_CMD.contains("Output thread"))
        assertFalse(ApiMuteTiming.DUMP_CMD.contains("dumpsys audio"))
        assertFalse(ApiMuteTiming.DUMP_CMD.contains("grep"))
    }

    @Test
    fun `moto voice volume is restored on every hangup path and idle startup`() {
        val gsm = File("src/main/java/com/gsm2computer/bridge/gsm/GsmCallManager.kt").readText()
        val service = File("src/main/java/com/gsm2computer/bridge/service/GatewayService.kt").readText()
        val orchestrator = File("src/main/java/com/gsm2computer/bridge/bridge/CallOrchestrator.kt").readText()
        val disconnected = gsm.substringAfter("STATE_DISCONNECTED ->").substringBefore("if (!waiting)")
        assertTrue(disconnected.contains("restoreBridgedAudio"))
        val removed = gsm.substringAfter("fun onCallRemoved").substringBefore("fun onCallStateChanged")
        assertTrue(removed.contains("HangupRestore.removed"))
        assertTrue(removed.contains("restoreBridgedAudio"))
        val bridged = gsm.substringAfter("fun restoreBridgedAudio").substringBefore("fun restoreSavedVoiceVolume")
        assertTrue(bridged.contains("restoreSavedVoiceVolume"))
        assertTrue(bridged.contains("restoreMotoMicrophone(reason)"))
        assertFalse(bridged.contains("maxVc * 2 / 3"))
        val other = gsm.substringAfter("private fun restoreAudio").substringBefore("fun restoreBridgedAudio")
        assertTrue(other.contains("maxVc * 2 / 3"))
        val index = gsm.substringAfter("fun voiceCallIndex").substringBefore("fun raiseMotoVoiceCall")
        assertTrue(index.contains("if (!bridgeAudioOpen) return null"))
        assertTrue(index.contains("muteLocalEarpiece"))
        assertTrue(gsm.contains("return MotoVoiceVolume.activeIndex(maxVc)"))
        val teardown = orchestrator.substringAfter("fun tearDown").substringBefore("val party")
        assertTrue(teardown.contains("restoreBridgedAudio(\"teardown\")"))
        val destroy = service.substringAfter("fun onDestroy()").substringBefore("fun createNotificationChannel")
        assertTrue(destroy.contains("restoreBridgedAudio(\"destroy\")"))
        val startup = service.substringAfter("fun clearStaleMotoState").substringBefore("fun telephonyCallState")
        val gate = startup.indexOf("noCallAtAll")
        val mic = startup.indexOf("forceStartupMicUnmute()")
        val mixer = startup.indexOf("clearIdleMusicMixers()")
        val volume = startup.indexOf("restoreSavedVoiceVolume(am, \"startup\")")
        assertTrue(gate in 0 until mic)
        assertTrue(mic < mixer && mixer < volume)
        val micFn = service.substringAfter("fun forceStartupMicUnmute").substringBefore("fun clearIdleMusicMixers")
        assertTrue(micFn.contains("isMicrophoneMute = false"))
    }

    @Test
    fun `audio flow logs raw voice mute reads only for moto`() {
        val session = File("src/main/java/com/gsm2computer/bridge/rtp/RtpSession.kt").readText()
        val flow = session.substringAfter("val flowStats").substringBefore("listener?.onRtpStats(flowStats)")
        assertTrue(flow.contains("Moto g84"))
        assertTrue(flow.contains("readMotoHardwareMutes()"))
        val reader = session.substringAfter("fun readMotoHardwareMutes").substringBefore("fun readIncallDump")
        assertTrue(reader.contains("MotoMuteReadback.script()"))
        assertTrue(reader.contains("RootShell.execForOutput"))
        assertTrue(reader.contains("helperAccepted"))
        assertTrue(reader.contains("versionScript()"))
        assertFalse(reader.contains("TinymixTrace.run"))
        assertFalse(reader.contains("tinymix write"))
        assertFalse(reader.contains("Runtime.getRuntime"))
    }

    @Test
    fun `no capture policy skips voice call capture on moto only`() {
        val gsm = File("src/main/java/com/gsm2computer/bridge/gsm/GsmCallManager.kt").readText()
        val session = File("src/main/java/com/gsm2computer/bridge/rtp/RtpSession.kt").readText()
        val orchestrator = File("src/main/java/com/gsm2computer/bridge/bridge/CallOrchestrator.kt").readText()
        val gate = gsm.substringAfter("fun suppressesVoiceCapture").substringBefore("private fun applyMotoMicrophone")
        assertTrue(gate.contains("if (!isMotoProfile()) return false"))
        assertTrue(gate.contains("opensVoiceCapture"))
        val init = session.substringAfter("private fun initAudio").substringBefore("private fun captureInitAndLoop")
        val beforeOpen = init.substringBefore("if (captureSuppressed())")
        assertFalse(beforeOpen.contains("AudioRecord("))
        val opened = init.substringAfter("if (captureSuppressed())")
        assertTrue(opened.contains("AudioRecord("))
        val capt = session.substringAfter("private fun captureInitAndLoop").substringBefore("private fun failCapture")
        val early = capt.substringBefore("val configs")
        assertTrue(early.contains("captureSuppressed()"))
        assertTrue(early.contains("logCaptureSuppressed()"))
        assertTrue(early.contains("return"))
        assertFalse(early.contains("AudioRecord("))
        assertTrue(session.contains("AUDIO-FLOW: capture disabled by policy=") ||
            session.contains("captureDisabledLog()"))
        val flow = session.substringAfter("val flowStats").substringBefore("listener?.onRtpStats(flowStats)")
        assertTrue(flow.contains("flowCaptureNote()"))
        val pump = orchestrator.substringAfter("fun startAudioPump").substringBefore("activeSession?.stop()")
        val skipped = pump.substringAfter("suppressesVoiceCapture()").substringBefore("} else {")
        assertTrue(skipped.contains("captureDisabledLog()"))
        assertFalse(skipped.contains("MicIsolationGuard"))
        assertTrue(pump.substringAfter("} else {").contains("MicIsolationGuard"))
    }

    @Test
    fun `voice volume write and hangup restore share audioLock including forceReset`() {
        val gsm = File("src/main/java/com/gsm2computer/bridge/gsm/GsmCallManager.kt").readText()
        val orchestrator = File("src/main/java/com/gsm2computer/bridge/bridge/CallOrchestrator.kt").readText()
        val enforce = gsm.substringAfter("fun enforceVolumes").substringBefore("private fun restoreAudio")
        val locked = enforce.substringAfter("synchronized(audioLock)").substringBefore("Music stream")
        assertTrue(locked.contains("voiceCallIndex"))
        assertTrue(locked.contains("setStreamVolume"))
        val restoreVol = gsm.substringAfter("fun restoreSavedVoiceVolume").substringBefore("private fun voiceCallIndex")
        assertTrue(restoreVol.contains("synchronized(audioLock)"))
        assertTrue(restoreVol.contains("restoreWrite(bridgeAudioOpen"))
        val reset = orchestrator.substringAfter("private fun forceReset").substringBefore("private fun telecomLabel")
        assertTrue(reset.contains("if (GsmCallManager.isMotoProfile())"))
        assertTrue(reset.contains("restoreBridgedAudio(\"force-reset\")"))
        assertTrue(reset.indexOf("restoreBridgedAudio") < reset.indexOf("activeSession?.stop()"))
        assertFalse(reset.contains("restoreAudio()"))
    }

    @Test
    fun `capture windows log a speech pause and do not revert`() {
        val guard = ApiMuteCaptureGuard()
        val lines = ArrayList<String>()
        var now = 0L
        repeat(101) {
            guard.onFrame(1_000, now, muted = false)?.let { lines.add(it) }
            now += 20
        }
        assertEquals("api_mute capture pre=1000 post=", lines.last())
        repeat(101) {
            guard.onFrame(0, now, muted = true)?.let { lines.add(it) }
            now += 20
        }
        assertTrue(lines.any { it == "api_mute capture pre=1000 post=0" })
        repeat(100) {
            guard.onFrame(900, now, muted = true)?.let { lines.add(it) }
            now += 20
        }
        assertTrue(lines.any { it.startsWith("api_mute capture pre=1000 post=0,") })
        assertTrue(lines.none { it.contains("revert") })
    }

    private fun startup(states: List<Int>, telephony: Int, mode: Int): Boolean =
        MotoIdleMixers.shouldForceMicUnmute("Moto g84 (holi)", states, telephony, mode) &&
            MotoIdleMixers.shouldClear("Moto g84 (holi)", states, telephony, mode)
}
