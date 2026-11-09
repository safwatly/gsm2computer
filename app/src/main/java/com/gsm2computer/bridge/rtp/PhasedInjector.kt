package com.gsm2computer.bridge.rtp

import android.content.Context
import android.media.AudioDeviceInfo
import android.media.AudioManager
import android.media.AudioTrack
import android.util.Log
import com.gsm2computer.bridge.RootShell
import java.io.OutputStream

/**
 * Build 343 playback on the Moto g84: AudioTrack USAGE_MEDIA preferred onto
 * TYPE_TELEPHONY for the whole call. This diagnostic build plays the 0.75 s
 * marker unless `persist.gsm2computer.inject_marker` is `0`. Builds 342's
 * later phases (PCM 23 reorder, AFE-proxy, and the post-45s decide) were
 * never called from [start] and are not in this class.
 * Mixers are reverted at stop / call end. Meta keys are inject_*.
 */
class PhasedInjector(
    private val ctx: Context,
    private val trackRef: () -> AudioTrack,
    private val srcRate: Int,
    private val tapRef: () -> CaptureTap?,
    private val enableIncallMusic: () -> Unit,
    private val resample: (ByteArray) -> ByteArray,
) {
    private val t0 = System.currentTimeMillis()
    private val tag = "c" + (t0 / 1000)
    private val log = StringBuilder()
    private val phaseLog = StringBuilder()
    @Volatile private var phase = 0          // 0 = not started
    @Volatile private var markerBeeps = 0
    private var markerSample = 0L
    @Volatile private var proc: Process? = null
    @Volatile private var stdin: OutputStream? = null
    @Volatile private var bytes = 0L
    @Volatile private var trackBytes = 0L
    @Volatile private var alive = true
    private val lock = Object()

    private fun put(k: String, v: Any) { try { tapRef()?.putMeta(k, v) } catch (_: Exception) {} }
    private fun rel() = (System.currentTimeMillis() - t0) / 1000.0
    private fun logLine(s: String) {
        val l = String.format("t=%.2f %s", rel(), s)
        Log.i(TAG, l)
        synchronized(log) { if (log.length < 12000) log.append(l).append('\n') }
        put("inject_log", synchronized(log) { log.toString() })
    }
    private fun phaseMark(s: String) {
        synchronized(phaseLog) { phaseLog.append(String.format("t=%.2fs epoch=%d %s\n", rel(), System.currentTimeMillis(), s)) }
        put("inject_phases", synchronized(phaseLog) { phaseLog.toString() })
        logLine("PHASE $s")
    }
    private fun sh(cmd: String, ms: Long = 3000) = try { RootShell.execForOutput(cmd, ms).trim().replace('\n', ' ') } catch (e: Exception) { "ERR ${e.message}" }

    // ── output path ──
    fun write(buf: ByteArray, n: Int) {
        val pcm = if (n == buf.size) buf else buf.copyOf(n)
        applyMarker(pcm)
        if (phase == 1 || phase == 4) {
            trackRef().write(pcm, 0, pcm.size); trackBytes += pcm.size
        } else {
            val s = synchronized(lock) { stdin }
            if (s != null) try {
                s.write(if (srcRate == 48000) pcm else resample(pcm)); bytes += pcm.size
            } catch (_: Exception) {}
            else Thread.sleep(18) // pace while the raw device is (re)opening
        }
    }

    /**
     * Replaces the start of the buffer AudioTrack.write will see. One beep is
     * 0.5 s of tone and 0.25 s of zeros (0.75 s). [RtpSession] records
     * playbackRms before this runs, and playback.wav after it.
     */
    private fun applyMarker(pcm: ByteArray) {
        val nb = markerBeeps; if (nb <= 0) return
        val onS = srcRate / 2; val cycle = srcRate * 3 / 4
        val total = cycle.toLong() * nb
        for (i in 0 until pcm.size / 2) {
            val k = markerSample
            if (k >= total) { markerBeeps = 0; return }
            val inBeep = (k % cycle) < onS
            val v = if (inBeep) (Math.sin(2 * Math.PI * 1000.0 * k / srcRate) * 9000).toInt() else 0
            pcm[i * 2] = (v and 0xFF).toByte(); pcm[i * 2 + 1] = ((v shr 8) and 0xFF).toByte()
            markerSample = k + 1
        }
    }
    private fun beeps(n: Int) { markerSample = 0; markerBeeps = n }

    // ── raw injector ──
    private fun killInj() {
        synchronized(lock) {
            try { stdin?.close() } catch (_: Exception) {}
            try { Thread.sleep(100); proc?.destroy() } catch (_: Exception) {}
            stdin = null; proc = null
        }
    }
    private fun dump(label: String) {
        val base = "${tag}_$label"
        sh("(sh $DUMP $base >/dev/null 2>&1 &)", 2000)
        Thread({
            try {
                Thread.sleep(12_000)
                val f = "/data/local/tmp/calldump/$base"
                put("inject_dump_${label}_files", "$f.*")
                put("inject_dump_${label}_start", sh("cat $f.start 2>/dev/null"))
                put("inject_tinymixv_diff_$label", RootShell.execForOutput("grep -vxFf /data/local/tmp/moto-mixer-baseline-v.txt $f.tinymixv.txt 2>/dev/null | head -c 5000", 4000))
                put("inject_tinymix_diff_$label", RootShell.execForOutput("head -c 5000 $f.diff.txt 2>/dev/null", 3000))
                put("inject_pcm_status_$label", RootShell.execForOutput("head -c 3000 $f.pcm.txt 2>/dev/null", 3000))
                put("inject_dmesg_$label", RootShell.execForOutput("tail -c 3000 $f.dmesgk.txt 2>/dev/null; echo ---; tail -c 2000 $f.dmesg.txt 2>/dev/null", 3000))
                put("inject_hal_$label", RootShell.execForOutput("tail -c 3000 $f.hal.txt 2>/dev/null", 3000))
                put("inject_afe_$label", RootShell.execForOutput("cat $f.afe.txt 2>/dev/null", 2000))
            } catch (_: Exception) {}
        }, "calldump-read").apply { isDaemon = true }.start()
    }

    // ── phases ──
    private fun phase1(again: Boolean) {
        killInj()
        val track = trackRef()
        val am = ctx.getSystemService(Context.AUDIO_SERVICE) as? AudioManager
        val dev = am?.getDevices(AudioManager.GET_DEVICES_OUTPUTS)?.firstOrNull { it.type == AudioDeviceInfo.TYPE_TELEPHONY }
        // The session already armed this track while it was stopped. Calling
        // setPreferredDevice here would recreate the native track via restoreTrack_l.
        if (track.playState != AudioTrack.PLAYSTATE_PLAYING) {
            try { track.play() } catch (e: Exception) { logLine("P1 play failed ${e.message}") }
        }
        enableIncallMusic()
        phase = if (again) 4 else 1
        val marker = markerBeepCount()
        if (marker > 0) beeps(marker)
        phaseMark(
            "P1${if (again) "b" else ""}_pixel_audiotrack telephony_found=${dev != null} " +
                "id=${dev?.id} armed=${track.playState} marker=$marker",
        )
        Thread({
            try {
                Thread.sleep(1500)
                val live = trackRef()
                val s = "routed=${live.routedDevice?.type} playState=${live.playState} state=${live.state} underruns=${live.underrunCount}"
                put("inject_p1${if (again) "b" else ""}_track", s); logLine("P1 track $s")
            } catch (_: Exception) {}
        }, "p1-check").apply { isDaemon = true }.start()
    }

    fun start() {
        put("inject_route", "p1only343")
        phase1(false)
        dump("p1_0s")
        Thread({
            var tick = 0
            while (alive) {
                try {
                    Thread.sleep(1000); if (!alive) break
                    tick++
                    if (tick == 30) dump("p1_30s") // 343: P1 only, no phase switching
                    put("inject_bytes", bytes); put("inject_track_bytes", trackBytes); put("inject_phase_now", phase)
                } catch (_: InterruptedException) { break } catch (e: Exception) { logLine("tick error ${e.message}") }
            }
        }, "phased-injector").apply { isDaemon = true }.start()
    }

    fun stop() {
        alive = false
        killInj()
        val traced = com.gsm2computer.bridge.TinymixTrace.run(
            "tinymix 'Incall_Music Audio Mixer MultiMedia9' 0; tinymix 'Incall_Music_2 Audio Mixer MultiMedia9' 0",
            timeoutMs = 4000,
        ).joinToString(" ")
        val tail = sh("tail -3 /data/local/tmp/calldump/afeproxy.log", 2000)
        val r = "$traced $tail"
        put("inject_bytes", bytes); put("inject_track_bytes", trackBytes)
        phaseMark("STOP reverted=[$r]")
    }

    companion object {
        private const val TAG = "PhasedInjector"
        const val BIN = "/data/local/tmp/pcminject3"
        const val DUMP = "/data/local/tmp/moto-calldump.sh"
        const val AFE = "/data/local/tmp/moto-afeproxy.sh"
        fun available(): Boolean = RootShell.execForOutput("ls $BIN $AFE 2>/dev/null", 2000).lines().count { it.isNotBlank() } == 2
    }

    private fun markerBeepCount(): Int {
        val flag = try {
            val cls = Class.forName("android.os.SystemProperties")
            val get = cls.getMethod("get", String::class.java, String::class.java)
            get.invoke(null, "persist.gsm2computer.inject_marker", "1") as? String
        } catch (_: Throwable) {
            null
        }
        return injectMarkerBeeps(flag)
    }
}
