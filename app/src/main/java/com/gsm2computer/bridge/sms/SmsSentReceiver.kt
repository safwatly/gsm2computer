package com.gsm2computer.bridge.sms

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.CountDownLatch

/**
 * Explicit, non-exported target for SmsManager's sent PendingIntent.
 * A dynamic receiver registered below API 33 is exported. Other apps cannot
 * send to this component. The platform can still deliver the PendingIntent
 * this process created.
 */
class SmsSentReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        SmsSentHub.onResult(intent, resultCode)
    }
}

internal object SmsSentHub {
    class Slot(
        val results: Array<Int?>,
        val rpErrors: Array<Int?>,
        val latch: CountDownLatch,
    )

    private val pending = ConcurrentHashMap<String, Slot>()

    fun register(id: String, count: Int): Slot {
        val slot = Slot(arrayOfNulls(count), arrayOfNulls(count), CountDownLatch(count))
        pending[id] = slot
        return slot
    }

    fun finish(id: String) {
        pending.remove(id)
    }

    fun onResult(intent: Intent, resultCode: Int) {
        val id = intent.getStringExtra("id") ?: return
        val rp = if (intent.hasExtra("errorCode")) intent.getIntExtra("errorCode", 0) else null
        record(id, intent.getIntExtra("part", 0), resultCode, rp)
    }

    fun record(id: String, part: Int, resultCode: Int, rp: Int?) {
        val slot = pending[id] ?: return
        if (part !in slot.results.indices || slot.results[part] != null) return
        slot.results[part] = resultCode
        if (rp != null) slot.rpErrors[part] = rp
        slot.latch.countDown()
    }
}
