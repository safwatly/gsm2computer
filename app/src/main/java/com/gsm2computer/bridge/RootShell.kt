package com.gsm2computer.bridge

import android.util.Log
import java.io.BufferedReader
import java.io.File
import java.io.InputStreamReader
import java.io.OutputStreamWriter
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.LinkedBlockingQueue

/**
 * Persistent root shell — opens `su` once and reuses it for all commands.
 * Eliminates repeated Magisk superuser popups.
 *
 * Usage:
 *   RootShell.exec("appops set --uid com.gsm2computer.bridge RECORD_AUDIO allow")
 *   val output = RootShell.execForOutput("tinymix 2>&1 | grep -i Incall")
 */
object RootShell {
    private const val TAG = "RootShell"
    private const val MARKER = "___ROOT_SHELL_DONE___"

    /** Android's unit-test jar throws from Log; a log line must not wedge the shell. */
    private fun note(level: String, msg: String) {
        try {
            when (level) {
                "e" -> Log.e(TAG, msg)
                "w" -> Log.w(TAG, msg)
                else -> Log.i(TAG, msg)
            }
        } catch (_: RuntimeException) {
        }
    }

    @Volatile private var process: Process? = null
    private var writer: OutputStreamWriter? = null
    private var reader: BufferedReader? = null

    // Serialize all commands through one thread to avoid interleaving
    private data class Command(
        val cmd: String,
        val latch: CountDownLatch,
        val timeoutMs: Long,
        var output: String = "",
        var exitCode: Int = -1,
        var done: Boolean = false,
    )
    private val commandQueue = LinkedBlockingQueue<Command>()
    @Volatile private var workerThread: Thread? = null
    @Volatile private var alive = false

    /** Test hook. Production launches `su`. */
    internal var shellLauncher: () -> Process = { Runtime.getRuntime().exec("su") }

    /** Ensure the persistent shell is running. Safe to call multiple times. */
    @Synchronized
    fun init() {
        if (alive && process != null) return
        try {
            val proc = shellLauncher()
            process = proc
            writer = OutputStreamWriter(proc.outputStream)
            reader = BufferedReader(InputStreamReader(proc.inputStream))
            alive = true

            workerThread = Thread({
                val owned = proc
                note("i", "Root shell worker started")
                while (alive && process === owned) {
                    try {
                        val cmd = commandQueue.poll(5, TimeUnit.SECONDS) ?: continue
                        if (process !== owned) break
                        executeInternal(cmd, owned)
                    } catch (_: InterruptedException) {
                        break
                    } catch (e: Exception) {
                        note("e", "Worker error: ${e.message}")
                    }
                }
                note("i", "Root shell worker exited")
            }, "RootShell-Worker").apply { isDaemon = true; start() }

            note("i", "Persistent root shell opened")
        } catch (e: Exception) {
            note("e", "Failed to open root shell: ${e.message}")
            alive = false
        }
    }

    /** Run a command, wait up to [timeoutMs] for completion. Returns exit code. */
    fun exec(cmd: String, timeoutMs: Long = 5000): Int {
        if (!alive) init()
        if (!alive) {
            // Fallback: try one-shot su -c
            return execFallback(cmd)
        }
        val command = Command(cmd, CountDownLatch(1), timeoutMs)
        commandQueue.put(command)
        if (!command.latch.await(timeoutMs, TimeUnit.MILLISECONDS)) {
            note("w", "Command timed out after ${timeoutMs}ms: ${cmd.take(80)}")
            return -1
        }
        return command.exitCode
    }

    /** Run a command and return its stdout. */
    fun execForOutput(cmd: String, timeoutMs: Long = 5000): String {
        if (!alive) init()
        if (!alive) return execFallbackOutput(cmd)
        val command = Command(cmd, CountDownLatch(1), timeoutMs)
        commandQueue.put(command)
        if (!command.latch.await(timeoutMs, TimeUnit.MILLISECONDS)) {
            note("w", "Command timed out after ${timeoutMs}ms: ${cmd.take(80)}")
            return ""
        }
        return command.output
    }

