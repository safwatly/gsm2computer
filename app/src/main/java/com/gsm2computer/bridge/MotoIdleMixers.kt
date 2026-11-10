package com.gsm2computer.bridge

import android.media.AudioManager
import android.telecom.Call
import android.telephony.TelephonyManager

/**
 * MM1 and MM2 are left On by older builds after hangup. Clear them once
 * while the phone has no call at all. MM9 is the HAL's incall-music uplink
 * and is not written. A restart in the middle of a call must not clear.
 */
object MotoIdleMixers {
    const val CLEAR_CMD =
        "tinymix 'Incall_Music Audio Mixer MultiMedia1' 0 2>/dev/null; " +
            "tinymix 'Incall_Music Audio Mixer MultiMedia2' 0 2>/dev/null"
    const val READBACK_CMD =
        "tinymix 'Incall_Music Audio Mixer MultiMedia1'; " +
            "tinymix 'Incall_Music Audio Mixer MultiMedia2'"

    /** Read both controls. No value arguments, so this is not a write. */
    const val MUTE_PATH_CMD =
        "tinymix 'Voice Tx Device Mute'; tinymix 'Voice Tx Mute'"

    fun shouldClear(
        profileName: String,
        callStates: Collection<Int>,
        telephonyCallState: Int,
        audioMode: Int,
    ): Boolean = profileName.startsWith("Moto g84") && noCallAtAll(callStates, telephonyCallState, audioMode)

    fun shouldForceMicUnmute(
        profileName: String,
        callStates: Collection<Int>,
        telephonyCallState: Int,
        audioMode: Int,
    ): Boolean = shouldClear(profileName, callStates, telephonyCallState, audioMode)

    /**
     * True only when every signal says the line is idle. An unknown
     * telephony state or audio mode is treated as a call, so a restart
     * that cannot see the live call does not clear or unmute.
     */
    fun noCallAtAll(callStates: Collection<Int>, telephonyCallState: Int, audioMode: Int): Boolean {
        if (callStates.any { occupiesLine(it) }) return false
        if (telephonyCallState != TelephonyManager.CALL_STATE_IDLE) return false
        if (audioMode != AudioManager.MODE_NORMAL) return false
        return true
    }

    fun occupiesLine(state: Int): Boolean =
        state != Call.STATE_DISCONNECTED && state != Call.STATE_DISCONNECTING
}
