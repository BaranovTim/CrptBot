/// Trades you entered, logged by hand.
///
/// WHAT THIS IS NOT
///     It is not order placement, and nothing here can become order
///     placement. This app holds no exchange key, signs nothing and moves no
///     money — the Profile screen says so in as many words, and this feature
///     must not quietly make that untrue. What it does is remember a trade
///     you already entered somewhere else, so the app can show you how it is
///     doing against the same price feed it uses for everything else.
///
///     Every surface that writes to this store says "did you enter this
///     trade?" rather than "buy". The distinction is the whole point.
///
/// WHY IT LIVES ON THE DEVICE
///     Same argument as `watchlist.dart` and `muted.dart`: the API is
///     deliberately read-only apart from accounts, billing, training and push
///     topics, and `test_writes_touch_accounts_billing_and_training_and_nothing_else`
///     is what holds that line. Adding a fifth write family for this would
///     put a record of someone's open positions on a server that currently
///     holds nothing worth stealing.
///
///     THE COST OF THAT CHOICE, STATED PLAINLY: entries do not sync between
///     your phones, and a reinstall loses them. For a trade journal that is a
///     real limitation rather than a theoretical one, and it is the first
///     thing to revisit if this feature turns out to matter.
///
/// PRICES ARE NEVER GUESSED
///     An entry with no price is not stored, and profit is null rather than
///     zero whenever the current price is unknown. A journal that invents a
///     number is worse than one that admits it does not have it — see
///     `LevelsPanel.money`, which makes the same argument about a stop loss
///     of "$0.00".
library;

import 'dart:convert';
import 'dart:math';

import 'package:flutter/foundation.dart' show debugPrint;
import 'package:shared_preferences/shared_preferences.dart';

/// One logged position.
class TradeEntry {
  TradeEntry({
    required this.id,
    required this.symbol,
    required this.side,
    required this.size,
    required this.entryPrice,
    required this.openedAt,
    this.takeProfit,
    this.stopLoss,
    this.closedAt,
    this.closePrice,
    this.note = '',
  });

  factory TradeEntry.create({
    required String symbol,
    required String side,
    required double size,
    required double entryPrice,
    double? takeProfit,
    double? stopLoss,
    String note = '',
  }) {
    final r = Random.secure();
    final id = List<int>.generate(8, (_) => r.nextInt(256))
        .map((x) => x.toRadixString(16).padLeft(2, '0'))
        .join();
    return TradeEntry(
      id: id,
      symbol: symbol.toUpperCase(),
      side: side == 'SHORT' ? 'SHORT' : 'LONG',
      size: size,
      entryPrice: entryPrice,
      openedAt: DateTime.now().toUtc(),
      takeProfit: takeProfit,
      stopLoss: stopLoss,
      note: note,
    );
  }

  factory TradeEntry.fromJson(Map<String, dynamic> j) => TradeEntry(
        id: j['id'] as String,
        symbol: j['symbol'] as String,
        side: j['side'] as String? ?? 'LONG',
        size: (j['size'] as num).toDouble(),
        entryPrice: (j['entry_price'] as num).toDouble(),
        openedAt: DateTime.parse(j['opened_at'] as String),
        takeProfit: (j['take_profit'] as num?)?.toDouble(),
        stopLoss: (j['stop_loss'] as num?)?.toDouble(),
        closedAt: j['closed_at'] == null
            ? null
            : DateTime.parse(j['closed_at'] as String),
        closePrice: (j['close_price'] as num?)?.toDouble(),
        note: j['note'] as String? ?? '',
      );

  final String id, symbol, side, note;
  final double size, entryPrice;
  final DateTime openedAt;
  final double? takeProfit, stopLoss;
  final DateTime? closedAt;
  final double? closePrice;

  bool get isOpen => closedAt == null;
  bool get isShort => side == 'SHORT';

  Map<String, dynamic> toJson() => {
        'id': id,
        'symbol': symbol,
        'side': side,
        'size': size,
        'entry_price': entryPrice,
        'opened_at': openedAt.toIso8601String(),
        'take_profit': takeProfit,
        'stop_loss': stopLoss,
        'closed_at': closedAt?.toIso8601String(),
        'close_price': closePrice,
        'note': note,
      };

  /// The price this position is marked against: its close for a finished
  /// trade, the live price for an open one.
  double? markPrice(double? live) => closedAt != null ? closePrice : live;

