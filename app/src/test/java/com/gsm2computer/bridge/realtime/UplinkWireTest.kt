package com.gsm2computer.bridge.realtime

import org.junit.Assert.assertEquals
import org.junit.Test

class UplinkWireTest {

    @Test
    fun wireRateCapsAt16k() {
        assertEquals(16000, UplinkWire.wireRate(48000))
        assertEquals(16000, UplinkWire.wireRate(16000))
        assertEquals(8000, UplinkWire.wireRate(8000))
    }

    @Test
    fun averages48kDownTo16k() {
        val src = ByteArray(6)
        putSample(src, 0, 0)
        putSample(src, 1, 300)
        putSample(src, 2, 600)
        val out = UplinkWire.Resampler(48000, 16000).resample(src, src.size)
        assertEquals(2, out.size)
        assertEquals(300, sampleAt(out, 0))
    }

    @Test
    fun holdsAPartialGroupForTheNextRead() {
        val resampler = UplinkWire.Resampler(48000, 16000)
        val first = ByteArray(4)
        putSample(first, 0, 10)
        putSample(first, 1, 20)
        assertEquals(0, resampler.resample(first, first.size).size)
        val second = ByteArray(2)
        putSample(second, 0, 30)
        val out = resampler.resample(second, second.size)
        assertEquals(2, out.size)
        assertEquals(20, sampleAt(out, 0))
    }

    @Test
    fun packWritesTheG2Header() {
        val pcm = byteArrayOf(0x34, 0x12)
        val frame = UplinkWire.pack(pcm, 16000)
        assertEquals('G'.code.toByte(), frame[0])
        assertEquals('2'.code.toByte(), frame[1])
        assertEquals(1.toByte(), frame[2])
        assertEquals(1.toByte(), frame[3])
        assertEquals(0x3e.toByte(), frame[4])
        assertEquals(0x80.toByte(), frame[5])
        assertEquals(0x34.toByte(), frame[8])
        assertEquals(0x12.toByte(), frame[9])
    }

    private fun putSample(buf: ByteArray, index: Int, sample: Int) {
        buf[index * 2] = (sample and 0xFF).toByte()
        buf[index * 2 + 1] = ((sample ushr 8) and 0xFF).toByte()
    }

    private fun sampleAt(buf: ByteArray, index: Int): Int {
        val lo = buf[index * 2].toInt() and 0xFF
        val hi = buf[index * 2 + 1].toInt()
        return (hi shl 8) or lo
    }
}
