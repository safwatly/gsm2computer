package com.gsm2computer.bridge.gsm

import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
import android.media.AudioManager
import android.net.Uri
import android.os.Build
import android.telecom.Call
import android.telecom.CallAudioState
import android.telecom.InCallService
import android.util.Log
import com.gsm2computer.bridge.ApiMuteCaptureGuard
import com.gsm2computer.bridge.ApiMuteTiming
import com.gsm2computer.bridge.BridgeConfig
import com.gsm2computer.bridge.DeviceProfile
import com.gsm2computer.bridge.GatewayApp
import com.gsm2computer.bridge.HangupRestore
import com.gsm2computer.bridge.InjectPolicy
import com.gsm2computer.bridge.MicApiAction
import com.gsm2computer.bridge.MicRestoreGate
import com.gsm2computer.bridge.MotoIdleMixers
import com.gsm2computer.bridge.MotoVoiceVolume
import com.gsm2computer.bridge.PolicyLogLevel
import com.gsm2computer.bridge.RootShell
import com.gsm2computer.bridge.TinymixTrace
import com.gsm2computer.bridge.VoiceTxMuteJob

/**
 * GSM call manager: answers/makes/hangs up GSM calls, tracks state.
 *
 * Calls are controlled through the InCallService (GsmCallService).
 * Audio routing uses device-specific mixer controls via [DeviceProfile].
 *
 * SIP→GSM: AudioTrack (USAGE_MEDIA / deep-buffer) → incall_music →
 * HAL injects STREAM_MUSIC digitally into voice TX (uplink).
 *
 * GSM→SIP: VOICE_CALL capture provides digital uplink+downlink audio.
 *
 * ALL tinymix commands are batched into a single su call to minimise
 * JVM spawning on low-end devices.
 */
object GsmCallManager {

    private const val TAG = "GsmCallManager"

    /** Active device profile — initialized on first use. */
    val profile: DeviceProfile by lazy { DeviceProfile.detect() }

    // The bridged GSM call only. A waiting/rejected leg must not replace this.
    @Volatile var activeCall: Call? = null; private set
    @Volatile var activeCallState: Int = Call.STATE_NEW; private set
    @Volatile var inCallService: InCallService? = null; private set

    @Volatile var listener: Listener? = null

    /** Optional callback for routing important audio diagnostics to the
     *  app log viewer (Settings tab).  Set by GatewayService. */
    @Volatile var logCallback: ((String) -> Unit)? = null

    /** Set when [batchMixerSetup] finishes; RtpSession waits past this before capture. */
    @Volatile var mixerSetupCompletedAtMs: Long = 0

    /** Moto inject policy latched at call start. Other profiles ignore it. */
    @Volatile var injectPolicy: InjectPolicy = InjectPolicy.DEFAULT
        private set

    @Volatile private var injectPolicyLatched = false
    private var voiceTxDecisionLogged = false
    private val audioLock = Any()
    private val micGate = MicRestoreGate()
    @Volatile private var bridgeAudioOpen = false
    @Volatile private var apiMuteApplied = false
    @Volatile private var captureGuard = ApiMuteCaptureGuard()

    /**
     * Relay mode: silence the gateway's own earpiece so the caller's voice isn't
     * audible on the gateway phone. In WS-direct mode the gateway needs no local
     * call audio (caller is captured via VOICE_DOWNLINK, the agent is injected
     * via STREAM_MUSIC/incall_music), so STREAM_VOICE_CALL is forced to 0. Set by
     * the orchestrator before answering; reset on call end.
     */
    @Volatile var muteLocalEarpiece = false

    /** Log to both Android logcat AND the app log viewer. */
    private fun appLog(msg: String) {
        Log.i(TAG, msg)
        logCallback?.invoke(msg)
    }

    interface Listener {
        /** Incoming GSM call ringing — caller number provided */
        fun onIncomingGsmCall(call: Call, number: String)
        /** GSM call connected (active) */
        fun onGsmCallActive(call: Call)
        /** GSM call state changed */
        fun onGsmCallStateChanged(call: Call, state: Int)
        /** GSM call ended */
        fun onGsmCallEnded(call: Call)
    }

    // ── InCallService callbacks ─────────────────────────

    fun onCallAdded(call: Call, service: InCallService) {
        inCallService = service
        val waiting = isWaitingGsmCall(activeCall, call)
        if (!waiting) {
            activeCall = call
            activeCallState = call.state
        }

        val number = call.details?.handle?.schemeSpecificPart ?: "unknown"

        when (call.state) {
            Call.STATE_RINGING -> {
                Log.i(TAG, "Incoming GSM call from $number waiting=$waiting")
                // Silence the ringtone immediately — this is a gateway device,
                // not a user-facing phone.  The call will be auto-answered
                // once the SIP leg is established.
                try {
                    val am = service.getSystemService(Context.AUDIO_SERVICE) as AudioManager
                    am.setStreamVolume(AudioManager.STREAM_RING, 0, 0)
                } catch (e: Exception) {
                    Log.w(TAG, "Ringer silence failed: ${e.message}")
                }
                if (listener != null) {
                    listener?.onIncomingGsmCall(call, number)
                } else if (!waiting) {
                    notifyStandaloneDialer(service, number)
                }
            }
            Call.STATE_DIALING, Call.STATE_CONNECTING -> {
                if (waiting) {
                    Log.w(TAG, "Ignoring outgoing GSM $number; live call already tracked")
                    return
                }
                Log.i(TAG, "Outgoing GSM call to $number")
            }
            Call.STATE_ACTIVE -> {
                if (waiting) {
                    Log.w(TAG, "Second GSM call $number went ACTIVE; not stealing mixer")
                    listener?.onIncomingGsmCall(call, number)
                    return
                }
                Log.i(TAG, "GSM call active: $number")
                configureAudioBridge()
                requestBluetoothRoute()
                listener?.onGsmCallActive(call)
            }
        }
    }