    /**
     * Runs [command] on the persistent shell. If it is still blocked when
     * [Command.timeoutMs] elapses, the shell and its helper children are
     * killed so this worker can exit and the next command can open a new one.
     */
    private fun executeInternal(command: Command, owned: Process) {
        val watchdog = Thread({
            try {
                val finished = command.latch.await(command.timeoutMs, TimeUnit.MILLISECONDS)
                val abandoned = synchronized(command) { !finished && !command.done }
                if (abandoned) {
                    note("w", "root command deadline ${command.timeoutMs}ms; killing shell")
                    killShellProcess(owned)
                }
            } catch (_: InterruptedException) {
            }
        }, "RootShell-Deadline").apply { isDaemon = true; start() }
        try {
            val w = writer ?: return
            val r = reader ?: return

            // Write command, then echo a unique marker + exit code
            w.write("${command.cmd}\necho \"${MARKER}\$?\"\n")
            w.flush()

            val sb = StringBuilder()
            while (true) {
                val line = r.readLine() ?: break
                if (line.startsWith(MARKER)) {
                    command.exitCode = line.removePrefix(MARKER).trim().toIntOrNull() ?: 0
                    break
                }
                sb.appendLine(line)
            }
            command.output = sb.toString().trimEnd()
        } catch (e: Exception) {
            note("e", "Execute error: ${e.message}")
            if (process === owned) killShellProcess(owned)
        } finally {
            synchronized(command) { command.done = true }
            command.latch.countDown()
            watchdog.interrupt()
        }
    }

    /** Kill the shell that is stuck inside [executeInternal], and its children. */
    @Synchronized
    private fun killShellProcess(owned: Process) {
        if (process !== owned) return
        alive = false
        val w = writer
        process = null
        writer = null
        reader = null
        try {
            w?.close()
        } catch (_: Exception) {
        }
        killProcessTree(owned)
        note("w", "root shell killed after command deadline")
    }

    private fun killProcessTree(proc: Process) {
        val root = processPid(proc)
        if (root > 0) killDescendants(root)
        try {
            proc.destroyForcibly()
        } catch (_: Exception) {
        }
    }

    /** Android's Process has no pid() at compileSdk 34. The concrete class still carries the id. */
    private fun processPid(proc: Process): Long {
        try {
            val method = proc.javaClass.methods.firstOrNull { it.name == "pid" && it.parameterCount == 0 }
            if (method != null) {
                val value = method.invoke(proc) as? Number
                if (value != null) return value.toLong()
            }
        } catch (_: Exception) {
        }
        var cls: Class<*>? = proc.javaClass
        while (cls != null) {
            try {
                val field = cls.getDeclaredField("pid")
                field.isAccessible = true
                val value = field.get(proc) as? Number
                if (value != null) return value.toLong()
            } catch (_: Exception) {
            }
            cls = cls.superclass
        }
        return -1L
    }

    private fun killDescendants(pid: Long) {
        val children = try {
            File("/proc/$pid/task/$pid/children").readText()
        } catch (_: Exception) {
            ""
        }
        for (token in children.trim().split(Regex("\\s+"))) {
            val child = token.toLongOrNull() ?: continue
            killDescendants(child)
            try {
                Runtime.getRuntime().exec(arrayOf("kill", "-9", child.toString()))
                    .waitFor(1, TimeUnit.SECONDS)
            } catch (_: Exception) {
            }
        }
    }

    /** Fallback for when persistent shell fails — single su -c call */
    private fun execFallback(cmd: String): Int {
        return try {
            val proc = Runtime.getRuntime().exec(arrayOf("su", "-c", cmd))
            if (proc.waitFor(5, TimeUnit.SECONDS)) proc.exitValue() else -1
        } catch (e: Exception) {
            note("w", "Fallback exec failed: ${e.message}")
            -1
        }
    }

    private fun execFallbackOutput(cmd: String): String {
        return try {
            val proc = Runtime.getRuntime().exec(arrayOf("su", "-c", cmd))
            val out = proc.inputStream.bufferedReader().readText().trim()
            proc.waitFor(5, TimeUnit.SECONDS)
            out
        } catch (e: Exception) {
            note("w", "Fallback exec failed: ${e.message}")
            ""
        }
    }

    @Synchronized
    fun destroy() {
        alive = false
        val thread = workerThread
        val proc = process
        val w = writer
        process = null
        writer = null
        reader = null
        workerThread = null
        thread?.interrupt()
        try {
            w?.close()
        } catch (_: Exception) {
        }
        if (proc != null) killProcessTree(proc)
        note("i", "Root shell destroyed")
    }
}
