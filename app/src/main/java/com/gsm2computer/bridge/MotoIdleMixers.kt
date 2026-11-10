package com.gsm2computer.bridge

/**
 * MM1 and MM2 are left On by older builds after hangup. Clear them once
 * while idle. MM9 is the HAL's incall-music uplink and is not written.
 */
object MotoIdleMixers {
    const val CLEAR_CMD =
        "tinymix 'Incall_Music Audio Mixer MultiMedia1' 0 2>/dev/null; " +
            "tinymix 'Incall_Music Audio Mixer MultiMedia2' 0 2>/dev/null"
    const val READBACK_CMD =
        "tinymix 'Incall_Music Audio Mixer MultiMedia1'; " +
            "tinymix 'Incall_Music Audio Mixer MultiMedia2'"

    fun shouldClear(profileName: String, callActive: Boolean): Boolean =
        profileName.startsWith("Moto g84") && !callActive
}
