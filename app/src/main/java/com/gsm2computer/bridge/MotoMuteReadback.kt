package com.gsm2computer.bridge

/**
 * Raw getter output for the Moto voice-mute controls.
 *
 * Both controls are read with no value arguments. A value would be a
 * write: `Voice Tx Mute` is mute/VSID/ramp, and `Voice Rx Device Mute`
 * has killed VOICE_CALL capture on this phone. The helper prints every
 * integer element on one line, so a successful Voice Tx Mute read is the
 * three-element tuple. The kernel getter is often EPERM; that text is
 * the readback too.
 */
internal object MotoMuteReadback {
    const val VOICE_TX_MUTE = "Voice Tx Mute"
    const val VOICE_RX_DEVICE_MUTE = "Voice Rx Device Mute"

    fun readArgv(control: String): String = "tinymix '$control'"

    /**
     * One shell script. Markers do not contain the word tinymix, so
     * [DeviceProfile.resolveCmd] can rewrite the binary without touching them.
     */
    fun script(): String {
        val tx = TinymixTrace.captureCommand(readArgv(VOICE_TX_MUTE))
        val rx = TinymixTrace.captureCommand(readArgv(VOICE_RX_DEVICE_MUTE))
        return "echo @@tx; $tx; echo @@tx_exit:\$?; echo @@rx; $rx; echo @@rx_exit:\$?"
    }

    fun missing(): String = "txMute=[tinymix-missing] rxDevMute=[tinymix-missing]"

    /** `txMute=[exit=N raw] rxDevMute=[exit=N raw]`, raw text unchanged apart from newlines. */
    fun format(output: String): String {
        val tx = slice(output, "@@tx", "@@rx")
        val rx = slice(output, "@@rx", null)
        return "txMute=${field(tx, "@@tx_exit:")} rxDevMute=${field(rx, "@@rx_exit:")}"
    }

    private fun slice(output: String, start: String, end: String?): String {
        val from = output.indexOf(start)
        if (from < 0) return ""
        val body = output.substring(from + start.length)
        if (end == null) return body
        val to = body.indexOf(end)
        return if (to < 0) body else body.substring(0, to)
    }

    private fun field(section: String, marker: String): String {
        val lines = section.lines()
        val exitLine = lines.lastOrNull { it.trim().startsWith(marker) }
        val code = exitLine?.trim()?.removePrefix(marker)?.trim()?.toIntOrNull() ?: -1
        val body = lines
            .filter { it.trim() != exitLine?.trim() && it.isNotBlank() }
            .joinToString(" ") { it.trim() }
            .ifEmpty { "(empty)" }
        return "[exit=$code $body]"
    }
}
