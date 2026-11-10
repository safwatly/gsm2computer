package com.gsm2computer.bridge

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class MotoVoiceVolumeTest {

    @Test
    fun `active call uses the stream max and keeps the pre-call index`() {
        // Build 352 asked for 0 while muteLocalEarpiece was set. AudioService
        // clamped that to 1 of 8, and stream 0 on the telephony output was -inf.
        assertEquals(8, MotoVoiceVolume.activeIndex(8))
        assertEquals(5, MotoVoiceVolume.indexToSave(stored = null, live = 5))
        assertNull(MotoVoiceVolume.indexToSave(stored = 5, live = 8))
        assertEquals(5, MotoVoiceVolume.restoreIndex(5))
        assertNull(MotoVoiceVolume.restoreIndex(null))
        assertNull(MotoVoiceVolume.restoreIndex(-1))
    }
}