  /// Percentage move IN THE DIRECTION OF THE TRADE.
  ///
  /// A short that falls 2% is +2%, not -2%. Getting this backwards would
  /// paint a winning trade red, which is the single most confusing thing a
  /// position card can do.
  double? pnlPct(double? live) {
    final m = markPrice(live);
    if (m == null || entryPrice <= 0) return null;
    final raw = (m - entryPrice) / entryPrice * 100.0;
    return isShort ? -raw : raw;
  }

  /// Profit in quote currency. `size` is in base units, so this is the move
  /// times the amount held.
  double? pnl(double? live) {
    final m = markPrice(live);
    if (m == null) return null;
    final raw = (m - entryPrice) * size;
    return isShort ? -raw : raw;
  }

  /// How far price has travelled toward the target, 0..1, or null when no
  /// target was recorded. Used only for the progress bar.
  double? towardTarget(double? live) {
    final m = markPrice(live);
    final tp = takeProfit;
    if (m == null || tp == null) return null;
    final span = (tp - entryPrice).abs();
    if (span <= 0) return null;
    final moved = isShort ? entryPrice - m : m - entryPrice;
    return (moved / span).clamp(0.0, 1.0);
  }

  TradeEntry closedAtPrice(double price) => TradeEntry(
        id: id,
        symbol: symbol,
        side: side,
        size: size,
        entryPrice: entryPrice,
        openedAt: openedAt,
        takeProfit: takeProfit,
        stopLoss: stopLoss,
        closedAt: DateTime.now().toUtc(),
        closePrice: price,
        note: note,
      );
}

class Trades {
  Trades._();
  static final Trades instance = Trades._();

  static const _key = 'trades.entries.v1';

  /// The balance the quick-percent buttons work from.
  ///
  /// SET BY YOU, never discovered. The design this came from showed
  /// "Avail: 1.42 BTC" above those buttons, which would require reading your
  /// exchange account — this app has no key and is never going to have one.
  /// Showing a number it cannot know would be a lie in the one place where
  /// being wrong costs money, so the buttons are driven by a figure you
  /// enter, and they stay disabled until you do.
  static const _balanceKey = 'trades.balance.v1';

  List<TradeEntry>? _cache;

  Future<List<TradeEntry>> load() async {
    if (_cache != null) return _cache!;
    try {
      final prefs = await SharedPreferences.getInstance();
      final raw = prefs.getString(_key);
      if (raw == null || raw.isEmpty) return _cache = <TradeEntry>[];
      final list = (json.decode(raw) as List)
          .map((e) => TradeEntry.fromJson(e as Map<String, dynamic>))
          .toList();
      return _cache = list;
    } catch (e) {
      // A corrupt store must not take the app down, and must not silently
      // look like "you have no trades" forever — it is logged and the next
      // write rebuilds it.
      debugPrint('[trades] could not read store: $e');
      return _cache = <TradeEntry>[];
    }
  }

  Future<List<TradeEntry>> forSymbol(String symbol, {bool openOnly = true}) async {
    final sym = symbol.toUpperCase();
    final all = await load();
    return all
        .where((t) => t.symbol == sym && (!openOnly || t.isOpen))
        .toList()
      ..sort((a, b) => b.openedAt.compareTo(a.openedAt));
  }

  Future<void> add(TradeEntry t) async {
    final all = List<TradeEntry>.from(await load())..add(t);
    await _save(all);
  }

  Future<void> close(String id, double price) async {
    final all = List<TradeEntry>.from(await load());
    final i = all.indexWhere((t) => t.id == id);
    if (i < 0) return;
    all[i] = all[i].closedAtPrice(price);
    await _save(all);
  }

  Future<void> remove(String id) async {
    final all = List<TradeEntry>.from(await load())
      ..removeWhere((t) => t.id == id);
    await _save(all);
  }

  Future<void> _save(List<TradeEntry> all) async {
    _cache = all;
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString(
          _key, json.encode(all.map((t) => t.toJson()).toList()));
    } catch (e) {
      debugPrint('[trades] could not save: $e');
    }
  }

  Future<double?> balance() async {
    try {
      final v = (await SharedPreferences.getInstance()).getDouble(_balanceKey);
      return (v == null || v <= 0) ? null : v;
    } catch (_) {
      return null;
    }
  }

  Future<void> saveBalance(double v) async {
    try {
      await (await SharedPreferences.getInstance())
          .setDouble(_balanceKey, v);
    } catch (_) {}
  }
}
