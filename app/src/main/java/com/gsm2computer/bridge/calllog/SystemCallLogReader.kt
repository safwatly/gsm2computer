package com.gsm2computer.bridge.calllog

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.database.Cursor
import android.provider.CallLog
import androidx.core.content.ContextCompat

/**
 * Reads recent rows from the system call log for the Calls tab.
 *
 * Requires [Manifest.permission.READ_CALL_LOG]. Names come from
 * [CallLog.Calls.CACHED_NAME] when the platform stored one, so this reader
 * does not request [Manifest.permission.READ_CONTACTS]. A missing name falls
 * back to the number inside [SystemCallLog.mapRow].
 *
 * This class only queries. It does not insert, update, or delete call-log rows
 * and it does not place or answer calls.
 */
class SystemCallLogReader(private val context: Context) {

    fun load(limit: Int = SystemCallLog.DEFAULT_LIMIT): SystemCallLogLoad {
        val bounded = limit.coerceIn(1, SystemCallLog.MAX_LIMIT)
        if (!hasReadPermission()) {
            return SystemCallLog.interpretQuery(false, null, null, bounded)
        }
        return try {
            SystemCallLog.interpretQuery(true, query(bounded), null, bounded)
        } catch (error: Exception) {
            SystemCallLog.interpretQuery(true, null, error, bounded)
        }
    }

    private fun hasReadPermission(): Boolean {
        return ContextCompat.checkSelfPermission(context, Manifest.permission.READ_CALL_LOG) ==
            PackageManager.PERMISSION_GRANTED
    }

    private fun query(limit: Int): List<SystemCallLogRow> {
        if (ContextCompat.checkSelfPermission(context, Manifest.permission.READ_CALL_LOG)
            != PackageManager.PERMISSION_GRANTED
        ) {
            throw SecurityException("READ_CALL_LOG")
        }
        // CallLogProvider honors this parameter. The SDK marks LIMIT_PARAM_KEY @hide;
        // its value is "limit". rowsFrom also stops after [limit] if a provider ignores it.
        val uri = CallLog.Calls.CONTENT_URI.buildUpon()
            .appendQueryParameter(CALL_LOG_LIMIT_PARAM, limit.toString())
            .build()
        val projection = arrayOf(
            CallLog.Calls.NUMBER,
            CallLog.Calls.CACHED_NAME,
            CallLog.Calls.TYPE,
            CallLog.Calls.DATE,
            CallLog.Calls.DURATION,
            CallLog.Calls.NUMBER_PRESENTATION,
        )
        val cursor = context.contentResolver.query(
            uri,
            projection,
            null,
            null,
            CallLog.Calls.DEFAULT_SORT_ORDER,
        ) ?: throw IllegalStateException(SystemCallLog.GENERIC_READ_FAILURE)
        try {
            return SystemCallLog.rowsFrom(CursorColumns(cursor), limit)
        } finally {
            cursor.close()
        }
    }

    private class CursorColumns(private val cursor: Cursor) : CallLogColumns {
        private val indexes = HashMap<String, Int>()

        private fun index(column: String): Int {
            return indexes.getOrPut(column) { cursor.getColumnIndex(column) }
        }

        override fun moveToNext(): Boolean = cursor.moveToNext()

        override fun isNull(column: String): Boolean {
            val i = index(column)
            return i < 0 || cursor.isNull(i)
        }

        override fun getString(column: String): String? {
            val i = index(column)
            if (i < 0 || cursor.isNull(i)) return null
            return cursor.getString(i)
        }

        override fun getInt(column: String): Int {
            val i = index(column)
            if (i < 0 || cursor.isNull(i)) return 0
            return cursor.getInt(i)
        }

        override fun getLong(column: String): Long {
            val i = index(column)
            if (i < 0 || cursor.isNull(i)) return 0L
            return cursor.getLong(i)
        }
    }

    private companion object {
        const val CALL_LOG_LIMIT_PARAM = "limit"
    }
}
