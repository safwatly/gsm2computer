package com.gsm2computer.bridge.realtime

/**
 * Uplink audio on the hub WebSocket: 16 kHz mono s16le, one binary frame.
 *
 * Header (8 bytes), then little-endian samples:
 * `G2` | version 1 | format 1 (pcm16 mono) | rate uint16 BE | reserved uint16.
 * 20 ms is 640 bytes of PCM, about 260 kbps with the WebSocket header.
 * A 48 kHz capture is averaged down; 16 kHz and 8 kHz are sent as captured.
 */
object UplinkWire {
    const val TARGET_RATE = 16000
    const val HEADER_BYTES = 8

    fun wireRate(captureRate: Int): Int =
        if (captureRate > TARGET_RATE) TARGET_RATE else captureRate.coerceAtLeast(8000)

    fun pack(pcm: ByteArray, rate: Int): ByteArray {
        val out = ByteArray(HEADER_BYTES + pcm.size)
        out[0] = 'G'.code.toByte()
        out[1] = '2'.code.toByte()
        out[2] = 1
        out[3] = 1
        out[4] = ((rate ushr 8) and 0xFF).toByte()
        out[5] = (rate and 0xFF).toByte()
        pcm.copyInto(out, HEADER_BYTES)
        return out
    }

    /**
     * Integer-ratio box filter. 48 kHz → 16 kHz averages each group of three
     * samples, which is enough for speech and stays off the audio thread's
     * critical path. A short tail is kept for the next read.
     */
    class Resampler(private val srcRate: Int, private val dstRate: Int) {
        private var pending = ByteArray(0)

        fun resample(input: ByteArray, length: Int): ByteArray {
            val src = concat(pending, input, length)
            if (srcRate == dstRate || dstRate <= 0 || srcRate < dstRate || srcRate % dstRate != 0) {
                pending = ByteArray(0)
                return src
            }
            val group = srcRate / dstRate
            val samples = src.size / 2
            val whole = samples / group
            val usedBytes = whole * group * 2
            pending = if (usedBytes < src.size) src.copyOfRange(usedBytes, src.size) else ByteArray(0)
            if (whole == 0) return ByteArray(0)
            val out = ByteArray(whole * 2)
            var inIndex = 0
            var outIndex = 0
            repeat(whole) {
                var acc = 0
                repeat(group) {
                    acc += sampleAt(src, inIndex)
                    inIndex += 2
                }
                val avg = (acc / group).coerceIn(-32768, 32767)
                out[outIndex] = (avg and 0xFF).toByte()
                out[outIndex + 1] = ((avg ushr 8) and 0xFF).toByte()
                outIndex += 2
            }
            return out
        }

        private fun sampleAt(buf: ByteArray, index: Int): Int {
            val lo = buf[index].toInt() and 0xFF
            val hi = buf[index + 1].toInt()
            return (hi shl 8) or lo
        }

        private fun concat(head: ByteArray, input: ByteArray, length: Int): ByteArray {
            val n = length.coerceIn(0, input.size)
            if (head.isEmpty()) {
                return if (n == input.size) input else input.copyOf(n)
            }
            val out = ByteArray(head.size + n)
            head.copyInto(out)
            input.copyInto(out, head.size, 0, n)
            return out
        }
    }
}
