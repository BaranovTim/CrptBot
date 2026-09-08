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

            val cardIds = listOf(R.id.p_card1, R.id.p_card2, R.id.p_card3)
            val pairIds = listOf(R.id.p_pair1, R.id.p_pair2, R.id.p_pair3)
            val sideIds = listOf(R.id.p_side1, R.id.p_side2, R.id.p_side3)
            val stateIds = listOf(R.id.p_state1, R.id.p_state2, R.id.p_state3)
            val pnlIds = listOf(R.id.p_pnl1, R.id.p_pnl2, R.id.p_pnl3)
            val sizeIds = listOf(R.id.p_size1, R.id.p_size2, R.id.p_size3)
            val entryIds = listOf(R.id.p_entry1, R.id.p_entry2, R.id.p_entry3)
            val tpLabelIds = listOf(R.id.p_tplabel1, R.id.p_tplabel2, R.id.p_tplabel3)
            val tpIds = listOf(R.id.p_tp1, R.id.p_tp2, R.id.p_tp3)
            val slLabelIds = listOf(R.id.p_sllabel1, R.id.p_sllabel2, R.id.p_sllabel3)
            val slIds = listOf(R.id.p_sl1, R.id.p_sl2, R.id.p_sl3)
            val whenIds = listOf(R.id.p_when1, R.id.p_when2, R.id.p_when3)
            val noteIds = listOf(R.id.p_note1, R.id.p_note2, R.id.p_note3)

            for (i in cardIds.indices) {
                if (i >= cards.length()) {
                    v.setViewVisibility(cardIds[i], View.GONE)
                    continue
                }
                val t = cards.getJSONObject(i)
                v.setViewVisibility(cardIds[i], View.VISIBLE)
                v.setTextViewText(pairIds[i], t.optString("pair"))

                val short = t.optBoolean("short", false)
                v.setTextViewText(sideIds[i], if (short) "SELL / SHORT" else "BUY / LONG")
                v.setTextColor(sideIds[i],
                    if (short) Color.parseColor("#FF4B6B") else Color.parseColor("#00E297"))

                v.setTextViewText(stateIds[i], t.optString("state"))
                v.setTextColor(stateIds[i], Color.parseColor(
                    t.optString("stateColor", "#8B90A0")))

                v.setTextViewText(pnlIds[i], t.optString("pnl"))
                v.setTextColor(pnlIds[i],
                    if (t.optBoolean("up", true)) Color.parseColor("#00E297")
                    else Color.parseColor("#FF4B6B"))

                v.setTextViewText(sizeIds[i], t.optString("size"))
                v.setTextViewText(entryIds[i], t.optString("entry"))
                v.setTextViewText(tpLabelIds[i], t.optString("tpLabel", "TP TARGET"))
                v.setTextViewText(tpIds[i], t.optString("tp"))
                v.setTextViewText(slLabelIds[i], t.optString("slLabel", "STOP LOSS"))
                v.setTextViewText(slIds[i], t.optString("sl"))
                v.setTextViewText(whenIds[i], t.optString("when"))
                v.setTextViewText(noteIds[i], t.optString("note"))
            }
            manager.updateAppWidget(id, v)
        }
    }
}