    fun onCallRemoved(call: Call) {
        if (!isMotoProfile()) {
            if (!isLiveGsmCall(activeCall, call)) {
                Log.i(TAG, "Non-live GSM call removed; live call unchanged")
                return
            }
            Log.i(TAG, "GSM call removed")
            activeCall = null
            activeCallState = Call.STATE_DISCONNECTED
            restoreAudio()
            listener?.onGsmCallEnded(call)
            return
        }
        val live = activeCall
        if (live != null && live !== call) {
            Log.i(TAG, "Non-live GSM call removed; live call unchanged")
            return
        }
        Log.i(TAG, "GSM call removed")
        activeCall = HangupRestore.removed(live, call) { restoreBridgedAudio(it) }
        activeCallState = Call.STATE_DISCONNECTED
        if (live === call) listener?.onGsmCallEnded(call)
    }

    fun onCallStateChanged(call: Call, state: Int) {
        val waiting = isWaitingGsmCall(activeCall, call)
        if (!waiting) {
            activeCallState = state
        }

        when (state) {
            Call.STATE_RINGING -> {
                // Handle calls that arrive as STATE_NEW in onCallAdded and
                // transition to RINGING via the callback.  Without this,
                // the orchestrator never learns about the incoming call.
                val number = call.details?.handle?.schemeSpecificPart ?: "unknown"
                Log.i(TAG, "GSM call ringing: $number (via state change) waiting=$waiting")
                if (listener != null) {
                    listener?.onIncomingGsmCall(call, number)
                } else if (!waiting) {
                    inCallService?.let { notifyStandaloneDialer(it, number) }
                }
            }
            Call.STATE_ACTIVE -> {
                if (waiting) {
                    Log.w(TAG, "Ignoring STATE_ACTIVE for non-live GSM call")
                    return
                }
                Log.i(TAG, "GSM call active")
                configureAudioBridge()
                requestBluetoothRoute()
                listener?.onGsmCallActive(call)
            }
            Call.STATE_DISCONNECTED -> {
                if (!isLiveGsmCall(activeCall, call)) {
                    Log.i(TAG, "Non-live GSM call disconnected; live call unchanged")
                    return
                }
                Log.i(TAG, "GSM call disconnected")
                if (isMotoProfile()) {
                    activeCall = HangupRestore.disconnected(activeCall, call) { restoreBridgedAudio(it) }
                    listener?.onGsmCallEnded(call)
                } else {
                    listener?.onGsmCallEnded(call)
                    activeCall = null
                }
            }
        }
        if (!waiting) {
            listener?.onGsmCallStateChanged(call, state)
        }
    }

    // ── Bluetooth call-audio routing (build 344) ────────

    /** Per-call BT diagnostics, merged into CaptureTap meta on close. */
    val btMeta = java.util.concurrent.ConcurrentHashMap<String, Any>()
    /** Set by CallOrchestrator to forward events into the call trace. */
    @Volatile var btTrace: ((String, Map<String, Any?>) -> Unit)? = null
    @Volatile private var btRouteToken = 0

    private fun btPut(k: String, v: Any?) {
        btMeta[k] = v ?: "null"
        try { btTrace?.invoke(k, mapOf("value" to v)) } catch (_: Exception) {}
    }

    private fun btEnabled(): Boolean = try {
        java.io.File("/data/local/tmp/bt_route_enabled").readText().trim() == "1"
    } catch (_: Exception) {
        try { RootShell.execForOutput("cat /data/local/tmp/bt_route_enabled", 2000).trim() == "1" } catch (_: Exception) { false }
    }

    fun noteAudioState(state: android.telecom.CallAudioState?) {
        if (state == null) return
        val s = "route=${android.telecom.CallAudioState.audioRouteToString(state.route)} mask=${state.supportedRouteMask} muted=${state.isMuted}" +
            (if (Build.VERSION.SDK_INT >= 28) " active=${state.activeBluetoothDevice?.address} supported=${state.supportedBluetoothDevices.size}" else "")
        btPut("bt_audio_state", s)
    }

