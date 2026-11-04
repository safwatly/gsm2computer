package com.gsm2computer.bridge.ui

import android.Manifest
import android.content.Intent
import android.graphics.drawable.GradientDrawable
import android.graphics.drawable.RippleDrawable
import android.net.Uri
import android.os.Bundle
import android.provider.Settings
import android.text.TextUtils
import android.view.Gravity
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.activity.result.contract.ActivityResultContracts
import androidx.fragment.app.Fragment
import com.gsm2computer.bridge.R
import com.gsm2computer.bridge.calllog.SystemCallDirection
import com.gsm2computer.bridge.calllog.SystemCallLog
import com.gsm2computer.bridge.calllog.SystemCallLogEntry
import com.gsm2computer.bridge.calllog.SystemCallLogLoad
import com.gsm2computer.bridge.calllog.SystemCallLogReader
import java.util.TimeZone

/**
 * Calls tab. Lists the phone's system call log (newest first): number, contact
 * name when the call log has one, direction, date, and duration.
 *
 * Tapping a row with a dialable number only pre-fills the existing dialer. It
 * does not place, answer, or otherwise touch the live call.
 *
 * [refreshCallLog] is the hook MainActivity already calls when this tab is shown.
 */
class CallsFragment : Fragment() {

    private lateinit var callLogContainer: LinearLayout
    private lateinit var callLogScroll: ScrollView
    private lateinit var callLogMessage: LinearLayout
    private lateinit var tvCallsMessage: TextView
    private lateinit var tvCallsDetail: TextView
    private lateinit var btnCallLogGrant: TextView

    private var loadGeneration = 0
    private var askedCallLogPermission = false

