/// What the home-screen widgets show.
///
/// HOW THIS WORKS AT ALL
///     A widget is drawn by the LAUNCHER, in another process, through
///     RemoteViews. It cannot run Flutter, cannot call the API and cannot
///     share objects with the app. So everything that decides what a number
///     MEANS is worked out here — in Dart, where it is already tested — and
///     handed over as a small JSON blob that the Kotlin side only has to
///     paste into text views.
///
/// WHAT THE WIDGETS DO NOT CLAIM
///     The designs these came from show "TOTAL BOT PORTFOLIO $48,290.45" and
///     "Synced 1m ago via Exchange API". This app holds no exchange key, so
///     it knows neither. What it does know is what you logged and what the
///     models currently say, and that is what goes on the widget — under
///     headings that describe it truthfully.
///
///     They also say WHEN they were last updated rather than "LIVE". Android
///     refreshes a widget on its own schedule with a thirty-minute floor, so
///     a widget claiming to be live would be lying most of the time.
library;

import 'dart:convert';
import 'dart:io' show Platform;

import 'package:flutter/foundation.dart' show debugPrint, kIsWeb;
import 'package:home_widget/home_widget.dart';

import 'format.dart';
import 'journal.dart';
import 'models.dart';
import 'trades.dart';

class Widgets {
  Widgets._();
  static final Widgets instance = Widgets._();

  static const _marketKey = 'widget_market';
  static const _positionsKey = 'widget_positions';
  static const _marketProvider = 'MarketWidgetProvider';
  static const _positionsProvider = 'PositionsWidgetProvider';

  bool get supported => !kIsWeb && Platform.isAndroid;

  /// Push the watchlist to the market widget.
  ///
  /// `calls` marks the pairs with a live BUY or SELL, so the widget shows the
  /// same thing the Market screen does rather than a second opinion.
  Future<void> pushMarket(List<Coin> coins, {LiveSignals? calls,
      Map<String, double> live = const {}}) async {
    if (!supported || coins.isEmpty) return;
    final byPair = <String, LiveSignal>{};
    for (final s in calls?.signals ?? const <LiveSignal>[]) {
      // The strongest call per pair. A coin calling on two timeframes is one
      // row here, and showing the weaker of the two would be the wrong one.
      final prev = byPair[s.symbol];
      if (prev == null || _rank(s.strength) < _rank(prev.strength)) {
        byPair[s.symbol] = s;
      }
    }

    final rows = <Map<String, dynamic>>[];
    for (final c in coins.take(5)) {
      final sig = byPair[c.symbol];
      final up = (c.changePct ?? 0) >= 0;
      rows.add({
        'symbol': c.short,
        'price': priceText(live[c.symbol] ?? c.price),
        'change': c.changePct == null
            ? '—'
            : '${up ? '+' : ''}${c.changePct!.toStringAsFixed(2)}%',
        'up': up,
        // The badge is the model's actual call, not a decoration. No call
        // means no badge — an empty chip that always said something would
        // stop meaning anything.
        'tag': sig == null ? '' : '${sig.action} ${sig.interval}',
        'tagColor': sig == null
            ? '#8B90A0'
            : (sig.isSell ? '#FF4B6B' : '#00E297'),
      });
    }

    await _save(_marketKey, {
      'rows': rows,
      'subtitle': 'Watchlist prices',
      'badge': _clock(),
      'footer': '${coins.length} followed'
          '${byPair.isEmpty ? '' : ' · ${byPair.length} calling'}',
    });
    await _refresh(_marketProvider);
  }

