package com.gsm2computer.bridge.calllog

import android.provider.CallLog
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale
import java.util.TimeZone

/**
 * Maps system [CallLog.Calls] rows into the values the Calls tab shows.
 *
 * Contact names are taken from [CallLog.Calls.CACHED_NAME]. That column lives in
 * the call log, so reading it needs [android.Manifest.permission.READ_CALL_LOG]
 * only. [android.Manifest.permission.READ_CONTACTS] is not used; when the cache
 * has no name the row shows the number (or a presentation label).
 *
 * Pure mapping lives here so unit tests can run without a ContentResolver.
 * [SystemCallLogReader] is the only type that touches the provider.
 */
enum class SystemCallDirection {
    INCOMING,
    OUTGOING,
    MISSED,
    REJECTED,
    BLOCKED,
    VOICEMAIL,
    ANSWERED_EXTERNALLY,
    UNKNOWN,
}

enum class CallNumberPresentation {
    ALLOWED,
    RESTRICTED,
    UNKNOWN,
    PAYPHONE,
}

/** One provider row, before presentation and direction are interpreted. */
internal data class SystemCallLogRow(
    val number: String?,
    val cachedName: String?,
    val type: Int,
    val dateMillis: Long,
    val durationSec: Long,
    val numberPresentation: Int,
)

data class SystemCallLogEntry(
    val number: String,
    val contactName: String?,
    val direction: SystemCallDirection,
    val dateMillis: Long,
    val durationSec: Long,
    val presentation: CallNumberPresentation,
) {
    val displayTitle: String
        get() = SystemCallLog.displayTitle(contactName, number, presentation)

    /** True when [number] is safe to copy into the dialer. Withheld numbers are not. */
    val canCallBack: Boolean
        get() = number.isNotEmpty()
}

sealed class SystemCallLogLoad {
    data class Ready(val entries: List<SystemCallLogEntry>) : SystemCallLogLoad()
    data object PermissionDenied : SystemCallLogLoad()
    data class Failed(val detail: String) : SystemCallLogLoad()
}

/**
 * Column reader so [SystemCallLog.rowsFrom] can be tested without an Android Cursor.
 */
internal interface CallLogColumns {
    fun moveToNext(): Boolean
    fun isNull(column: String): Boolean
    fun getString(column: String): String?
    fun getInt(column: String): Int
    fun getLong(column: String): Long
}

object SystemCallLog {
    const val DEFAULT_LIMIT = 100
    const val MAX_LIMIT = 500
    const val GENERIC_READ_FAILURE = "Could not read the call log"

    fun directionOf(type: Int): SystemCallDirection = when (type) {
        CallLog.Calls.INCOMING_TYPE -> SystemCallDirection.INCOMING
        CallLog.Calls.OUTGOING_TYPE -> SystemCallDirection.OUTGOING
        CallLog.Calls.MISSED_TYPE -> SystemCallDirection.MISSED
        CallLog.Calls.REJECTED_TYPE -> SystemCallDirection.REJECTED
        CallLog.Calls.BLOCKED_TYPE -> SystemCallDirection.BLOCKED
        CallLog.Calls.VOICEMAIL_TYPE -> SystemCallDirection.VOICEMAIL
        CallLog.Calls.ANSWERED_EXTERNALLY_TYPE -> SystemCallDirection.ANSWERED_EXTERNALLY
        else -> SystemCallDirection.UNKNOWN
    }

    fun directionLabel(direction: SystemCallDirection): String = when (direction) {
        SystemCallDirection.INCOMING -> "Incoming"
        SystemCallDirection.OUTGOING -> "Outgoing"
        SystemCallDirection.MISSED -> "Missed"
        SystemCallDirection.REJECTED -> "Rejected"
        SystemCallDirection.BLOCKED -> "Blocked"
        SystemCallDirection.VOICEMAIL -> "Voicemail"
        SystemCallDirection.ANSWERED_EXTERNALLY -> "Answered elsewhere"
        SystemCallDirection.UNKNOWN -> "Unknown"
    }

    fun presentationOf(value: Int): CallNumberPresentation = when (value) {
        CallLog.Calls.PRESENTATION_RESTRICTED -> CallNumberPresentation.RESTRICTED
        CallLog.Calls.PRESENTATION_UNKNOWN -> CallNumberPresentation.UNKNOWN
        CallLog.Calls.PRESENTATION_PAYPHONE -> CallNumberPresentation.PAYPHONE
        else -> CallNumberPresentation.ALLOWED
    }

    fun displayTitle(
        contactName: String?,
        number: String,
        presentation: CallNumberPresentation,
    ): String {
        val name = contactName?.trim().orEmpty()
        if (name.isNotEmpty()) return name
        if (number.isNotEmpty()) return number
        return when (presentation) {
            CallNumberPresentation.RESTRICTED -> "Private number"
            CallNumberPresentation.PAYPHONE -> "Payphone"
            CallNumberPresentation.UNKNOWN, CallNumberPresentation.ALLOWED -> "Unknown"
        }
    }

    /** `m:ss`, or `h:mm:ss` once the call reaches an hour. Negative durations display as zero. */
    fun formatDuration(totalSeconds: Long): String {
        val safe = totalSeconds.coerceAtLeast(0)
        val hours = safe / 3600
        val minutes = (safe % 3600) / 60
        val seconds = safe % 60
        return if (hours > 0) {
            String.format(Locale.US, "%d:%02d:%02d", hours, minutes, seconds)
        } else {
            String.format(Locale.US, "%d:%02d", minutes, seconds)
        }
    }

