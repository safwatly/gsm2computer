package com.gsm2computer.bridge.sms

/**
 * Turns SmsManager sent-intent result codes into the hub outbox ack.
 * [Activity.RESULT_OK] is -1. Anything else, including modem error 40, is
 * `failed`. No callback at all is `timeout`, not `sent`.
 */
internal object SmsSendOutcome {
    const val OK = -1

    data class Ack(val status: String, val error: String)

    fun decide(results: List<Int?>, timedOut: Boolean, rpErrors: List<Int?> = emptyList()): Ack {
        val anyFailure = results.any { it != null && it != OK }
        val anyMissing = results.isEmpty() || results.any { it == null }
        if (anyFailure) return Ack("failed", describe(results, rpErrors))
        if (timedOut && anyMissing) return Ack("timeout", "send result timeout")
        if (anyMissing) return Ack("failed", describe(results, rpErrors))
        return Ack("sent", "")
    }

    private fun describe(results: List<Int?>, rpErrors: List<Int?>): String {
        if (results.size <= 1) {
            return formatOne(results.firstOrNull(), rpErrors.firstOrNull())
        }
        return results.mapIndexed { index, code ->
            val rp = rpErrors.getOrNull(index)
            "part ${index + 1} ${formatOne(code, rp)}"
        }.joinToString("; ")
    }

    private fun formatOne(code: Int?, rp: Int?): String {
        if (code == null) return "missing"
        if (code == OK) return "ok"
        val name = when (code) {
            1 -> "GENERIC_FAILURE"
            2 -> "RADIO_OFF"
            3 -> "NULL_PDU"
            4 -> "NO_SERVICE"
            5 -> "LIMIT_EXCEEDED"
            6 -> "FDN_CHECK_FAILURE"
            16 -> "MODEM_ERROR"
            40 -> "MODEM_ERR"
            else -> ""
        }
        val base = if (name.isEmpty()) "result=$code" else "result=$code $name"
        return if (rp != null && rp != code) "$base rp=$rp" else base
    }
}
