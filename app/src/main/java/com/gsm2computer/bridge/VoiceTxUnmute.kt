package com.gsm2computer.bridge

/**
 * Session-aware `Voice Tx Mute` for the Moto g84 holi kernel.
 *
 * `msm_voice_mute_put` reads three integers: mute, session id, ramp ms.
 * `voc_set_tx_mute` looks that session up through `voice_get_idx_for_session`.
 * A one-value write leaves the session at 0. An id that is not in the VSID
 * table logs `voice_get_idx_for_session: Invalid session_id` and returns
 * index 0, so the iterator applies the mute to `VOC_PATH_PASSIVE`. That
 * write is not harmless. A known but idle VSID is stored in
 * `stream_tx.stream_mute` and `voc_start_voice_call` replays it before
 * `VOC_RUN`, so writing the unused MMode slot changes the next session
 * that uses it. This build writes one known VSID only.
 *
 * Build 349 waited for a per-substream status file to say RUNNING.
 * That tree is empty on 5.4.295-moto-17290-g91c2dab5855a, so
 * the write never ran. Both `VoiceMMode1 (*)` and `VoiceMMode2 (*)` are
 * registered for the whole boot, so the pcm list alone is not a live
 * session. The default is VoiceMMode1 `0x11C05000` (the VSID the HAL
 * logged on the 349 call). A HAL log or property that names exactly one
 * of the two MMode VSIDs wins. A pcm list that contains exactly one of
 * the two ids wins when the HAL text is empty or names both.
 *
 * The Moto tinymix is the Go helper in the Magisk module, copied to
 * `/data/local/tmp/tinymix` by `service.sh`. Helper 351 writes every
 * value in one ioctl. The kernel getter for this control is NULL, so
 * a readback cannot confirm the value; each attempt checks dmesg for a
 * new `Invalid session_id : 0` instead. `voc_start_playback: Invalid
 * session` is iteration noise and is not that check.
 *
 * Does not write `Voice Rx Device Mute` or `Voice Tx Device Mute`.
 */
internal object VoiceTxUnmute {
    const val CONTROL = "Voice Tx Mute"
    const val MMODE1_VSID = 0x11C05000L
    const val MMODE2_VSID = 0x11DC5000L

    /** Printed by `tinymix --version`. The bridge will not write an older helper. */
    const val HELPER_BANNER = "tinymix-helper 351"

    /** `voice_get_idx_for_session` prints this when the session id is 0. */
    const val INVALID_SESSION_ZERO = "Invalid session_id : 0"

    /** Tail only. The verdict also uses a full count of the same string. */
    const val DMESG_TAIL = "dmesg | tail -n 80"

    const val DMESG_COUNT =
        "dmesg | grep -F 'Invalid session_id : 0' | wc -l"

    /** From the trigger: immediately, then 1s, then 3s. Not cumulative. */
    val RETRY_DELAYS_MS: List<Long> = listOf(0L, 1_000L, 3_000L)

    data class Session(val prefix: String, val vsid: Long)

    /**
     * `strncmp` prefixes from `msm_pcm_open`, longest first.
     * `Voice` is the default voice frontend (`Voice (*)`). It is shorter
     * than `VoiceMMode1`, `VoiceMMode2`, and `Voice2`, so those win.
     */
    val sessions: List<Session> = listOf(
        Session("VoiceMMode1", MMODE1_VSID),
        Session("VoiceMMode2", MMODE2_VSID),
        Session("Voice2", 0x10DC1000),
        Session("VoWLAN", 0x10002000),
        Session("VoLTE", 0x10C02000),
        Session("QCHAT", 0x10803000),
        Session("Voice", 0x10C01000),
    ).sortedByDescending { it.prefix.length }

    data class Choice(val vsids: List<Long>, val why: String)

    /** Longest `msm_pcm_open` prefix of a PCM id. `VoiceMMode2 (*)` is not MMode1. */
    fun match(id: String): Session? =
        sessions.firstOrNull { id.startsWith(it.prefix) }

    /** PCM id from a `/proc/asound/pcm` line. The name field after it may be blank. */
    fun idFromPcmListLine(line: String): String? {
        val match = Regex("""^\d+-\d+: (.*?) :""").find(line.trim()) ?: return null
        val id = match.groupValues[1].trim()
        return id.ifEmpty { null }
    }

    /** `cat /proc/asound/pcm` only. No per-substream status walk. */
    const val PCM_LIST: String = "cat /proc/asound/pcm 2>/dev/null"

    /**
     * Last logcat lines plus properties. Empty output is a valid result
     * and selects the MMode1 default.
     */
    const val HAL_HINT: String =
        "{ logcat -d -t 400 2>/dev/null | grep -Ei 'voicemmode|11c05000|11dc5000|VSID'; " +
            "getprop 2>/dev/null | grep -Ei 'vsid|voicemmode'; } | tail -n 40"

