package com.gsm2computer.bridge

import android.util.Log

/**
 * Writes the chosen `Voice Tx Mute` tuple on a daemon thread.
 * Each trigger runs at +0, +1s and +3s from that trigger, and stops
 * when [alive] goes false (call ended). Stderr is merged into the
 * logged output because the root shell keeps stdout only.
 */
internal object VoiceTxMuteJob {
    private const val TAG = "VoiceTxMute"
    private val lastAt = HashMap<String, Long>()

    fun schedule(
        reason: String,
        alive: () -> Boolean,
        debounceMs: Long = 0L,
        log: (String) -> Unit,
    ) {
        if (debounceMs > 0) {
            val now = System.currentTimeMillis()
            synchronized(lastAt) {
                val prev = lastAt[reason] ?: Long.MIN_VALUE
                if (now - prev < debounceMs) {
                    emit(log, "voice tx unmute schedule skipped reason=$reason debounceMs=$debounceMs")
                    return
                }
                lastAt[reason] = now
            }
        }
        val delays = VoiceTxUnmute.RETRY_DELAYS_MS.joinToString(",")
        emit(log, "voice tx unmute schedule reason=$reason delays=$delays")
        Thread({
            val t0 = System.currentTimeMillis()
            for (delay in VoiceTxUnmute.RETRY_DELAYS_MS) {
                if (!waitUntil(t0 + delay, alive)) {
                    emit(log, "voice tx unmute stop reason=$reason delayMs=$delay call ended")
                    return@Thread
                }
                attempt(reason, delay, log)
            }
            val dmesg = read(VoiceTxUnmute.DMESG_HINT, 2000)
            emit(log, "voice tx unmute dmesg reason=$reason out=${VoiceTxUnmute.oneLine(dmesg)}")
        }, "voice-tx-unmute").apply { isDaemon = true }.start()
    }

    private fun attempt(reason: String, delayMs: Long, log: (String) -> Unit) {
        val pcm = read(VoiceTxUnmute.PCM_LIST, 2000)
        val hal = read(VoiceTxUnmute.HAL_HINT, 2500)
        val choice = VoiceTxUnmute.choose(pcm, hal)
        emit(
            log,
            "voice tx unmute choose reason=$reason delayMs=$delayMs " +
                "vsid=${choice.vsids.joinToString(",")} why=${choice.why}",
        )
        if (DeviceProfile.tinymixBin.isEmpty()) {
            val argv = VoiceTxUnmute.unmuteCommand(choice.vsids.first())
            emit(log, "voice tx unmute: tinymix not found reason=$reason delayMs=$delayMs argv=$argv")
            return
        }
        for (vsid in choice.vsids) {
            val argv = DeviceProfile.resolveCmd(VoiceTxUnmute.unmuteCommand(vsid))
            val (code, body) = exec(argv)
            emit(log, VoiceTxUnmute.attemptLine(reason, delayMs, choice.why, vsid, argv, code, body))
        }
        val readArgv = DeviceProfile.resolveCmd(VoiceTxUnmute.readbackCommand())
        val (code, body) = exec(readArgv)
        emit(log, VoiceTxUnmute.readbackLine(reason, delayMs, readArgv, code, body))
    }

    private fun exec(argv: String): Pair<Int, String> {
        val raw = try {
            RootShell.execForOutput("${TinymixTrace.captureCommand(argv)}; echo tinymix_exit:\$?", 3000)
        } catch (e: Exception) {
            "tinymix_exit:-1\n${e.message ?: ""}"
        }
        val (code, body) = TinymixTrace.exitOf(raw)
        Log.i("Tinymix", TinymixTrace.line(argv, code, body))
        return code to body
    }

    private fun read(cmd: String, timeoutMs: Long): String =
        try {
            RootShell.execForOutput(cmd, timeoutMs)
        } catch (e: Exception) {
            Log.w(TAG, "voice tx unmute read failed: ${e.message}")
            ""
        }

    private fun waitUntil(deadline: Long, alive: () -> Boolean): Boolean {
        while (alive()) {
            val left = deadline - System.currentTimeMillis()
            if (left <= 0) return true
            try {
                Thread.sleep(left.coerceAtMost(200))
            } catch (_: InterruptedException) {
                return false
            }
        }
        return false
    }

    private fun emit(log: (String) -> Unit, line: String) {
        Log.i(TAG, line)
        try {
            log(line)
        } catch (_: Exception) {
        }
    }
}
