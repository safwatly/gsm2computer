package com.gsm2computer.bridge

/**
 * One read-only tinymix listing during a Moto call, about [DELAY_MS]
 * after the audio session starts. The helper is the same binary the
 * Voice Tx Mute writes use. Only VOC, Voice, MultiMedia9, and Incall
 * controls are logged.
 */
internal object MotoActiveMixDump {
    const val DELAY_MS = 5_000L
    const val LOG_PREFIX = "TINYMIX-SNAPSHOT"
    private const val LIMIT = 80

    /** No values: a bare `tinymix` lists controls and does not write. */
    fun listArgv(): String = "tinymix"

    fun relevant(line: String): Boolean {
        val lower = line.lowercase()
        return lower.contains("voc") ||
            lower.contains("voice") ||
            lower.contains("multimedia9") ||
            lower.contains("incall")
    }

    fun filter(dump: String): List<String> =
        dump.lines().map { it.trim() }.filter { it.isNotEmpty() && relevant(it) }

    fun logLines(dump: String): List<String> {
        val rows = filter(dump)
        if (rows.isEmpty()) {
            return listOf("$LOG_PREFIX: (no VOC/Voice/MultiMedia9/Incall controls)")
        }
        val shown = rows.take(LIMIT).map { "$LOG_PREFIX: $it" }
        if (rows.size <= LIMIT) return shown
        return shown + "$LOG_PREFIX: truncated ${rows.size - LIMIT} more"
    }

    fun helperMismatch(output: String): String {
        val raw = output.replace('\n', ' ').trim().take(180).ifEmpty { "(empty)" }
        return "$LOG_PREFIX: helper-mismatch $raw"
    }
}
