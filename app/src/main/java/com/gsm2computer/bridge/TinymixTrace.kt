package com.gsm2computer.bridge

import android.util.Log

/**
 * Runs each statement of a mixer script on its own and records the exit
 * status. Profile commands chain with `;` and `2>/dev/null`, which used to
 * hide a failed write behind the last command.
 */
object TinymixTrace {
    private const val TAG = "Tinymix"
    private const val MARKER = "tinymix_exit:"

    fun statements(script: String): List<String> =
        script.split(';').map { it.trim() }.filter { it.isNotEmpty() }

    /**
     * Drop `2>/dev/null` and merge stderr into stdout. A rejected tinymix
     * write reports the error on stderr; the root shell only keeps stdout.
     */
    fun captureCommand(part: String): String {
        val stripped = part.replace("2>/dev/null", "").trim()
        return "{ $stripped; } 2>&1"
    }

    fun line(cmd: String, exitCode: Int, output: String): String {
        val out = output.replace('\n', ' ').trim().take(160)
        return "tinymix write exit=$exitCode cmd=$cmd out=$out"
    }

    /** Split a command's stdout from the trailing `tinymix_exit:N` marker. */
    fun exitOf(output: String): Pair<Int, String> {
        val lines = output.lines()
        val marker = lines.lastOrNull { it.startsWith(MARKER) }
        val code = marker?.removePrefix(MARKER)?.trim()?.toIntOrNull() ?: -1
        val body = lines.filter { it != marker }.joinToString("\n").trim()
        return code to body
    }

    fun run(script: String, timeoutMs: Long = 3_000, log: (String) -> Unit = {}): List<String> {
        val lines = ArrayList<String>()
        for (part in statements(script)) {
            val raw = try {
                RootShell.execForOutput("${captureCommand(part)}; echo $MARKER\$?", timeoutMs)
            } catch (e: Exception) {
                "$MARKER-1\n${e.message ?: ""}"
            }
            val (code, body) = exitOf(raw)
            val logged = line(part, code, body)
            Log.i(TAG, logged)
            log(logged)
            lines.add(logged)
        }
        return lines
    }
}