    private fun requestBluetoothRoute() {
        val svc = inCallService ?: return
        val token = ++btRouteToken
        btMeta.clear()
        Thread({
            try {
                if (!btEnabled()) { btPut("bt_route_request", "disabled"); return@Thread }
                btPut("bt_route_request", "start")
                var result = "unavailable"
                for (attempt in 1..10) {
                    if (token != btRouteToken || activeCall == null) { result = "aborted"; break }
                    @Suppress("DEPRECATION")
                    val st = svc.callAudioState
                    noteAudioState(st)
                    val mask = st?.supportedRouteMask ?: 0
                    btPut("bt_supported_mask", mask)
                    if (st != null && st.route == android.telecom.CallAudioState.ROUTE_BLUETOOTH) {
                        result = "ok attempt=$attempt"
                        if (Build.VERSION.SDK_INT >= 28) btPut("bt_device_name", safeName(st.activeBluetoothDevice))
                        break
                    }
                    if (mask and android.telecom.CallAudioState.ROUTE_BLUETOOTH != 0) {
                        val dev = if (Build.VERSION.SDK_INT >= 28) st?.supportedBluetoothDevices?.firstOrNull() else null
                        if (Build.VERSION.SDK_INT >= 28 && dev != null) {
                            btPut("bt_device_name", safeName(dev))
                            try { svc.requestBluetoothAudio(dev) } catch (e: Exception) { btPut("bt_route_err", "requestBluetoothAudio: ${e.message}") }
                        }
                        @Suppress("DEPRECATION")
                        try { svc.setAudioRoute(android.telecom.CallAudioState.ROUTE_BLUETOOTH) } catch (e: Exception) { btPut("bt_route_err", "setAudioRoute: ${e.message}") }
                        result = "requested attempt=$attempt"
                    }
                    Thread.sleep(500)
                }
                @Suppress("DEPRECATION")
                val fin = svc.callAudioState
                noteAudioState(fin)
                if (fin?.route == android.telecom.CallAudioState.ROUTE_BLUETOOTH && !result.startsWith("ok")) result = "ok (after $result)"
                btPut("bt_route_result", result)
                appLog("BT route: $result")
            } catch (e: Exception) {
                btPut("bt_route_result", "error: ${e.message}")
            }
        }, "BtRoute").start()
    }

    @android.annotation.SuppressLint("MissingPermission")
    private fun safeName(d: android.bluetooth.BluetoothDevice?): String =
        if (d == null) "null" else try { "${d.name} (${d.address})" } catch (e: Exception) { d.address ?: "?" }

    // ── Call control ────────────────────────────────────

    /** Answer a ringing GSM call */
    fun answerCall(call: Call? = activeCall) {
        call?.let {
            Log.i(TAG, "Answering GSM call")
            it.answer(it.details.videoState)
        }
    }