  /// Push the trade journal to the positions widget.
  Future<void> pushPositions(List<TradeEntry> all,
      {Map<String, double> prices = const {}, double? balance}) async {
    if (!supported) return;
    final stats = JournalStats.of(all, prices: prices);

    // Open first, newest within each group — the trades you can still act on
    // are the ones worth the space.
    final sorted = List<TradeEntry>.from(all)
      ..sort((a, b) {
        if (a.isOpen != b.isOpen) return a.isOpen ? -1 : 1;
        return b.openedAt.compareTo(a.openedAt);
      });

    // ALL OF THEM, not the first three. The list scrolls now — see
    // PositionsWidgetService.kt — so the limit is what a RemoteViews
    // collection will carry rather than what fits on screen. Fifty is a
    // bound, not a design: every row crosses a process boundary as its own
    // RemoteViews, and a journal of thousands would make the launcher work
    // for rows nobody scrolls to.
    final trades = <Map<String, dynamic>>[];
    for (final t in sorted.take(50)) {
      final live = prices[t.symbol];
      final pct = t.pnlPct(live);
      final abs = t.pnl(live);
      trades.add({
        'pair': '${_short(t.symbol)} / USDT',
        'short': t.isShort,
        'state': t.isOpen
            ? 'ACTIVE'
            : t.closedBy == 'take_profit'
                ? 'TP HIT'
                : t.closedBy == 'stop_loss'
                    ? 'SL HIT'
                    : 'CLOSED',
        'stateColor': t.isOpen
            ? '#00D2FF'
            : t.closedBy == 'stop_loss'
                ? '#FF4B6B'
                : '#00E297',
        'pnl': abs == null
            ? '—'
            : '${abs >= 0 ? '+' : ''}${priceText(abs)}'
                ' (${pct! >= 0 ? '+' : ''}${pct.toStringAsFixed(2)}%)',
        'up': (pct ?? 0) >= 0,
        'size': '${_trim(t.size)} ${_short(t.symbol)}',
        'entry': priceText(t.entryPrice),
        // The labels change with the trade's state, because "TP TARGET" on a
        // finished trade is a target that is no longer being aimed at.
        'tpLabel': t.isOpen ? 'TP TARGET' : 'EXITED',
        'tp': priceText(t.isOpen ? t.takeProfit : t.closePrice),
        'slLabel': t.isOpen ? 'STOP LOSS' : 'STOP WAS',
        'sl': priceText(t.stopLoss),
        'when': _when(t.openedAt),
        'note': t.isOpen ? '' : (pct == null ? '' : 'net ${pct.toStringAsFixed(2)}%'),
      });
    }

    await _save(_positionsKey, {
      'net': '${stats.net >= 0 ? '+' : ''}${priceText(stats.net)}',
      'netUp': stats.net >= 0,
      'winRate':
          stats.winRate == null ? '—' : '${stats.winRate!.toStringAsFixed(1)}%',
      'profitFactor':
          stats.profitFactor == null ? '—' : stats.profitFactor!.toStringAsFixed(2),
      // OPEN RISK is what your stops would cost if every one of them was hit,
      // as a share of the balance you entered. Null without a balance rather
      // than a made-up percentage — see `trades.dart` on why this app cannot
      // read your exchange.
      'openRisk': _openRisk(all, balance),
      'realised24h': '${stats.realisedToday >= 0 ? '+' : ''}'
          '${priceText(stats.realisedToday)}',
      'openCount': stats.open == 0
          ? ''
          : '${stats.open} open position${stats.open == 1 ? '' : 's'}',
      'trades': trades,
      'footer': 'Updated ${_clock()} · ${stats.total} logged',
    });
    await _refresh(_positionsProvider);
  }

  /// What every open stop would cost together, against your stated balance.
  static String _openRisk(List<TradeEntry> all, double? balance) {
    if (balance == null || balance <= 0) return 'set balance';
    var risk = 0.0;
    for (final t in all) {
      final sl = t.stopLoss;
      if (!t.isOpen || sl == null) continue;
      risk += (t.entryPrice - sl).abs() * t.size;
    }
    if (risk <= 0) return 'none';
    return '${(risk / balance * 100).toStringAsFixed(1)}%';
  }

  static int _rank(String s) => switch (s) {
        'strong' => 0,
        'medium' => 1,
        'small' => 2,
        _ => 3,
      };

  static String _short(String symbol) => symbol.endsWith('USDT')
      ? symbol.substring(0, symbol.length - 4)
      : symbol;

  static String _trim(double v) {
    var s = v.toStringAsFixed(8);
    if (s.contains('.')) {
      s = s.replaceFirst(RegExp(r'0+$'), '').replaceFirst(RegExp(r'\.$'), '');
    }
    return s;
  }

  static String _clock() {
    final n = DateTime.now();
    return '${n.hour.toString().padLeft(2, '0')}:'
        '${n.minute.toString().padLeft(2, '0')}';
  }

  static String _when(DateTime at) {
    final t = at.toLocal();
    final now = DateTime.now();
    final sameDay =
        t.year == now.year && t.month == now.month && t.day == now.day;
    const months = ['Jan','Feb','Mar','Apr','May','Jun',
                    'Jul','Aug','Sep','Oct','Nov','Dec'];
    final hhmm = '${t.hour.toString().padLeft(2, '0')}:'
        '${t.minute.toString().padLeft(2, '0')}';
    return sameDay
        ? 'Today, $hhmm'
        : '${months[t.month - 1]} ${t.day}, $hhmm';
  }

  Future<void> _save(String key, Map<String, dynamic> data) async {
    try {
      await HomeWidget.saveWidgetData<String>(key, json.encode(data));
    } catch (e) {
      // A widget that cannot be updated is a widget showing older numbers.
      // That is not worth an error in front of the person using the app.
      debugPrint('[widget] could not save $key: $e');
    }
  }

  Future<void> _refresh(String provider) async {
    try {
      await HomeWidget.updateWidget(
        androidName: provider,
        qualifiedAndroidName: 'com.tradingbot.tradingbot_app.$provider',
      );
    } catch (e) {
      debugPrint('[widget] could not refresh $provider: $e');
    }
  }
}