    /** Date and time. The year is included only when it is not [nowMillis]'s year in [zone]. */
    fun formatWhen(dateMillis: Long, nowMillis: Long, zone: TimeZone): String {
        val pattern = if (yearOf(dateMillis, zone) == yearOf(nowMillis, zone)) {
            "dd/MM HH:mm"
        } else {
            "dd/MM/yyyy HH:mm"
        }
        val format = SimpleDateFormat(pattern, Locale.US)
        format.timeZone = zone
        return format.format(Date(dateMillis))
    }

    /**
     * Second line under the title: dialable number when the title is a name,
     * then direction, date, and duration.
     */
    fun detailLine(entry: SystemCallLogEntry, whenText: String): String {
        val parts = ArrayList<String>(4)
        if (entry.number.isNotEmpty() && entry.number != entry.displayTitle) {
            parts.add(entry.number)
        }
        parts.add(directionLabel(entry.direction))
        parts.add(whenText)
        parts.add(formatDuration(entry.durationSec))
        return parts.joinToString(" · ")
    }

    internal fun mapRow(row: SystemCallLogRow): SystemCallLogEntry {
        val trimmed = row.number?.trim().orEmpty()
        var presentation = presentationOf(row.numberPresentation)
        var number = trimmed
        when (trimmed) {
            PRIVATE_NUMBER_SENTINEL -> {
                number = ""
                if (presentation == CallNumberPresentation.ALLOWED) {
                    presentation = CallNumberPresentation.RESTRICTED
                }
            }
            UNKNOWN_NUMBER_SENTINEL -> {
                number = ""
                if (presentation == CallNumberPresentation.ALLOWED) {
                    presentation = CallNumberPresentation.UNKNOWN
                }
            }
            else -> {
                if (presentation == CallNumberPresentation.RESTRICTED ||
                    presentation == CallNumberPresentation.UNKNOWN
                ) {
                    number = ""
                }
            }
        }
        val name = row.cachedName?.trim()?.takeIf { it.isNotEmpty() }
        return SystemCallLogEntry(
            number = number,
            contactName = name,
            direction = directionOf(row.type),
            dateMillis = row.dateMillis,
            durationSec = row.durationSec.coerceAtLeast(0),
            presentation = presentation,
        )
    }

    /** Newest first. Equal timestamps keep their incoming order. */
    internal fun mapRows(rows: List<SystemCallLogRow>, limit: Int = DEFAULT_LIMIT): List<SystemCallLogEntry> {
        if (limit <= 0) return emptyList()
        return rows.map { mapRow(it) }
            .sortedByDescending { it.dateMillis }
            .take(limit)
    }

    internal fun rowsFrom(columns: CallLogColumns, limit: Int): List<SystemCallLogRow> {
        if (limit <= 0) return emptyList()
        val rows = ArrayList<SystemCallLogRow>(minOf(limit, 64))
        while (rows.size < limit && columns.moveToNext()) {
            val presentation = if (columns.isNull(CallLog.Calls.NUMBER_PRESENTATION)) {
                CallLog.Calls.PRESENTATION_ALLOWED
            } else {
                columns.getInt(CallLog.Calls.NUMBER_PRESENTATION)
            }
            val type = if (columns.isNull(CallLog.Calls.TYPE)) 0 else columns.getInt(CallLog.Calls.TYPE)
            val date = if (columns.isNull(CallLog.Calls.DATE)) 0L else columns.getLong(CallLog.Calls.DATE)
            val duration = if (columns.isNull(CallLog.Calls.DURATION)) {
                0L
            } else {
                columns.getLong(CallLog.Calls.DURATION)
            }
            rows.add(
                SystemCallLogRow(
                    number = columns.getString(CallLog.Calls.NUMBER),
                    cachedName = columns.getString(CallLog.Calls.CACHED_NAME),
                    type = type,
                    dateMillis = date,
                    durationSec = duration,
                    numberPresentation = presentation,
                )
            )
        }
        return rows
    }

    internal fun interpretQuery(
        permissionGranted: Boolean,
        rows: List<SystemCallLogRow>?,
        error: Throwable?,
        limit: Int,
    ): SystemCallLogLoad {
        if (!permissionGranted || error is SecurityException) {
            return SystemCallLogLoad.PermissionDenied
        }
        if (error != null) {
            return SystemCallLogLoad.Failed(failureDetail(error))
        }
        return SystemCallLogLoad.Ready(mapRows(rows.orEmpty(), limit))
    }

    internal fun failureDetail(error: Throwable): String {
        val line = error.message
            ?.lineSequence()
            ?.map { it.trim() }
            ?.firstOrNull { it.isNotEmpty() }
            .orEmpty()
        if (line.isEmpty()) return GENERIC_READ_FAILURE
        return if (line.length <= 160) line else line.take(157) + "..."
    }

    private fun yearOf(millis: Long, zone: TimeZone): Int {
        return Calendar.getInstance(zone).apply { timeInMillis = millis }.get(Calendar.YEAR)
    }

    private const val PRIVATE_NUMBER_SENTINEL = "-1"
    private const val UNKNOWN_NUMBER_SENTINEL = "-2"
}
