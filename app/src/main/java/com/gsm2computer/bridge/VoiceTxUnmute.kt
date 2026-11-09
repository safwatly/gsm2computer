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
 * index 6). Names and ids are `voc_get_session_id` / `q6voice.h` on the
 * Motorola SM6375 tree (same `msm-pcm-voice-v2.c` blob on bangkk, fogos,
 * and android_kernel_motorola_sm6375). The phone kernel is
 * 5.4.295-moto-17290-g91c2dab5855a; that exact SHA is not in the public
 * trees, and this control file is unchanged since the 2022 audio-kernel
 * import.
 *
 * Does not write `Voice Rx Device Mute` or `Voice Tx Device Mute`.
 */
internal object VoiceTxUnmute {
    const val CONTROL = "Voice Tx Mute"

    data class Session(val pcmName: String, val vsid: Long)

    /** PCM `name:` values that `voc_get_session_id` accepts, with their VSIDs. */
    val sessions: List<Session> = listOf(
        Session("VoiceMMode1", 0x11C05000),
        Session("VoiceMMode2", 0x11DC5000),
        Session("VoLTE session", 0x10C02000),
        Session("Voice2 session", 0x10DC1000),
        Session("QCHAT session", 0x10803000),
        Session("VoWLAN session", 0x10002000),
        Session("Voice session", 0x10C01000),
        Session("VoIP session", 0x10004000),
    )

    data class Active(val name: String, val vsid: Long)

    data class Probe(val active: List<Active>, val runningUnknown: List<String>)

    /**
     * One line per PCM substream from `/proc/asound`: `name=<pcm> state=<alsa>`.
     * Only `RUNNING` counts as an active voice session.
     */
    fun parseProbe(text: String): Probe {
        val active = LinkedHashMap<Long, Active>()
        val unknown = LinkedHashSet<String>()
        val lineRe = Regex("""name=(.*) state=(\S+)""")
        for (raw in text.lines()) {
            val match = lineRe.find(raw.trim()) ?: continue
            val name = match.groupValues[1].trim()
            val state = match.groupValues[2]
            if (state != "RUNNING") continue
            val session = sessions.firstOrNull { it.pcmName == name }
            if (session == null) {
                unknown.add(name)
            } else {
                active.putIfAbsent(session.vsid, Active(session.pcmName, session.vsid))
            }
        }
        return Probe(active.values.toList(), unknown.toList())
    }

    /** `[mute=0, vsid, ramp_ms=0]`. Decimal, because tinymix parses integers in base 10. */
    fun unmuteCommand(vsid: Long): String = "tinymix '$CONTROL' 0 $vsid 0"

    /** Reads card status. No mixer write. */
    val probeScript: String = """
        for info in /proc/asound/card*/pcm*/info; do
          [ -f "${'$'}info" ] || continue
          name=${'$'}(sed -n 's/^name: //p' "${'$'}info" | head -n 1)
          base=${'$'}(dirname "${'$'}info")
          for st in "${'$'}base"/sub*/status; do
            [ -f "${'$'}st" ] || continue
            state=${'$'}(sed -n 's/^state: //p' "${'$'}st" | head -n 1)
            echo "name=${'$'}{name} state=${'$'}{state:-closed}"
          done
        done
    """.trimIndent()
}