    private fun notifyStandaloneDialer(context: Context, number: String) {
        // Gateway off — launch MainActivity as a standalone dialer
        val intent = Intent(context, Class.forName("com.gsm2computer.bridge.MainActivity")).apply {
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP)
            putExtra("incoming_call", true)
            putExtra("number", number)
        }
        context.startActivity(intent)
    }

    /** Drop a waiting GSM call without touching the live bridged call. */
    fun rejectCall(call: Call? = activeCall) {
        val target = call ?: return
        Log.i(TAG, "Rejecting GSM call state=${target.state}")
        if (target.state == Call.STATE_RINGING || target.state == Call.STATE_NEW) {
            target.reject(false, "")
        } else {
            target.disconnect()
        }
    }

    /** Hang up active GSM call */
    fun hangupCall(call: Call? = activeCall) {
        call?.let {
            Log.i(TAG, "Hanging up GSM call")
            it.disconnect()
        }
    }

    /** Place outgoing GSM call via the SIM */
    fun makeCall(context: Context, destination: String) {
        Log.i(TAG, "Making GSM call to $destination")
        val intent = Intent(Intent.ACTION_CALL, Uri.parse("tel:$destination"))
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        context.startActivity(intent)
    }

    // ── Standalone Dialer Controls ──────────────────────

    fun setSpeakerMode(enabled: Boolean) {
        if (enabled) {
            inCallService?.setAudioRoute(CallAudioState.ROUTE_SPEAKER)
            Log.i(TAG, "Standalone: Speaker route selected")
        } else {
            val supported = inCallService?.callAudioState?.supportedRouteMask ?: 0
            val newRoute = when {
                (supported and CallAudioState.ROUTE_WIRED_HEADSET) != 0 -> CallAudioState.ROUTE_WIRED_HEADSET
                (supported and CallAudioState.ROUTE_BLUETOOTH) != 0 -> CallAudioState.ROUTE_BLUETOOTH
                else -> CallAudioState.ROUTE_EARPIECE
            }
            inCallService?.setAudioRoute(newRoute)
            Log.i(TAG, "Standalone: Earpiece/Headset route selected ($newRoute)")
        }
    }

    fun setMuteMode(muted: Boolean) {
        inCallService?.setMuted(muted)
        Log.i(TAG, "Standalone: Mute state set to $muted")
    }

    fun playDtmfTone(c: Char) {
        activeCall?.playDtmfTone(c)
    }

    fun stopDtmfTone() {
        activeCall?.stopDtmfTone()
    }

    /** Music volume percent — from device profile. */
    val MUSIC_VOL_PERCENT: Int get() = profile.audio.musicVolPercent

    /** Run mixer discovery once on first audio bridge setup. */
    @Volatile private var discoveryDone = false

    private fun runMixerDiscovery() {
        if (discoveryDone) return
        discoveryDone = true
        Thread({
            try {
                val discovery = DeviceProfile.discoverMixerControls()
                for (line in discovery.lines()) {
                    if (line.isNotBlank()) Log.i(TAG, "MixerDiscovery: $line")
                }
                // Send summary to app log viewer (not the full dump)
                val cards = discovery.lines()
                    .filter { it.contains("[") && it.contains("]") && it.contains(":") }
                    .joinToString(", ") { it.trim() }
                val tinymix = if (DeviceProfile.tinymixBin.isNotEmpty())
                    DeviceProfile.tinymixBin else "NOT FOUND"
                appLog("ALSA: tinymix=$tinymix cards=[$cards]")
            } catch (e: Exception) {
                appLog("Mixer discovery failed: ${e.message}")
            }
        }, "MixerDiscovery").start()
    }

    /** Configure audio for GSM↔SIP bridge using the active device profile. */
    private fun configureAudioBridge() {
        if (listener == null) return // Standalone mode: let Android handle audio routing natively
        if (isMotoProfile()) markBridgeOpen()

        // Voice session is already up at ACTIVE. Do not wait for the playback
        // track or for a PCM status file. A waiting leg never reaches here.
        latchMotoInjectPolicy()
        if (profile.routing.sessionVoiceTxUnmute && writesSessionVoiceTxMute()) {
            if (isMotoProfile() && !voiceTxDecisionLogged) {
                voiceTxDecisionLogged = true
                appLog("voice tx unmute policy=${injectPolicy.wire} action=session-tuple")
            }
            VoiceTxMuteJob.schedule(VoiceTxMuteJob.CALL_ACTIVE, { isCallActive }, debounceMs = 500) { appLog(it) }
        } else if (isMotoProfile() && profile.routing.sessionVoiceTxUnmute && !voiceTxDecisionLogged) {
            voiceTxDecisionLogged = true
            appLog("voice tx unmute skipped policy=${injectPolicy.wire}")
        }

        mixerSetupCompletedAtMs = 0
        try {
            // Run ABOX/ALSA discovery on first call for diagnostics
            runMixerDiscovery()

            inCallService?.let { service ->
                val audioManager = service.getSystemService(Context.AUDIO_SERVICE) as? AudioManager

                // Samsung HAL params (incall_music_enabled, g_call_path, etc.)
                // are NOT set here — they fire in RtpSession.enableIncallMusic()
                // AFTER the AudioRecord is running.  Setting them before capture
                // kills VOICE_CALL capture (confirmed v2.8.33).

                if (profile.routing.requireSpeakerMode) {
                    service.setAudioRoute(CallAudioState.ROUTE_SPEAKER)
                }

                audioManager?.let { am ->
                    // Samsung Exynos HAL treats API mic mute as full uplink mute
                    // (blocks incall_music injection).  Pixel/Tensor use tinymix +
                    // INCALL_TX and can safely mute the physical mic here.
                    // Moto reads persist.gsm2computer.inject_policy instead.
                    if (isMotoProfile()) {
                        applyMotoMicrophone(am)
                    } else if (profile.routing.muteMicrophoneAtApi) {
                        am.isMicrophoneMute = true
                    } else {
                        am.isMicrophoneMute = false
                    }
                    enforceVolumes(am)

                    // Delay mixer/volume setup until speaker route change settles.
                    Thread({
                        try {
                            Thread.sleep(profile.routing.routeChangeDelayMs)
                            enforceVolumes(am)
                            batchMixerSetup()
                        } catch (_: Exception) {}
                    }, "VolEnforce").start()

                    // Samsung Exynos re-route dance REMOVED (v2.8.39):
                    // v2.8.38 tried earpiece→speaker re-route at t=3s to force HAL
                    // voice path recreation with incall_music ausage.  Results:
                    //   - Audio moved from speaker to earpiece and STAYED there
                    //   - 300ms delay was insufficient for route to settle
                    //   - No incall_music mixer controls exist on Exynos 9820 anyway
                    //     (confirmed: 1267 tinymix controls, zero match incall/inject)
                    //   - No ausage config files on this firmware
                    // The re-route served no purpose and broke speaker mode.

                    val tinymixStatus = if (DeviceProfile.tinymixBin.isNotEmpty()) "available" else "NOT FOUND"
                    val route = if (profile.routing.requireSpeakerMode) "speaker" else "earpiece"
                    appLog("Audio bridge: $route, mode=${am.mode}, tinymix=$tinymixStatus, profile=${profile.name}")
                }
            }
        } catch (e: Exception) {
            Log.e(TAG, "Failed to configure audio: ${e.message}")
        }
    }

    /** Set audio stream volumes for the GSM↔SIP bridge.
     *  Called multiple times: immediately, after delayed route change,
     *  and from RtpSession as a secondary safeguard. */
    fun enforceVolumes(am: AudioManager) {
        // Clear any stale ADJUST_MUTE flag from a previous call.
        // CRITICAL: Do NOT use ADJUST_MUTE on STREAM_VOICE_CALL — on
        // MSM8930 it kills the incall_music injection path, preventing
        // the agent's audio from reaching the GSM caller.  Speaker
        // silencing is handled by muteVoiceRx() at the ALSA mixer level.
        try {
            am.adjustStreamVolume(AudioManager.STREAM_VOICE_CALL, AudioManager.ADJUST_UNMUTE, 0)
        } catch (_: SecurityException) {}
        // Voice call volume: controls caller's voice on speaker.
        // MSM8930: minimum (1) — speaker silenced by muteVoiceRx via tinymix.
        // Exynos 9820: 80% — no muteVoiceRx, need loud speaker for mic capture.
        // Volume=0 can confuse audio policy into treating call as inactive.
        // Moto, while the bridge is open: the stream maximum. Build 352 asked
        // for 0 here (muteLocalEarpiece) and AudioService clamped that to 1 of
        // 8, which left the telephony output's stream 0 at -inf. The saved
        // index is restored when the bridge closes. A late pass after that
        // must not write the minimum back over the restored index.
        try {
            val vcVol = voiceCallIndex(am)
            if (vcVol != null) {
                am.setStreamVolume(AudioManager.STREAM_VOICE_CALL, vcVol, 0)
            }
        } catch (_: SecurityException) {}
        // Music stream controls incall_music injection level into
        // the modem uplink.  Lower value = quieter speaker + quieter
        // agent voice for the GSM caller.
        val maxMusic = am.getStreamMaxVolume(AudioManager.STREAM_MUSIC)
        val musicVol = (maxMusic * MUSIC_VOL_PERCENT / 100).coerceAtLeast(1)
        am.setStreamVolume(AudioManager.STREAM_MUSIC, musicVol, 0)
        // Read back actual values to confirm they stuck
        val actualVoice = am.getStreamVolume(AudioManager.STREAM_VOICE_CALL)
        val actualMusic = am.getStreamVolume(AudioManager.STREAM_MUSIC)
        val muted = am.isStreamMute(AudioManager.STREAM_VOICE_CALL)
        appLog("Vol: voice=$actualVoice(m=$muted), music=$actualMusic/$maxMusic(target=$musicVol)")
    }

    /** Pre-352 hangup path. Pixel and the other profiles still use only this. */
    private fun restoreAudio() {
        muteLocalEarpiece = false
        if (listener == null) return
        try {
            mixerSetupCompletedAtMs = 0
            batchMixerRestore()
            inCallService?.let { service ->
                service.setAudioRoute(CallAudioState.ROUTE_EARPIECE)
                val audioManager = service.getSystemService(Context.AUDIO_SERVICE) as? AudioManager
                audioManager?.let { am ->
                    am.isMicrophoneMute = false
                    if (profile.routing.incallMusicParam.isNotEmpty()) {
                        am.setParameters("${profile.routing.incallMusicParam}=false")
                    }
                    try {
                        am.adjustStreamVolume(AudioManager.STREAM_VOICE_CALL, AudioManager.ADJUST_UNMUTE, 0)
                    } catch (_: SecurityException) {}
                    try {
                        val maxVc = am.getStreamMaxVolume(AudioManager.STREAM_VOICE_CALL)
                        am.setStreamVolume(AudioManager.STREAM_VOICE_CALL, (maxVc * 2 / 3).coerceAtLeast(1), 0)
                    } catch (_: SecurityException) {}
                    Log.i(TAG, "Audio restored: earpiece, VoiceRx unmuted, echoRef=SLIM_RX, incall_music=false")
                }
            }
        } catch (e: Exception) {
            Log.e(TAG, "Failed to restore audio: ${e.message}")
        }
    }

    /**
     * Moto hangup restore. Mixer failure still attempts the mic write.
     * The saved mic value stays until that write returns, so a missing
     * AudioManager can be retried by the next hangup edge.
     */
    fun restoreBridgedAudio(reason: String) {
        if (!isMotoProfile()) return
        muteLocalEarpiece = false
        VoiceTxMuteJob.forget(VoiceTxMuteJob.CALL_ACTIVE)
        val skipped = synchronized(audioLock) {
            if (!bridgeAudioOpen && !micGate.open) {
                true
            } else {
                bridgeAudioOpen = false
                injectPolicyLatched = false
                voiceTxDecisionLogged = false
                false
            }
        }
        if (skipped) {
            Log.i(TAG, "audio restore skipped reason=$reason")
            restoreSavedVoiceVolume(currentAudioManager(), reason)
            return
        }
        Log.i(TAG, "audio restore reason=$reason")
        try {
            mixerSetupCompletedAtMs = 0
            batchMixerRestore()
        } catch (e: Exception) {
            Log.e(TAG, "Failed to restore mixer: ${e.message}")
        }
        try {
            inCallService?.setAudioRoute(CallAudioState.ROUTE_EARPIECE)
        } catch (e: Exception) {
            Log.e(TAG, "Failed to restore route: ${e.message}")
        }
        try {
            val am = currentAudioManager()
            restoreSavedVoiceVolume(am, reason)
            if (am != null) {
                Log.i(TAG, "Audio restored: earpiece, VoiceRx unmuted, echoRef=SLIM_RX, incall_music=untouched")
            }
        } catch (e: Exception) {
            Log.e(TAG, "Failed to restore volumes: ${e.message}")
        }
        restoreMotoMicrophone(reason)
    }

    /**
     * Put back the STREAM_VOICE_CALL index saved before the Moto bridge
     * raised it. Nothing is saved on the other profiles, and a missing
     * AudioManager leaves the index in prefs for the next restore or for
     * idle startup. Mic restore is separate and is not touched here.
     */
    fun restoreSavedVoiceVolume(am: AudioManager?, reason: String) {
        if (!isMotoProfile()) return
        val prefs = voicePrefs()
        if (prefs == null) {
            Log.w(TAG, "voice volume restore skipped no context reason=$reason")
            return
        }
        val stored = MotoVoiceVolume.restoreIndex(MotoVoiceVolume.read(prefs)) ?: return
        if (am == null) {
            Log.w(TAG, "voice volume restore kept prior=$stored reason=$reason")
            return
        }
        try {
            try {
                am.adjustStreamVolume(AudioManager.STREAM_VOICE_CALL, AudioManager.ADJUST_UNMUTE, 0)
            } catch (_: SecurityException) {}
            am.setStreamVolume(AudioManager.STREAM_VOICE_CALL, stored, 0)
        } catch (e: SecurityException) {
            Log.w(TAG, "voice volume restore kept prior=$stored reason=$reason err=${e.message}")
            return
        }
        if (!MotoVoiceVolume.clear(prefs)) {
            Log.w(TAG, "voice volume restore wrote $stored but the saved index remains reason=$reason")
        }
        val after = try {
            am.getStreamVolume(AudioManager.STREAM_VOICE_CALL)
        } catch (_: Exception) {
            -1
        }
        appLog("voice volume restore set=$stored after=$after reason=$reason")
    }

    /**
     * Null leaves the stream alone. Moto does that once the bridge has
     * closed so a late volume pass cannot replace the restored index.
     */
    private fun voiceCallIndex(am: AudioManager): Int? {
        if (isMotoProfile()) {
            if (!bridgeAudioOpen) return null
            return raiseMotoVoiceCall(am)
        }
        if (muteLocalEarpiece) {
            // Relay mode: mute the local earpiece entirely. Capture uses
            // VOICE_DOWNLINK and injection uses STREAM_MUSIC, so nothing here
            // depends on STREAM_VOICE_CALL being audible.
            return 0
        }
        if (profile.audio.voiceCallVolPercent > 0) {
            val maxVc = am.getStreamMaxVolume(AudioManager.STREAM_VOICE_CALL)
            return (maxVc * profile.audio.voiceCallVolPercent / 100).coerceAtLeast(1)
        }
        return 1
    }

    private fun raiseMotoVoiceCall(am: AudioManager): Int {
        val maxVc = am.getStreamMaxVolume(AudioManager.STREAM_VOICE_CALL)
        val live = am.getStreamVolume(AudioManager.STREAM_VOICE_CALL)
        val prefs = voicePrefs()
        if (prefs == null) {
            Log.w(TAG, "voice volume left unchanged: no app context")
            return live
        }
        val toSave = MotoVoiceVolume.indexToSave(MotoVoiceVolume.read(prefs), live)
        if (toSave != null && !MotoVoiceVolume.save(prefs, toSave)) {
            Log.w(TAG, "voice volume left unchanged: save failed prior=$toSave")
            return live
        }
        if (toSave != null) appLog("voice volume saved=$toSave max=$maxVc")
        return MotoVoiceVolume.activeIndex(maxVc)
    }

    private fun voicePrefs(): SharedPreferences? =
        GatewayApp.context()?.let { BridgeConfig.openPrefs(it) }

    /** Call objects this process already knows about. Empty before InCallService binds. */
    fun trackedCallStates(): List<Int> {
        val states = ArrayList<Int>()
        try {
            inCallService?.calls?.forEach { states.add(it.state) }
        } catch (_: Exception) {
        }
        if (activeCall != null) states.add(activeCallState)
        return states
    }

    fun wantsDeferredApiMute(): Boolean {
        if (!bridgeAudioOpen || apiMuteApplied) return false
        return isMotoProfile() && injectPolicy.micApi == MicApiAction.FORCE_TRUE
    }

    fun applyDeferredApiMute(reason: String) {
        val applied = synchronized(audioLock) {
            if (!bridgeAudioOpen || apiMuteApplied) return
            if (!isMotoProfile() || injectPolicy.micApi != MicApiAction.FORCE_TRUE) return
            val am = currentAudioManager() ?: return
            val before = am.isMicrophoneMute
            am.isMicrophoneMute = true
            apiMuteApplied = true
            appLog(
                "micMute before=$before action=set-true after=${am.isMicrophoneMute} " +
                    "policy=${injectPolicy.wire} when=$reason",
            )
            true
        }
        if (!applied) return
        if (reason == ApiMuteTiming.WRITE_DELAY) appLog(ApiMuteTiming.FALLBACK_LOG)
        logMutePath()
    }

    /** Logs 2 s capture windows around the API mute. Never changes the mute. */
    fun observeCaptureRms(rms: Int, nowMs: Long) {
        if (!isMotoProfile() || injectPolicy.micApi != MicApiAction.FORCE_TRUE) return
        val line = captureGuard.onFrame(rms, nowMs, apiMuteApplied) ?: return
        appLog(line)
    }

    private fun markBridgeOpen() {
        synchronized(audioLock) {
            if (bridgeAudioOpen) return
            if (micGate.open) {
                Log.w(TAG, "dropping unrestored mic prior at new bridge")
                micGate.commit()
            }
            bridgeAudioOpen = true
            apiMuteApplied = false
            captureGuard = ApiMuteCaptureGuard()
        }
    }

    private fun currentAudioManager(): AudioManager? =
        inCallService?.getSystemService(Context.AUDIO_SERVICE) as? AudioManager

    private fun logMutePath() {
        val cmd = DeviceProfile.resolveCmd(MotoIdleMixers.MUTE_PATH_CMD)
        if (cmd.isEmpty()) {
            appLog("mute path skipped: tinymix not found")
            return
        }
        val lines = TinymixTrace.run(cmd, timeoutMs = 4_000) { appLog(it) }
        appLog("mute path ${lines.joinToString(" | ")}")
    }

    fun isMotoProfile(): Boolean = profile.name.startsWith("Moto g84")

    /** One read per call. A later ACTIVE callback must not change the experiment. */
    private fun latchMotoInjectPolicy() {
        if (!isMotoProfile() || injectPolicyLatched) return
        injectPolicyLatched = true
        val raw = InjectPolicy.rawProperty()
        injectPolicy = InjectPolicy.parse(raw)
        val shown = if (InjectPolicy.recognized(raw)) {
            raw.trim().ifEmpty { "(default)" }
        } else {
            "${raw.trim()} unrecognized"
        }
        val line = "inject policy=${injectPolicy.wire} property=${InjectPolicy.PROPERTY} raw=$shown"
        if (InjectPolicy.logLevel(raw) == PolicyLogLevel.WARN) {
            Log.w(TAG, line)
            logCallback?.invoke(line)
        } else {
            appLog(line)
        }
    }

    private fun writesSessionVoiceTxMute(): Boolean {
        if (!isMotoProfile()) return true
        return injectPolicy.writesVoiceTxMute
    }

    private fun applyMotoMicrophone(am: AudioManager) {
        val before = am.isMicrophoneMute
        synchronized(audioLock) { micGate.open(before) }
        val action = when (injectPolicy.micApi) {
            MicApiAction.FORCE_FALSE -> {
                am.isMicrophoneMute = false
                MicApiAction.FORCE_FALSE.wire
            }
            MicApiAction.LEAVE -> MicApiAction.LEAVE.wire
            // The incall-music usecase is not up yet. Muting now makes Qualcomm
            // stream-mute Voice Tx instead of device-muting the handset mic.
            MicApiAction.FORCE_TRUE -> "deferred"
        }
        val after = am.isMicrophoneMute
        appLog("micMute before=$before action=$action after=$after policy=${injectPolicy.wire}")
    }

    private fun restoreMotoMicrophone(reason: String) {
        val am = currentAudioManager()
        val wrote = try {
            micGate.write(am != null) { prior ->
                val before = am!!.isMicrophoneMute
                am.isMicrophoneMute = prior
                appLog(
                    "micMute restore before=$before set=$prior after=${am.isMicrophoneMute} " +
                        "policy=${injectPolicy.wire}",
                )
            }
        } catch (e: Exception) {
            Log.w(TAG, "micMute restore write failed: ${e.message}")
            false
        }
        if (!wrote) {
            Log.w(TAG, "micMute restore kept prior=${micGate.pending()} reason=$reason")
        }
    }

    /**
     * Set up mixer controls for audio bridge using the device profile.
     * All commands batched into a single su call for efficiency.
     *
     * Commands reference bare 'tinymix' — [DeviceProfile.resolveCmd] replaces
     * it with the discovered full path at runtime.
     */
    fun batchMixerSetup() {
        if (profile.mixer.mixerSetupCmd.isEmpty()) {
            appLog("Mixer: no commands for ${profile.name}")
            return
        }
        val resolvedSetup = DeviceProfile.resolveCmd(profile.mixer.mixerSetupCmd)
        if (resolvedSetup.isEmpty()) {
            appLog("Mixer: tinymix NOT FOUND — cannot set controls for ${profile.name}")
            return
        }
        try {
            val bin = DeviceProfile.tinymixBin
            
            // Step 1: Readback BEFORE — see what HAL set during call setup
            // Only on Samsung ABOX devices to avoid "control not found" noise on Qualcomm
            if (profile.routing.isAbox) {
                val before = RootShell.execForOutput(buildString {
                    append("echo 'NSRC0B:'; $bin 'ABOX NSRC0 Bridge' 2>&1; ")
                    append("echo 'NSRC1B:'; $bin 'ABOX NSRC1 Bridge' 2>&1; ")
                    append("echo 'NSRC0:'; $bin 'ABOX NSRC0' 2>&1; ")
                    append("echo 'NSRC1:'; $bin 'ABOX NSRC1' 2>&1; ")
                    append("echo 'SPUS0:'; $bin 'ABOX SPUS OUT0' 2>&1")
                }, timeoutMs = 8000)
                appLog("Mixer BEFORE: $before")
            }
            // Step 2: Run mixer setup commands (all ABOX controls on card 0)
            // Use execForOutput to capture discovery/diagnostic output from setup commands
            val setupOutput = TinymixTrace.run(resolvedSetup, timeoutMs = 3000) { appLog(it) }
                .joinToString("\n")
            if (setupOutput.isNotBlank()) appLog("Mixer setup: $setupOutput")
            mixerSetupCompletedAtMs = System.currentTimeMillis()

            // Step 3: Readback AFTER — verify controls were actually changed
            if (profile.routing.isAbox) {
                val readback = RootShell.execForOutput(buildString {
                    append("echo 'NSRC0B:'; $bin 'ABOX NSRC0 Bridge' 2>&1; ")
                    append("echo 'NSRC1B:'; $bin 'ABOX NSRC1 Bridge' 2>&1; ")
                    append("echo 'NSRC2B:'; $bin 'ABOX NSRC2 Bridge' 2>&1; ")
                    append("echo 'NSRC0:'; $bin 'ABOX NSRC0' 2>&1; ")
                    append("echo 'NSRC1:'; $bin 'ABOX NSRC1' 2>&1; ")
                    append("echo 'SPUS0:'; $bin 'ABOX SPUS OUT0' 2>&1")
                }, timeoutMs = 10000)
                appLog("Mixer AFTER: $readback")
            }
        } catch (e: Exception) {
            appLog("Mixer setup FAILED: ${e.message}")
        }
    }

    /** Restore mixer state when call ends using the device profile. */
    fun batchMixerRestore() {
        if (profile.mixer.mixerRestoreCmd.isEmpty()) {
            Log.i(TAG, "batchMixerRestore: no mixer commands for ${profile.name}")
            return
        }
        val resolvedRestore = DeviceProfile.resolveCmd(profile.mixer.mixerRestoreCmd)
        if (resolvedRestore.isEmpty()) {
            Log.i(TAG, "batchMixerRestore: tinymix not found, skipping")
            return
        }
        try {
            TinymixTrace.run(resolvedRestore, timeoutMs = 3000) { appLog(it) }
            appLog("Mixer restored")
        } catch (e: Exception) {
            appLog("Mixer restore FAILED: ${e.message}")
        }
    }

    /** Check if a GSM call is currently active */
    val isCallActive: Boolean
        get() = activeCall != null && activeCallState == Call.STATE_ACTIVE

    /** Get current call number */
    val currentNumber: String?
        get() = activeCall?.details?.handle?.schemeSpecificPart

    /**
     * Decide whether a post-restore tinymix readback [probe] still reflects an
     * active-call mixer state instead of the idle default. Used to detect a
     * defective teardown (e.g. the Pixel 7 defect that left mic mutes ON and
     * INCALL playback/mixers ON at REST) so [batchMixerRestore] can be retried.
     *
     * Pure over the probe string, so it is unit-testable without a device.
     * A control reports "stuck" if any watched control holds its in-call value:
     *  - mute/enable controls (Voice Tx/Rx Mute, INCALL playback, EP TX mixers,
     *    Incall_Music mixers) are stuck when =1
     *  - Main Mic Switch (generic Exynos) is stuck when =0 (mic disabled)
     *  - =N/A or a missing control never counts as stuck
     */
    fun mixerLooksStuck(probe: String): Boolean {
        if (probe.isBlank()) return false
        // name → value that indicates the call path is still up.
        // Mute/inject controls are active at 1; the Exynos mic switch is off (0) during a call.
        val stuckWhenOne = setOf(
            "Voice Call Mic Mute",
            "Incall Mic Mute",
            "Incall Playback Stream0",
            "EP2 TX Mixer INCALL_TX",
            "EP6 TX Mixer INCALL_TX",
            "Voice Tx Mute",
            "Voice Rx Device Mute",
            "Incall_Music Audio Mixer MultiMedia1",
            "Incall_Music Audio Mixer MultiMedia2",
        )
        val stuckWhenZero = setOf("Main Mic Switch")

        val values = HashMap<String, String>()
        // tolerate "Name = value", "Name=value", leading/trailing spaces, multiple lines
        val regex = Regex("""^\s*([^=\n]+?)\s*=\s*([^\n]*?)\s*$""")
        for (line in probe.lines()) {
            val m = regex.matchEntire(line) ?: continue
            values[m.groupValues[1]] = m.groupValues[2]
        }

        for ((control, value) in values) {
            if (value == "N/A") continue
            if (control in stuckWhenOne && value == "1") return true
            if (control in stuckWhenZero && value == "0") return true
        }
        return false
    }
}

/** True when [candidate] is a different object than the live call (call-waiting). */
internal fun isWaitingGsmCall(live: Any?, candidate: Any): Boolean =
    live != null && live !== candidate

/** True when [candidate] is the tracked live call. Null live is never a match. */
internal fun isLiveGsmCall(live: Any?, candidate: Any): Boolean = live === candidate