    const val DMESG_HINT: String =
        "dmesg | grep -E 'Invalid session|voice_get_idx_for_session' | tail -n 8"

    /** One VSID. Never both MMode slots, and never an id outside the table. */
    fun choose(pcmList: String, halText: String): Choice {
        val hal = signals(halText)
        if (hal.size == 1) return Choice(hal.toList(), "hal")
        val pcm = mmodesInPcmList(pcmList)
        if (pcm.size == 1) return Choice(pcm.toList(), "pcm-id")
        return Choice(listOf(MMODE1_VSID), "default-mmode1")
    }

    fun signals(text: String): Set<Long> {
        val lower = text.lowercase()
        val found = LinkedHashSet<Long>()
        if (lower.contains("voicemmode1") || lower.contains("11c05000") || lower.contains(MMODE1_VSID.toString())) {
            found.add(MMODE1_VSID)
        }
        if (lower.contains("voicemmode2") || lower.contains("11dc5000") || lower.contains(MMODE2_VSID.toString())) {
            found.add(MMODE2_VSID)
        }
        return found
    }

    fun mmodesInPcmList(text: String): Set<Long> {
        val found = LinkedHashSet<Long>()
        for (line in text.lines()) {
            val id = idFromPcmListLine(line) ?: continue
            val vsid = match(id)?.vsid ?: continue
            if (vsid == MMODE1_VSID || vsid == MMODE2_VSID) found.add(vsid)
        }
        return found
    }

    /** `[mute=0, vsid, ramp_ms=0]`. Decimal, because tinymix parses integers in base 10. */
    fun unmuteCommand(vsid: Long): String = "tinymix '$CONTROL' 0 $vsid 0"

    fun helperVersionCommand(): String = "tinymix --version"

    /**
     * The kernel `.get` for Voice Tx Mute is NULL, so ELEM_READ returns
     * EPERM even as root. Do not treat a tinymix read as the value.
     */
    const val READBACK_SKIPPED = "voice tx unmute readback skipped reason=kernel-getter-null"

    fun oneLine(output: String): String =
        output.replace('\n', ' ').trim().take(300).ifEmpty { "(empty)" }

    fun attemptLine(
        reason: String,
        delayMs: Long,
        why: String,
        vsid: Long,
        argv: String,
        exitCode: Int,
        output: String,
    ): String =
        "voice tx unmute attempt reason=$reason delayMs=$delayMs why=$why " +
            "vsid=$vsid argv=$argv exit=$exitCode out=${oneLine(output)}"

    /**
     * [beforeCount] and [afterCount] are `wc -l` of [INVALID_SESSION_ZERO]
     * across dmesg. A new warning after the write fails the attempt.
     * [beforeTail] and [afterTail] are the end of dmesg, used for a positive
     * `msm_voice_mute_put` line when dynamic debug is on. `voc_start_playback:
     * Invalid session` is not the session-id check.
     */
    fun dmesgCheck(beforeCount: Int, afterCount: Int, beforeTail: String, afterTail: String): String {
        val fresh = freshKernelLines(beforeTail, afterTail)
        val bad = afterCount > beforeCount ||
            fresh.lineSequence().any { it.contains(INVALID_SESSION_ZERO) }
        val positive = fresh.lineSequence().filter { line ->
            line.contains("msm_voice_mute_put:") || line.contains("session_id=0x11c05000", ignoreCase = true)
        }.joinToString(" | ")
        val playbackNoise = fresh.lineSequence().any { line ->
            line.contains("voc_start_playback") &&
                line.contains("Invalid session") &&
                !line.contains("session_id")
        }
        val interesting = fresh.lineSequence().filter { line ->
            line.contains("Invalid session") || line.contains("voice_get_idx") || line.contains("msm_voice_mute")
        }.joinToString(" | ")
        return "voice tx unmute dmesg invalid_session_id_0=${if (bad) "present" else "absent"} " +
            "count_before=$beforeCount count_after=$afterCount " +
            "positive=${if (positive.isEmpty()) "(none)" else oneLine(positive)} " +
            "playback_invalid_session=${if (playbackNoise) "ignored" else "absent"} " +
            "fresh=${oneLine(interesting)}"
    }

    /** Lines in [after] that follow the last line of [before]. */
    fun freshKernelLines(before: String, after: String): String {
        val older = before.lines().filter { it.isNotBlank() }
        val newer = after.lines().filter { it.isNotBlank() }
        if (newer.isEmpty()) return ""
        if (older.isEmpty()) return newer.joinToString("\n")
        val anchor = older.last()
        val idx = newer.indexOfLast { it == anchor }
        if (idx < 0) return ""
        return newer.drop(idx + 1).joinToString("\n")
    }

    fun dmesgCount(text: String): Int =
        text.trim().lineSequence().firstOrNull { it.isNotBlank() }?.trim()?.toIntOrNull() ?: 0
}
