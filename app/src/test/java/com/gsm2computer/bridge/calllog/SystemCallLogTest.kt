package com.gsm2computer.bridge.calllog

import android.provider.CallLog
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.Calendar
import java.util.TimeZone

/**
 * Call-log mapping: direction, withheld numbers, contact-name fallback, ordering.
 *
 * The ContentResolver query itself stays in [SystemCallLogReader]. These tests
 * cover the pure row mapping and the permission / failure result, which is the
 * part that can run without a device.
 */
class SystemCallLogTest {

    private val utc: TimeZone = TimeZone.getTimeZone("UTC")

    @Test
    fun directionOf_mapsSystemCallTypes() {
        assertEquals(SystemCallDirection.INCOMING, SystemCallLog.directionOf(CallLog.Calls.INCOMING_TYPE))
        assertEquals(SystemCallDirection.OUTGOING, SystemCallLog.directionOf(CallLog.Calls.OUTGOING_TYPE))
        assertEquals(SystemCallDirection.MISSED, SystemCallLog.directionOf(CallLog.Calls.MISSED_TYPE))
        assertEquals(SystemCallDirection.REJECTED, SystemCallLog.directionOf(CallLog.Calls.REJECTED_TYPE))
        assertEquals(SystemCallDirection.BLOCKED, SystemCallLog.directionOf(CallLog.Calls.BLOCKED_TYPE))
        assertEquals(SystemCallDirection.VOICEMAIL, SystemCallLog.directionOf(CallLog.Calls.VOICEMAIL_TYPE))
        assertEquals(
            SystemCallDirection.ANSWERED_EXTERNALLY,
            SystemCallLog.directionOf(CallLog.Calls.ANSWERED_EXTERNALLY_TYPE),
        )
        assertEquals(SystemCallDirection.UNKNOWN, SystemCallLog.directionOf(0))
        assertEquals(SystemCallDirection.UNKNOWN, SystemCallLog.directionOf(99))
    }

    @Test
    fun directionLabel_usesOwnerFacingWords() {
        assertEquals("Incoming", SystemCallLog.directionLabel(SystemCallDirection.INCOMING))
        assertEquals("Outgoing", SystemCallLog.directionLabel(SystemCallDirection.OUTGOING))
        assertEquals("Missed", SystemCallLog.directionLabel(SystemCallDirection.MISSED))
        assertEquals("Rejected", SystemCallLog.directionLabel(SystemCallDirection.REJECTED))
        assertEquals("Blocked", SystemCallLog.directionLabel(SystemCallDirection.BLOCKED))
        assertEquals("Voicemail", SystemCallLog.directionLabel(SystemCallDirection.VOICEMAIL))
        assertEquals(
            "Answered elsewhere",
            SystemCallLog.directionLabel(SystemCallDirection.ANSWERED_EXTERNALLY),
        )
        assertEquals("Unknown", SystemCallLog.directionLabel(SystemCallDirection.UNKNOWN))
    }

    @Test
    fun mapRow_prefersCachedNameAndKeepsDialableNumber() {
        val entry = SystemCallLog.mapRow(
            row(
                number = "  +15551212  ",
                cachedName = "  Ada Lovelace  ",
                type = CallLog.Calls.OUTGOING_TYPE,
                durationSec = 65,
            )
        )
        assertEquals("Ada Lovelace", entry.displayTitle)
        assertEquals("Ada Lovelace", entry.contactName)
        assertEquals("+15551212", entry.number)
        assertEquals(SystemCallDirection.OUTGOING, entry.direction)
        assertTrue(entry.canCallBack)
        assertEquals(
            "+15551212 · Outgoing · 04/11 15:04 · 1:05",
            SystemCallLog.detailLine(entry, "04/11 15:04"),
        )
    }

    @Test
    fun mapRow_blankNameFallsBackToNumber() {
        val entry = SystemCallLog.mapRow(row(number = "+15551212", cachedName = "   "))
        assertEquals("+15551212", entry.displayTitle)
        assertEquals(null, entry.contactName)
        assertTrue(entry.canCallBack)
        assertEquals(
            "Incoming · 04/11 15:04 · 1:05",
            SystemCallLog.detailLine(entry, "04/11 15:04"),
        )
    }

    @Test
    fun mapRow_hidesRestrictedNumberEvenWhenProviderStoredIt() {
        val entry = SystemCallLog.mapRow(
            row(
                number = "+15551212",
                cachedName = "Ada",
                type = CallLog.Calls.REJECTED_TYPE,
                numberPresentation = CallLog.Calls.PRESENTATION_RESTRICTED,
                durationSec = 0,
            )
        )
        assertEquals("Ada", entry.displayTitle)
        assertEquals("", entry.number)
        assertEquals(CallNumberPresentation.RESTRICTED, entry.presentation)
        assertEquals(SystemCallDirection.REJECTED, entry.direction)
        assertFalse(entry.canCallBack)
        assertEquals(
            "Rejected · 04/11 15:04 · 0:00",
            SystemCallLog.detailLine(entry, "04/11 15:04"),
        )
    }

