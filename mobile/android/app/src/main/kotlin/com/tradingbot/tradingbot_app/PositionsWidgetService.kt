package com.tradingbot.tradingbot_app

import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.widget.RemoteViews
import android.widget.RemoteViewsService
import es.antonborri.home_widget.HomeWidgetPlugin
import org.json.JSONArray
import org.json.JSONObject

/**
 * Feeds the scrollable trade list.
 *
 * WHY A SERVICE AND A FACTORY FOR WHAT LOOKS LIKE A LIST
 *     The launcher draws widgets in ITS process and cannot read this app's
 *     data. A `ListView` inside a widget is therefore not a list at all — it
 *     is a remote adapter: the launcher asks this service, running here, for
 *     one `RemoteViews` per row and ferries them across. That is the whole
 *     reason the first version used three fixed cards; a journal that grows
 *     is what makes the extra machinery worth it.
 *
 * READS THE SAME BLOB THE PROVIDER DOES
 *     No second source of truth. Everything that decides what a number means
 *     already happened in Dart; this only pastes strings into text views.
 */
class PositionsWidgetService : RemoteViewsService() {
    override fun onGetViewFactory(intent: Intent): RemoteViewsFactory =
        PositionsFactory(applicationContext)
}

private class PositionsFactory(private val context: Context) :
    RemoteViewsService.RemoteViewsFactory {

    private var trades: JSONArray = JSONArray()

    override fun onCreate() {}

    /// Called by the launcher whenever the provider says the data changed.
    override fun onDataSetChanged() {
        trades = try {
            HomeWidgetPlugin.getData(context)
                .getString("widget_positions", null)
                ?.let { JSONObject(it).optJSONArray("trades") } ?: JSONArray()
        } catch (e: Exception) {
            // Keep the last good list rather than emptying the widget in the
            // launcher's face.
            trades
        }
    }

    override fun onDestroy() {}
    override fun getCount(): Int = trades.length()
    override fun getViewTypeCount(): Int = 1
    override fun getItemId(position: Int): Long = position.toLong()
    override fun hasStableIds(): Boolean = true
    override fun getLoadingView(): RemoteViews? = null

    override fun getViewAt(position: Int): RemoteViews {
        val v = RemoteViews(context.packageName, R.layout.widget_positions_item)
        val t = trades.optJSONObject(position) ?: return v

        v.setTextViewText(R.id.i_pair, t.optString("pair"))

        val short = t.optBoolean("short", false)
        v.setTextViewText(R.id.i_side, if (short) "SELL / SHORT" else "BUY / LONG")
        v.setTextColor(R.id.i_side,
            if (short) Color.parseColor("#FF4B6B") else Color.parseColor("#00E297"))

        v.setTextViewText(R.id.i_state, t.optString("state"))
        v.setTextColor(R.id.i_state,
            Color.parseColor(t.optString("stateColor", "#8B90A0")))

        v.setTextViewText(R.id.i_pnl, t.optString("pnl"))
        v.setTextColor(R.id.i_pnl,
            if (t.optBoolean("up", true)) Color.parseColor("#00E297")
            else Color.parseColor("#FF4B6B"))

        v.setTextViewText(R.id.i_size, t.optString("size"))
        v.setTextViewText(R.id.i_entry, t.optString("entry"))
        v.setTextViewText(R.id.i_tplabel, t.optString("tpLabel", "TP TARGET"))
        v.setTextViewText(R.id.i_tp, t.optString("tp"))
        v.setTextViewText(R.id.i_sllabel, t.optString("slLabel", "STOP LOSS"))
        v.setTextViewText(R.id.i_sl, t.optString("sl"))
        v.setTextViewText(R.id.i_when, t.optString("when"))
        v.setTextViewText(R.id.i_note, t.optString("note"))

        // A row needs a FILL-IN intent, not its own PendingIntent: the
        // template lives on the collection and each row supplies the extras.
        v.setOnClickFillInIntent(R.id.i_pair, Intent().putExtra("route", "profile"))
        return v
    }
}
