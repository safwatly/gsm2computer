package com.gsm2computer.bridge

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class RootShellTimeoutTest {

    @Test
    fun `timed out helper is killed and the next root command still runs`() {
        val previous = RootShell.shellLauncher
        RootShell.shellLauncher = { Runtime.getRuntime().exec(arrayOf("sh")) }
        try {
            RootShell.destroy()
            assertEquals("ok", RootShell.execForOutput("echo ok", 3_000))
            val slow = RootShell.execForOutput("sleep 30; echo hi", 300)
            assertFalse(slow.contains("hi"))
            val deadline = System.currentTimeMillis() + 5_000
            var next = ""
            while (System.currentTimeMillis() < deadline) {
                next = RootShell.execForOutput("echo next", 500)
                if (next.contains("next")) break
                Thread.sleep(100)
            }
            assertTrue("root worker stayed wedged, last output=[$next]", next.contains("next"))
        } finally {
            RootShell.destroy()
            RootShell.shellLauncher = previous
        }
    }
}
