package com.gsm2computer.bridge

/**
 * Session-aware `Voice Tx Mute` for the Moto g84 holi kernel.
 *
 * `msm_voice_mute_put` in techpack/audio/asoc/msm-pcm-voice-v2.c reads three
 * integers: mute, session id, ramp ms. `voc_set_tx_mute` looks that session
 * up through `voice_get_idx_for_session`. A one-value write leaves the
 * session at 0, and the handler logs
 * `voice_get_idx_for_session: Invalid session_id : 0` (`%x` of 0). Index 0
 * is `VOC_PATH_PASSIVE`, not VoiceMMode1 (`VOC_PATH_VOICEMMODE1_PASSIVE`,
 * index 6). VSIDs are `q6voice.h` on the Motorola SM6375 tree (same
 * `msm-pcm-voice-v2.c` blob on bangkk, fogos, and
 * android_kernel_motorola_sm6375). The phone kernel is
 * 5.4.295-moto-17290-g91c2dab5855a; that exact SHA is not in the public
 * trees, and this control file is unchanged since the 2022 audio-kernel
 * import. `msm_pcm_open` matches `pcm->id` by prefix. A 5.4 dynamic id
 * is `"%s (*)"` and `pcm->name` is blank, so the probe reads `id:`.
 *
 * Does not write `Voice Rx Device Mute` or `Voice Tx Device Mute`.
 */
internal object VoiceTxUnmute {
    const val CONTROL = "Voice Tx Mute"

    data class Session(val prefix: String, val vsid: Long)

    /**
     * `strncmp` prefixes from `msm_pcm_open`, longest first.
     * `Voice` is the default voice frontend (`Voice (*)`). It is shorter
     * than `VoiceMMode1`, `VoiceMMode2`, and `Voice2`, so those win.
     */
    val sessions: List<Session> = listOf(
        Session("VoiceMMode1", 0x11C05000),
        Session("VoiceMMode2", 0x11DC5000),
        Session("Voice2", 0x10DC1000),
        Session("VoWLAN", 0x10002000),
        Session("VoLTE", 0x10C02000),
        Session("QCHAT", 0x10803000),
        Session("Voice", 0x10C01000),
    ).sortedByDescending { it.prefix.length }

    data class Active(val id: String, val vsid: Long)

    data class Seen(val id: String, val state: String, val vsid: Long?)

    data class Probe(val active: List<Active>, val seen: List<Seen>) {
        val runningUnknown: List<String>
            get() = seen.filter { it.state == "RUNNING" && it.vsid == null }.map { it.id }
    }

    /** Longest `msm_pcm_open` prefix of a PCM id. `VoiceMMode2 (*)` is not MMode1. */
    fun match(id: String): Session? =
        sessions.firstOrNull { id.startsWith(it.prefix) }

    /**
     * One line per PCM substream: `id=<pcm id> state=<alsa>`.
     * The id is the dynamic value, for example `VoiceMMode1 (*)`.
     * Only `RUNNING` is an active voice session.
     */
    fun parseProbe(text: String): Probe {
        val active = LinkedHashMap<Long, Active>()
        val seen = ArrayList<Seen>()
        val lineRe = Regex("""id=(.*) state=(\S+)""")
        for (raw in text.lines()) {
            val found = lineRe.find(raw.trim()) ?: continue
            val id = found.groupValues[1].trim()
            val state = found.groupValues[2]
            if (id.isEmpty()) continue
            val session = match(id)
            seen.add(Seen(id, state, session?.vsid))
            if (state == "RUNNING" && session != null) {
                active.putIfAbsent(session.vsid, Active(id, session.vsid))
            }
        }
        return Probe(active.values.toList(), seen)
    }

    /** PCM id from a `/proc/asound/pcm` line. The name field after it may be blank. */
    fun idFromPcmListLine(line: String): String? {
        val match = Regex("""^\d+-\d+: (.*?) :""").find(line.trim()) ?: return null
        val id = match.groupValues[1].trim()
        return id.ifEmpty { null }
    }

    /** One call log line: each interesting PCM, its state, and the chosen VSID. */
    fun summary(probe: Probe): String {
        val interesting = probe.seen.filter { it.vsid != null || it.state == "RUNNING" }
        val body = if (interesting.isEmpty()) {
            "no voice pcm"
        } else {
            interesting.joinToString(" ") { seen ->
                val vsid = if (seen.state == "RUNNING" && seen.vsid != null) " vsid=${seen.vsid}" else ""
                val tag = if (seen.state == "RUNNING" && seen.vsid == null) " unmatched" else ""
                "pcm='${seen.id}' state=${seen.state}$vsid$tag"
            }
        }
        val chosen = if (probe.active.isEmpty()) {
            "chosen=none"
        } else {
            "chosen=" + probe.active.joinToString(",") { "${it.id} vsid=${it.vsid}" }
        }
        return "voice tx unmute probe: $body $chosen"
    }

    /** `[mute=0, vsid, ramp_ms=0]`. Decimal, because tinymix parses integers in base 10. */
    fun unmuteCommand(vsid: Long): String = "tinymix '$CONTROL' 0 $vsid 0"

    /** Reads card status. No mixer write. */
    val probeScript: String = """
        for info in /proc/asound/card*/pcm*/info; do
          [ -f "${'$'}info" ] || continue
          id=${'$'}(sed -n 's/^id: //p' "${'$'}info" | head -n 1)
          base=${'$'}(dirname "${'$'}info")
          for st in "${'$'}base"/sub*/status; do
            [ -f "${'$'}st" ] || continue
            state=${'$'}(sed -n 's/^state: //p' "${'$'}st" | head -n 1)
            echo "id=${'$'}{id} state=${'$'}{state:-closed}"
          done
        done
    """.trimIndent()
}
