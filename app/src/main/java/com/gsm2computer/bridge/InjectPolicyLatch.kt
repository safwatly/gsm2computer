package com.gsm2computer.bridge

/**
 * One inject-policy read per Moto call.
 *
 * [latch] is the call-start read. [flowNote] and [peek] report that value
 * and do not read the property again, so a late AUDIO-FLOW sample cannot
 * pin the next call. [clear] runs after the RTP session has stopped.
 */
internal class InjectPolicyLatch {
    private var latchedFlag = false
    private var policy: InjectPolicy = InjectPolicy.DEFAULT

    val latched: Boolean get() = synchronized(this) { latchedFlag }

    fun latch(raw: String?): LatchResult = synchronized(this) {
        if (latchedFlag) return LatchResult(policy, fresh = false)
        policy = InjectPolicy.parse(raw)
        latchedFlag = true
        LatchResult(policy, fresh = true)
    }

    fun peek(): InjectPolicy = synchronized(this) { policy }

    /** Empty when the latched policy still opens voice-call capture. */
    fun flowNote(): String = synchronized(this) {
        if (policy.opensVoiceCapture) "" else policy.captureDisabledLog()
    }

    fun clear() = synchronized(this) {
        latchedFlag = false
    }

    data class LatchResult(val policy: InjectPolicy, val fresh: Boolean)
}