    @Test
    fun mapRow_privateSentinelWithoutNameIsPrivateNumber() {
        val entry = SystemCallLog.mapRow(row(number = "-1", cachedName = null, durationSec = -4))
        assertEquals("", entry.number)
        assertEquals(CallNumberPresentation.RESTRICTED, entry.presentation)
        assertEquals("Private number", entry.displayTitle)
        assertEquals(0L, entry.durationSec)
        assertFalse(entry.canCallBack)
    }

    @Test
    fun mapRow_unknownSentinelWithoutNameIsUnknown() {
        val entry = SystemCallLog.mapRow(
            row(
                number = "-2",
                type = CallLog.Calls.MISSED_TYPE,
                numberPresentation = CallLog.Calls.PRESENTATION_ALLOWED,
            )
        )
        assertEquals("", entry.number)
        assertEquals(CallNumberPresentation.UNKNOWN, entry.presentation)
        assertEquals("Unknown", entry.displayTitle)
        assertEquals(SystemCallDirection.MISSED, entry.direction)
        assertFalse(entry.canCallBack)
    }

    @Test
    fun mapRow_unknownPresentationDropsNumber() {
        val entry = SystemCallLog.mapRow(
            row(
                number = "+15551212",
                numberPresentation = CallLog.Calls.PRESENTATION_UNKNOWN,
            )
        )
        assertEquals("", entry.number)
        assertEquals("Unknown", entry.displayTitle)
        assertFalse(entry.canCallBack)
    }

    @Test
    fun mapRow_payphoneKeepsNumberAndEmptyPayphoneHasLabel() {
        val withNumber = SystemCallLog.mapRow(
            row(number = "+15551212", numberPresentation = CallLog.Calls.PRESENTATION_PAYPHONE)
        )
        assertEquals("+15551212", withNumber.number)
        assertEquals("+15551212", withNumber.displayTitle)
        assertTrue(withNumber.canCallBack)

        val empty = SystemCallLog.mapRow(
            row(number = "", numberPresentation = CallLog.Calls.PRESENTATION_PAYPHONE)
        )
        assertEquals("Payphone", empty.displayTitle)
        assertFalse(empty.canCallBack)
    }

    @Test
    fun mapRows_sortsNewestFirstAndHonorsLimit() {
        val rows = listOf(
            row(number = "old", dateMillis = 1),
            row(number = "new", dateMillis = 3),
            row(number = "mid", dateMillis = 2),
        )
        assertEquals(listOf("new", "mid"), SystemCallLog.mapRows(rows, limit = 2).map { it.number })
    }

    @Test
    fun mapRows_keepsOrderWhenTimestampsMatch() {
        val rows = listOf(
            row(number = "1", dateMillis = 1000),
            row(number = "2", dateMillis = 1000),
        )
        assertEquals(listOf("1", "2"), SystemCallLog.mapRows(rows).map { it.number })
    }

    @Test
    fun mapRows_zeroLimitReturnsEmpty() {
        assertTrue(SystemCallLog.mapRows(listOf(row()), limit = 0).isEmpty())
    }

    @Test
    fun rowsFrom_readsColumnsStopsAtLimitAndDefaultsMissingPresentation() {
        val columns = FakeColumns(
            listOf(
                mapOf(
                    CallLog.Calls.NUMBER to "111",
                    CallLog.Calls.CACHED_NAME to "Ann",
                    CallLog.Calls.TYPE to CallLog.Calls.INCOMING_TYPE,
                    CallLog.Calls.DATE to 10L,
                    CallLog.Calls.DURATION to 3L,
                ),
                mapOf(
                    CallLog.Calls.NUMBER to "222",
                    CallLog.Calls.TYPE to CallLog.Calls.MISSED_TYPE,
                    CallLog.Calls.DATE to 20L,
                    CallLog.Calls.DURATION to 0L,
                    CallLog.Calls.NUMBER_PRESENTATION to CallLog.Calls.PRESENTATION_ALLOWED,
                ),
                mapOf(
                    CallLog.Calls.NUMBER to "333",
                    CallLog.Calls.TYPE to CallLog.Calls.OUTGOING_TYPE,
                    CallLog.Calls.DATE to 30L,
                    CallLog.Calls.DURATION to 1L,
                ),
            )
        )
        val rows = SystemCallLog.rowsFrom(columns, limit = 2)
        assertEquals(2, rows.size)
        assertEquals("111", rows[0].number)
        assertEquals("Ann", rows[0].cachedName)
        assertEquals(CallLog.Calls.INCOMING_TYPE, rows[0].type)
        assertEquals(10L, rows[0].dateMillis)
        assertEquals(3L, rows[0].durationSec)
        assertEquals(CallLog.Calls.PRESENTATION_ALLOWED, rows[0].numberPresentation)
        assertEquals("222", rows[1].number)
        assertEquals(null, rows[1].cachedName)
    }

    @Test
    fun rowsFrom_nullDurationAndTypeBecomeZero() {
        val columns = FakeColumns(
            listOf(
                mapOf(CallLog.Calls.NUMBER to "+1"),
            )
        )
        val row = SystemCallLog.rowsFrom(columns, limit = 5).single()
        assertEquals(0, row.type)
        assertEquals(0L, row.dateMillis)
        assertEquals(0L, row.durationSec)
        assertEquals(CallLog.Calls.PRESENTATION_ALLOWED, row.numberPresentation)
    }

