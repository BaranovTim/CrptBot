package com.tradingbot.tradingbot_app

import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.view.View
import android.widget.RemoteViews
import es.antonborri.home_widget.HomeWidgetPlugin
import org.json.JSONArray
import org.json.JSONObject

/**
 * The home-screen widgets.
 *
 * WHY THESE READ JSON AND DRAW NOTHING THEMSELVES
 *     A widget is rendered by the LAUNCHER, in another process, through
 *     RemoteViews. It cannot run Flutter, cannot call the API, and cannot
 *     share objects with the app. So the app writes a small JSON blob when it
 *     has fresh data, and this reads it back and fills in text. Everything
 *     that decides what the numbers MEAN — direction, profit, which trade is
 *     open — happens in Dart, where it is already tested.
 *
 * WHAT THAT MEANS FOR FRESHNESS, SAID PLAINLY
 *     A widget is only as current as the last time the app or its background
 *     job ran. Android's own `updatePeriodMillis` floor is 30 minutes and it
 *     is a request, not a promise. So both widgets print when they were last
 *     updated rather than implying they are live.
 */
private const val MARKET_KEY = "widget_market"
private const val POSITIONS_KEY = "widget_positions"

private fun launchIntent(context: Context, route: String): PendingIntent {
    // Opens the app. `route` rides along so the app can land on the right
    // tab; an intent that just opened the launcher activity would drop you
    // wherever you were last, which is rarely where you tapped from.
    val intent = context.packageManager
        .getLaunchIntentForPackage(context.packageName)
        ?.apply {
            addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP)
            putExtra("route", route)
        } ?: Intent()
    return PendingIntent.getActivity(
        context, route.hashCode(), intent,
        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
    )
}

private fun stored(context: Context, key: String): JSONObject? = try {
    HomeWidgetPlugin.getData(context).getString(key, null)?.let { JSONObject(it) }
} catch (e: Exception) {
    // A malformed blob must leave the widget on its last good picture rather
    // than crashing the launcher's host process.
    null
}

class MarketWidgetProvider : AppWidgetProvider() {
    override fun onUpdate(
        context: Context, manager: AppWidgetManager, ids: IntArray
    ) {
        val data = stored(context, MARKET_KEY)
        for (id in ids) {
            val v = RemoteViews(context.packageName, R.layout.widget_market)
            v.setOnClickPendingIntent(R.id.m_root, launchIntent(context, "market"))

            val rows: JSONArray = data?.optJSONArray("rows") ?: JSONArray()
            val ids5 = listOf(
                Triple(R.id.m_row1, R.id.m_sym1, R.id.m_tag1),
                Triple(R.id.m_row2, R.id.m_sym2, R.id.m_tag2),
                Triple(R.id.m_row3, R.id.m_sym3, R.id.m_tag3),
                Triple(R.id.m_row4, R.id.m_sym4, R.id.m_tag4),
                Triple(R.id.m_row5, R.id.m_sym5, R.id.m_tag5),
            )
            val priceIds = listOf(R.id.m_price1, R.id.m_price2, R.id.m_price3,
                R.id.m_price4, R.id.m_price5)
            val chgIds = listOf(R.id.m_chg1, R.id.m_chg2, R.id.m_chg3,
                R.id.m_chg4, R.id.m_chg5)

            for (i in ids5.indices) {
                val (rowId, symId, tagId) = ids5[i]
                if (i >= rows.length()) {
                    v.setViewVisibility(rowId, View.GONE)
                    continue
                }
                val r = rows.getJSONObject(i)
                v.setViewVisibility(rowId, View.VISIBLE)
                v.setTextViewText(symId, r.optString("symbol"))
                v.setTextViewText(priceIds[i], r.optString("price"))

                val chg = r.optString("change")
                v.setTextViewText(chgIds[i], chg)
                v.setTextColor(chgIds[i],
                    if (r.optBoolean("up", true)) Color.parseColor("#00E297")
                    else Color.parseColor("#FF4B6B"))

                val tag = r.optString("tag")
                if (tag.isEmpty()) {
                    v.setViewVisibility(tagId, View.GONE)
                } else {
                    v.setViewVisibility(tagId, View.VISIBLE)
                    v.setTextViewText(tagId, tag)
                    v.setTextColor(tagId, Color.parseColor(
                        r.optString("tagColor", "#00E297")))
                }
            }
            v.setTextViewText(R.id.m_sub, data?.optString("subtitle") ?: "Watchlist prices")
            v.setTextViewText(R.id.m_foot, data?.optString("footer") ?: "Open the app to refresh")
            v.setTextViewText(R.id.m_live, data?.optString("badge") ?: "—")
            manager.updateAppWidget(id, v)
            // Re-ask the factory. Without this the launcher keeps the rows it
            // already has and the widget silently shows yesterday's trades.
            manager.notifyAppWidgetViewDataChanged(id, R.id.p_list)
        }
    }
}

class PositionsWidgetProvider : AppWidgetProvider() {
    override fun onUpdate(
        context: Context, manager: AppWidgetManager, ids: IntArray
    ) {
        val data = stored(context, POSITIONS_KEY)
        for (id in ids) {
            val v = RemoteViews(context.packageName, R.layout.widget_positions)
            v.setOnClickPendingIntent(R.id.p_root, launchIntent(context, "profile"))

            v.setTextViewText(R.id.p_net, data?.optString("net") ?: "—")
            v.setTextColor(R.id.p_net,
                if (data?.optBoolean("netUp", true) != false) Color.parseColor("#00E297")
                else Color.parseColor("#FF4B6B"))
            v.setTextViewText(R.id.p_win, data?.optString("winRate") ?: "—")
            v.setTextViewText(R.id.p_pf, data?.optString("profitFactor") ?: "—")
            v.setTextViewText(R.id.p_risk, data?.optString("openRisk") ?: "—")
            v.setTextViewText(R.id.p_24h, data?.optString("realised24h") ?: "—")
            v.setTextViewText(R.id.p_open, data?.optString("openCount") ?: "")
            v.setTextViewText(R.id.p_foot, data?.optString("footer") ?: "")

            val cards: JSONArray = data?.optJSONArray("trades") ?: JSONArray()
            v.setViewVisibility(R.id.p_empty,
                if (cards.length() == 0) View.VISIBLE else View.GONE)

            // THE SCROLLABLE LIST.
            //
            // `setRemoteAdapter` hands the launcher a service to ask for
            // rows; the template intent below is what makes a tap on any row
            // open the app. Both are set before `notifyAppWidgetViewDataChanged`,
            // which tells the launcher to re-ask the factory.
            val svc = Intent(context, PositionsWidgetService::class.java)
            v.setRemoteAdapter(R.id.p_list, svc)
            v.setPendingIntentTemplate(R.id.p_list, launchIntent(context, "profile"))
            v.setEmptyView(R.id.p_list, R.id.p_empty)

            manager.updateAppWidget(id, v)
            // Re-ask the factory. Without this the launcher keeps the rows it
            // already has and the widget silently shows yesterday's trades.
            manager.notifyAppWidgetViewDataChanged(id, R.id.p_list)
        }
    }
}
