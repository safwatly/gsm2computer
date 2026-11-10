package com.gsm2computer.bridge

import android.content.SharedPreferences

/**
 * STREAM_VOICE_CALL index for the Moto g84 bridge.
 *
 * Build 352 left the stream at 1 of 8 (the relay path asks for 0, and
 * AudioService clamps that to the minimum). The telephony output then
 * showed stream 0 at -inf. While the bridge is open the index is the
 * stream maximum. The pre-call index is written here before that raise
 * and put back on every restore path. A later read can already be max,
 * so an existing saved index is never replaced.
 */
internal object MotoVoiceVolume {
    const val PREF_KEY = "moto_saved_voice_call_volume"

    fun activeIndex(max: Int): Int = max.coerceAtLeast(0)

    /** Null when [stored] must be kept. Otherwise [live] is the index to save. */
    fun indexToSave(stored: Int?, live: Int): Int? = if (stored == null) live else null

    fun restoreIndex(stored: Int?): Int? = stored?.takeIf { it >= 0 }

    /**
     * Hangup write. Null while a bridge is open, so a late restore cannot
     * pull the stream down under a call that has already raised it, and
     * null when nothing was saved.
     */
    fun restoreWrite(bridgeOpen: Boolean, stored: Int?): Int? {
        if (bridgeOpen) return null
        return restoreIndex(stored)
    }

    fun read(prefs: SharedPreferences): Int? {
        if (!prefs.contains(PREF_KEY)) return null
        return prefs.getInt(PREF_KEY, -1).takeIf { it >= 0 }
    }

    fun save(prefs: SharedPreferences, index: Int): Boolean =
        prefs.edit().putInt(PREF_KEY, index).commit()

    fun clear(prefs: SharedPreferences): Boolean =
        prefs.edit().remove(PREF_KEY).commit()
}