    @Test
    fun formatDuration_rollsHoursAndClampsNegative() {
        assertEquals("0:00", SystemCallLog.formatDuration(0))
        assertEquals("0:59", SystemCallLog.formatDuration(59))
        assertEquals("1:00", SystemCallLog.formatDuration(60))
        assertEquals("1:01:01", SystemCallLog.formatDuration(3661))
        assertEquals("0:00", SystemCallLog.formatDuration(-5))
    }

    @Test
    fun formatWhen_omitsYearInTheSameYearAndIncludesItOtherwise() {
        val sameYear = utcMillis(2026, Calendar.NOVEMBER, 4, 15, 4)
        val now = utcMillis(2026, Calendar.NOVEMBER, 4, 18, 0)
        assertEquals("04/11 15:04", SystemCallLog.formatWhen(sameYear, now, utc))

        val lastYear = utcMillis(2025, Calendar.JANUARY, 2, 8, 9)
        assertEquals("02/01/2025 08:09", SystemCallLog.formatWhen(lastYear, now, utc))
    }

    @Test
    fun interpretQuery_permissionDeniedIgnoresRows() {
        val result = SystemCallLog.interpretQuery(
            permissionGranted = false,
            rows = listOf(row()),
            error = null,
            limit = 10,
        )
        assertEquals(SystemCallLogLoad.PermissionDenied, result)
    }

    @Test
    fun interpretQuery_securityExceptionIsPermissionDenied() {
        val result = SystemCallLog.interpretQuery(
            permissionGranted = true,
            rows = listOf(row()),
            error = SecurityException("nope"),
            limit = 10,
        )
        assertEquals(SystemCallLogLoad.PermissionDenied, result)
    }

    @Test
    fun interpretQuery_emptyLogIsReadyAndEmpty() {
        val result = SystemCallLog.interpretQuery(true, emptyList(), null, 10)
        assertTrue(result is SystemCallLogLoad.Ready)
        assertTrue((result as SystemCallLogLoad.Ready).entries.isEmpty())
    }

    @Test
    fun interpretQuery_readySortsNewestFirst() {
        val result = SystemCallLog.interpretQuery(
            permissionGranted = true,
            rows = listOf(row(number = "old", dateMillis = 1), row(number = "new", dateMillis = 2)),
            error = null,
            limit = 10,
        )
        val ready = result as SystemCallLogLoad.Ready
        assertEquals(listOf("new", "old"), ready.entries.map { it.number })
    }

    @Test
    fun interpretQuery_failureUsesFirstLineAndTruncates() {
        val blank = SystemCallLog.interpretQuery(true, null, IllegalStateException("  "), 10)
        assertEquals(
            SystemCallLogLoad.Failed(SystemCallLog.GENERIC_READ_FAILURE),
            blank,
        )

        val multiline = SystemCallLog.interpretQuery(
            true,
            null,
            IllegalStateException("disk offline\nmore"),
            10,
        )
        assertEquals(SystemCallLogLoad.Failed("disk offline"), multiline)

        val long = SystemCallLog.interpretQuery(true, null, IllegalStateException("x".repeat(200)), 10)
        val detail = (long as SystemCallLogLoad.Failed).detail
        assertEquals(160, detail.length)
        assertTrue(detail.endsWith("..."))
    }

    private fun row(
        number: String? = "+15551212",
        cachedName: String? = null,
        type: Int = CallLog.Calls.INCOMING_TYPE,
        dateMillis: Long = 1_700_000_000_000L,
        durationSec: Long = 65,
        numberPresentation: Int = CallLog.Calls.PRESENTATION_ALLOWED,
    ): SystemCallLogRow {
        return SystemCallLogRow(number, cachedName, type, dateMillis, durationSec, numberPresentation)
    }

    private fun utcMillis(year: Int, month: Int, day: Int, hour: Int, minute: Int): Long {
        return Calendar.getInstance(utc).apply {
            set(Calendar.YEAR, year)
            set(Calendar.MONTH, month)
            set(Calendar.DAY_OF_MONTH, day)
            set(Calendar.HOUR_OF_DAY, hour)
            set(Calendar.MINUTE, minute)
            set(Calendar.SECOND, 0)
            set(Calendar.MILLISECOND, 0)
        }.timeInMillis
    }

    private class FakeColumns(private val rows: List<Map<String, Any?>>) : CallLogColumns {
        private var index = -1

        override fun moveToNext(): Boolean {
            index += 1
            return index < rows.size
        }

        private fun cell(column: String): Any? = rows[index][column]

        override fun isNull(column: String): Boolean = cell(column) == null

        override fun getString(column: String): String? = cell(column) as? String

        override fun getInt(column: String): Int = (cell(column) as? Number)?.toInt() ?: 0

        override fun getLong(column: String): Long = (cell(column) as? Number)?.toLong() ?: 0L
    }
}