    private val requestCallLogPermission = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) {
        refreshCallLog()
    }

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View = inflater.inflate(R.layout.fragment_calls, container, false)

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        callLogContainer = view.findViewById(R.id.callLogContainer)
        callLogScroll = view.findViewById(R.id.callLogScroll)
        callLogMessage = view.findViewById(R.id.callLogMessage)
        tvCallsMessage = view.findViewById(R.id.tvCallsMessage)
        tvCallsDetail = view.findViewById(R.id.tvCallsDetail)
        btnCallLogGrant = view.findViewById(R.id.btnCallLogGrant)
        btnCallLogGrant.setOnClickListener { onGrantClicked() }
        refreshCallLog()
    }

    override fun onResume() {
        super.onResume()
        refreshCallLog()
    }

    fun refreshCallLog() {
        if (view == null || !isAdded) return
        if (callLogContainer.childCount == 0 && callLogMessage.visibility != View.VISIBLE) {
            showMessage(getString(R.string.phone_call_log_loading), detail = null, grant = false)
        }
        val gen = ++loadGeneration
        val appContext = requireContext().applicationContext
        val host = activity ?: return
        Thread({
            val result = SystemCallLogReader(appContext).load()
            host.runOnUiThread {
                if (!isAdded || gen != loadGeneration || view == null) return@runOnUiThread
                render(result)
            }
        }, "PhoneCallLog").start()
    }

    private fun render(result: SystemCallLogLoad) {
        when (result) {
            is SystemCallLogLoad.Ready -> {
                if (result.entries.isEmpty()) {
                    showMessage(getString(R.string.phone_call_log_empty), detail = null, grant = false)
                } else {
                    showEntries(result.entries)
                }
            }
            SystemCallLogLoad.PermissionDenied -> {
                showMessage(
                    getString(R.string.phone_call_log_denied),
                    detail = getString(R.string.phone_call_log_denied_detail),
                    grant = true,
                )
            }
            is SystemCallLogLoad.Failed -> {
                val detail = result.detail.takeIf {
                    it.isNotBlank() && it != SystemCallLog.GENERIC_READ_FAILURE
                }
                showMessage(getString(R.string.phone_call_log_error), detail, grant = false)
            }
        }
    }

    private fun showMessage(message: String, detail: String?, grant: Boolean) {
        callLogScroll.visibility = View.GONE
        callLogContainer.removeAllViews()
        callLogMessage.visibility = View.VISIBLE
        tvCallsMessage.text = message
        if (detail.isNullOrBlank()) {
            tvCallsDetail.visibility = View.GONE
        } else {
            tvCallsDetail.visibility = View.VISIBLE
            tvCallsDetail.text = detail
        }
        if (grant) {
            btnCallLogGrant.visibility = View.VISIBLE
            val openSettings = askedCallLogPermission &&
                !shouldShowRequestPermissionRationale(Manifest.permission.READ_CALL_LOG)
            btnCallLogGrant.text = getString(
                if (openSettings) R.string.phone_call_log_settings else R.string.phone_call_log_grant
            )
        } else {
            btnCallLogGrant.visibility = View.GONE
        }
    }

    private fun showEntries(entries: List<SystemCallLogEntry>) {
        callLogMessage.visibility = View.GONE
        btnCallLogGrant.visibility = View.GONE
        callLogScroll.visibility = View.VISIBLE
        val ctx = requireContext()
        val density = resources.displayMetrics.density
        val now = System.currentTimeMillis()
        val zone = TimeZone.getDefault()
        callLogContainer.removeAllViews()
        for (entry in entries) {
            val whenText = SystemCallLog.formatWhen(entry.dateMillis, now, zone)
            callLogContainer.addView(buildRow(ctx, density, entry, whenText))
        }
    }

    private fun buildRow(
        ctx: android.content.Context,
        density: Float,
        entry: SystemCallLogEntry,
        whenText: String,
    ): View {
        val row = LinearLayout(ctx).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(dp(density, 16), dp(density, 12), dp(density, 12), dp(density, 12))
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            ).apply {
                bottomMargin = dp(density, 4)
            }
            background = rowBackground(density, entry.canCallBack)
            isClickable = entry.canCallBack
            isFocusable = entry.canCallBack
            if (entry.canCallBack) {
                setOnClickListener { offerCallBack(entry.number) }
            }
        }

        val icon = ImageView(ctx).apply {
            layoutParams = LinearLayout.LayoutParams(dp(density, 20), dp(density, 20))
            setImageResource(directionIcon(entry.direction))
            setColorFilter(directionColor(entry.direction))
        }

        val textBlock = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply {
                marginStart = dp(density, 12)
            }
        }
        textBlock.addView(TextView(ctx).apply {
            text = entry.displayTitle
            textSize = 16f
            maxLines = 1
            ellipsize = TextUtils.TruncateAt.END
            setTextColor(0xFFFFFFFF.toInt())
        })
        textBlock.addView(TextView(ctx).apply {
            text = SystemCallLog.detailLine(entry, whenText)
            textSize = 13f
            maxLines = 2
            ellipsize = TextUtils.TruncateAt.END
            setTextColor(directionColor(entry.direction))
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            ).apply {
                topMargin = dp(density, 4)
            }
        })

        row.addView(icon)
        row.addView(textBlock)
        if (entry.canCallBack) {
            row.addView(callButton(ctx, density, entry.number))
        }
        return row
    }

    private fun callButton(ctx: android.content.Context, density: Float, number: String): ImageView {
        val size = dp(density, 40)
        return ImageView(ctx).apply {
            layoutParams = LinearLayout.LayoutParams(size, size).apply {
                marginStart = dp(density, 12)
            }
            setImageResource(R.drawable.ic_phone_call)
            setColorFilter(0xFF10B981.toInt())
            setPadding(dp(density, 8), dp(density, 8), dp(density, 8), dp(density, 8))
            background = roundedRipple(density, 0xFF374151.toInt(), 20f)
            isClickable = true
            isFocusable = true
            contentDescription = getString(R.string.phone_call_log_callback, number)
            setOnClickListener { offerCallBack(number) }
        }
    }

    /**
     * Copies [number] into the dial pad and shows it. The dialer still places
     * the call, so this cannot answer, hang up, or start audio on its own.
     */
    private fun offerCallBack(number: String) {
        if (number.isEmpty()) return
        val host = activity as? GatewayHost ?: return
        host.openDiallerWithNumber(number)
    }

    private fun onGrantClicked() {
        val openSettings = askedCallLogPermission &&
            !shouldShowRequestPermissionRationale(Manifest.permission.READ_CALL_LOG)
        if (openSettings) {
            val intent = Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS).apply {
                data = Uri.fromParts("package", requireContext().packageName, null)
            }
            startActivity(intent)
        } else {
            askedCallLogPermission = true
            requestCallLogPermission.launch(Manifest.permission.READ_CALL_LOG)
        }
    }

    private fun rowBackground(density: Float, clickable: Boolean): android.graphics.drawable.Drawable {
        return if (clickable) {
            roundedRipple(density, 0xFF1F2937.toInt(), 12f)
        } else {
            GradientDrawable().apply {
                setColor(0xFF1F2937.toInt())
                cornerRadius = 12f * density
            }
        }
    }

    private fun roundedRipple(density: Float, fill: Int, radiusDp: Float): RippleDrawable {
        val radius = radiusDp * density
        return RippleDrawable(
            android.content.res.ColorStateList.valueOf(0x40FFFFFF),
            GradientDrawable().apply {
                setColor(fill)
                cornerRadius = radius
            },
            GradientDrawable().apply {
                setColor(0xFFFFFFFF.toInt())
                cornerRadius = radius
            }
        )
    }

    private fun directionIcon(direction: SystemCallDirection): Int {
        return if (direction == SystemCallDirection.OUTGOING) {
            R.drawable.ic_call_outgoing
        } else {
            R.drawable.ic_call_incoming
        }
    }

    private fun directionColor(direction: SystemCallDirection): Int {
        return when (direction) {
            SystemCallDirection.INCOMING, SystemCallDirection.ANSWERED_EXTERNALLY -> 0xFF16A34A.toInt()
            SystemCallDirection.OUTGOING -> 0xFF2563EB.toInt()
            SystemCallDirection.MISSED, SystemCallDirection.REJECTED -> 0xFFDC2626.toInt()
            else -> 0xFF9CA3AF.toInt()
        }
    }

    private fun dp(density: Float, value: Int): Int = (value * density).toInt()
}
