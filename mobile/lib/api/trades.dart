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

import 'package:flutter/foundation.dart'
    show ChangeNotifier, debugPrint, visibleForTesting;
import 'package:shared_preferences/shared_preferences.dart';

import 'dart:async';

import 'client.dart';
import 'format.dart';
import 'notifications.dart';

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
    this.closedBy = '',
    this.note = '',
    this.highSince,
    this.lowSince,
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
        closedBy: j['closed_by'] as String? ?? '',
        note: j['note'] as String? ?? '',
        highSince: (j['high_since'] as num?)?.toDouble(),
        lowSince: (j['low_since'] as num?)?.toDouble(),
      );

  final String id, symbol, side, note;

  /// How it ended: '' while open or closed by hand, 'take_profit' or
  /// 'stop_loss' when the app settled it against a level you set.
  ///
  /// Recorded rather than inferred, because the two are NOT the same thing.
  /// A trade closed at a price that happens to equal your take profit might
  /// have been closed by you at that price; the journal should not guess.
  final String closedBy;

  bool get autoClosed => closedBy == 'take_profit' || closedBy == 'stop_loss';
  final double size, entryPrice;
  final DateTime openedAt;
  final double? takeProfit, stopLoss;
  final DateTime? closedAt;
  final double? closePrice;

  /// The furthest price has been, either way, since the entry.
  ///
  /// Two sources feed these and they are merged, never replaced: the
  /// server's high/low over the 1m bars since entry (which covers the hours
  /// the app was closed), and the live ticks while a screen is open (which
  /// extend it in real time). Persisted, so the bar is right the instant
  /// Profile opens rather than after the first round trip.
  final double? highSince, lowSince;

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
        'closed_by': closedBy,
        'note': note,
        'high_since': highSince,
        'low_since': lowSince,
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

  /// Where price sits between the stop and the target.
  ///
  ///     -1 ........ 0 ........ +1
  ///    stop      entry      target
  ///
  /// Each side is scaled to ITS OWN barrier, so the two ends of the bar are
  /// the two levels you actually set, whatever their distances -- a stop
  /// 1% away and a target 3% away still put the stop at the left edge and
  /// the target at the right. The sign says which side of the entry price
  /// is on, which is what the bar colours by.
  ///
  /// `towardTarget` above answers a narrower question -- "how far to the
  /// target" -- and reports 0 for every adverse move, so a trade sitting a
  /// hair above its stop and one sitting exactly at entry looked the same.
  /// That is the bar this replaces.
  ///
  /// With only one level set, the other side borrows its span, so an
  /// adverse move on a trade with no stop still shows as movement rather
  /// than as nothing. With neither, there is nothing to draw against: null.
  double? barrierPosition(double? live) => _positionOf(markPrice(live));

  /// How far price has EVER got toward the target, 0..1, or null with no
  /// levels. The lighter fill beyond the solid one: "it was at 90%, it is
  /// at 85% now".
  double? get bestPosition {
    return _positionOf(isShort ? lowSince : highSince)?.clamp(0.0, 1.0);
  }

  /// How far it has ever got toward the stop, 0..1. Same idea, other side.
  double? get worstPosition {
    final p = _positionOf(isShort ? highSince : lowSince);
    return p == null ? null : (-p).clamp(0.0, 1.0);
  }

  double? _positionOf(double? m) {
    if (m == null) return null;
    final tp = takeProfit, sl = stopLoss;
    if (tp == null && sl == null) return null;
    final spanTp = tp == null ? null : (tp - entryPrice).abs();
    final spanSl = sl == null ? null : (sl - entryPrice).abs();
    // positive = toward the target, for either side of the trade
    final favourable = isShort ? entryPrice - m : m - entryPrice;
    if (favourable >= 0) {
      final span = spanTp ?? spanSl!;
      return span <= 0 ? 0.0 : (favourable / span).clamp(0.0, 1.0);
    }
    final span = spanSl ?? spanTp!;
    return span <= 0 ? 0.0 : -((-favourable) / span).clamp(0.0, 1.0);
  }

  TradeEntry closedAtPrice(double price, {String by = ''}) => TradeEntry(
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
        closedBy: by,
        note: note,
        highSince: highSince,
        lowSince: lowSince,
      );

  /// This entry with the extremes widened to include `high`/`low`.
  ///
  /// Returns THIS object when nothing widened, so a caller can tell by
  /// identity whether anything changed and skip the write. On a quiet
  /// tick nothing has -- which is most ticks.
  TradeEntry withExtremes({double? high, double? low}) {
    // THE ENTRY IS THE BASELINE. Price was at `entryPrice` when the trade
    // opened, so the range since entry always contains it. Starting from
    // the first tick instead made that tick both extremes at once, and a
    // move back toward the entry then counted as a new low.
    final h = _wider(highSince ?? entryPrice, high, (a, b) => a > b);
    final l = _wider(lowSince ?? entryPrice, low, (a, b) => a < b);
    if (h == highSince && l == lowSince) return this;
    return TradeEntry(
      id: id, symbol: symbol, side: side, size: size, entryPrice: entryPrice,
      openedAt: openedAt, takeProfit: takeProfit, stopLoss: stopLoss,
      closedAt: closedAt, closePrice: closePrice, closedBy: closedBy,
      note: note, highSince: h, lowSince: l,
    );
  }

  static double _wider(double have, double? seen,
      bool Function(double, double) beats) {
    if (seen == null || !seen.isFinite || seen <= 0) return have;
    return beats(seen, have) ? seen : have;
  }
}

/// What a price range did to a trade's levels. Null when nothing was hit.
///
/// PURE, AND SEPARATELY TESTED, because two things here are easy to get
/// backwards and both corrupt a journal silently:
///
///   THE DIRECTION.  A long is stopped out by the LOW and takes profit on
///   the HIGH. A short is the mirror image. Applying a long's rule to a
///   short would close winners as losses and losses as winners.
///
///   THE TIE.  When one bar's range spans both levels, OHLC cannot say which
///   came first — the data simply does not contain the answer. `monitor.py`
///   resolves that ambiguity as a LOSS and says so in as many words, and
///   this agrees with it deliberately: a journal that resolved ties in your
///   favour would flatter every statistic built on top of it.
String? levelHitBy(TradeEntry t, {double? high, double? low}) {
  if (!t.isOpen || high == null || low == null) return null;
  final tp = t.takeProfit, sl = t.stopLoss;
  final hitTp = tp != null && (t.isShort ? low <= tp : high >= tp);
  final hitSl = sl != null && (t.isShort ? high >= sl : low <= sl);
  if (hitSl) return 'stop_loss';        // the tie goes here, on purpose
  if (hitTp) return 'take_profit';
  return null;
}

class Trades extends ChangeNotifier {
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

  /// Set when the last read FAILED, as opposed to finding nothing.
  ///
  /// THE DATA-LOSS BUG THIS EXISTS TO KILL
  ///     `load()` used to swallow any read error and cache an empty list,
  ///     with a comment saying the next write would rebuild it. The next
  ///     write did not rebuild it — it PERSISTED it. One transient failure
  ///     became `[]` in memory, the next `_save` wrote `[]` to disk, and
  ///     every logged trade was gone for good. `settle()` runs on every
  ///     dashboard and profile load, so a write was never far away.
  ///
  ///     "Read failed" and "you have no trades" look identical in an empty
  ///     list and must never be treated the same, because one of them is
  ///     safe to overwrite and the other is not.
  bool _readFailed = false;

  /// Where the previous contents go before every write.
  ///
  /// Cheap insurance: a few hundred bytes against losing a trade journal.
  static const _backupKey = 'trades.entries.backup.v1';

  Future<List<TradeEntry>> load() async {
    if (_cache != null) return _cache!;
    try {
      final prefs = await SharedPreferences.getInstance();
      var raw = prefs.getString(_key);
      // `[]` counts as empty here, not as "you have no trades". An emptied
      // live key IS the shape the old data-loss bug left behind, so it is
      // exactly the case the backup exists to answer.
      if (raw == null || raw.isEmpty || raw.trim() == '[]') {
        // Nothing under the live key. Before believing that, check the
        // backup: if a previous version of this bug emptied the store, the
        // trades are still sitting there.
        final backup = prefs.getString(_backupKey);
        if (backup != null && backup.isNotEmpty && backup != '[]') {
          debugPrint('[trades] live store empty, recovering from backup');
          raw = backup;
        } else {
          _readFailed = false;
          return _cache = <TradeEntry>[];
        }
      }
      final decoded = json.decode(raw) as List;
      final list = <TradeEntry>[];
      var skipped = 0;
      for (final e in decoded) {
        try {
          list.add(TradeEntry.fromJson(e as Map<String, dynamic>));
        } catch (_) {
          // ONE BAD ENTRY IS NOT A BAD STORE. Mapping over the whole list
          // threw on the first unreadable element and lost every good one
          // behind it.
          skipped++;
        }
      }
      if (skipped > 0) debugPrint('[trades] skipped $skipped unreadable entr(y/ies)');
      _readFailed = false;
      return _cache = list;
    } catch (e) {
      // Do NOT cache. A failed read leaves the store untouched and lets the
      // next call try again, and `_save` refuses to write over a store it
      // could not read.
      debugPrint('[trades] could not read store: $e');
      _readFailed = true;
      return <TradeEntry>[];
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

  /// Close any open trade whose take profit or stop loss has been touched.
  ///
  /// Returns what it closed, so a caller can tell you rather than changing
  /// the list under you silently.
  ///
  /// THE PRICE RECORDED IS THE LEVEL, NOT A FILL. This app never saw an
  /// order; it is stating "your stop was reached", which is the honest claim
  /// the data supports. Real slippage and gaps mean your actual exit
  /// differed, so the entry is tagged `closedBy` and the UI labels it — and
  /// the close price stays editable by closing it yourself first.
  ///
  /// ASKS THE SERVER FOR THE RANGE SINCE THE ENTRY rather than comparing the
  /// current price. A stop hit at 3am that retraced by morning still took
  /// the trade out, and a current-price check would miss every one of those.
  /// Close because price touched the level, and say so. ONCE.
  ///
  /// Both automatic paths end here -- `settle`, which asks the server for
  /// the high/low since the entry, and `checkLive`, which compares against
  /// the websocket price as it arrives. They can both see the same touch,
  /// seconds apart; re-reading the log and refusing to close what is already
  /// closed is what keeps that to one close and one notification, whichever
  /// path got there first.
  ///
  /// Manual closes do not come through here on purpose: you did that
  /// yourself and do not need telling.
  Future<TradeEntry?> autoClose(String id, String hit) async {
    final all = List<TradeEntry>.from(await load());
    final i = all.indexWhere((t) => t.id == id);
    if (i < 0 || !all[i].isOpen) return null;
    final t = all[i];
    final level = hit == 'take_profit' ? t.takeProfit : t.stopLoss;
    if (level == null) return null;
    final closed = t.closedAtPrice(level, by: hit);
    all[i] = closed;
    await _save(all);
    unawaited(Notifications.instance.showTradeClosed(
      id: t.id,
      symbol: t.symbol,
      takeProfit: hit == 'take_profit',
      level: priceText(level),
      pnl: '${(closed.pnlPct(null) ?? 0) >= 0 ? '+' : ''}'
          '${(closed.pnlPct(null) ?? 0).toStringAsFixed(2)}%  '
          '${priceText(closed.pnl(null))}',
    ));
    return closed;
  }

  /// Every open entry against a live price. Immediate, cheap, idempotent.
  ///
  /// THIS IS WHAT MAKES A CLOSE HAPPEN WHILE YOU WATCH. `settle` runs on
  /// screen load and on the background poll; between those a position sat
  /// open on a screen whose live price had visibly crossed the level, and
  /// if price then came back the level was never seen to be hit at all.
  /// The socket delivers a price several times a second; comparing each one
  /// against the open entries is a few dozen comparisons. Wired into every
  /// screen that has a socket, because whichever screen is open is the one
  /// that has to notice.
  ///
  /// A single price is passed as both `high` and `low` -- a touch is a
  /// touch. Nothing at or below zero is believed: a reconnecting socket can
  /// emit one, and closing a real position on it would be permanent.
  Future<({List<TradeEntry> settled, bool extremesMoved})> checkLive(
      Map<String, double> prices) async {
    if (prices.isEmpty) {
      return (settled: const <TradeEntry>[], extremesMoved: false);
    }
    double? tick(TradeEntry t) {
      final p = prices[t.symbol];
      return p == null || !p.isFinite || p <= 0 ? null : p;
    }

    // PASS ONE: widen the extremes, in memory only, and commit once.
    //
    // A new high or low is what the lighter band on the bar draws; it is
    // not worth a disk write per tick. The cache is what `load()` hands
    // out, so the screen sees it on its next read, and the next real save
    // carries it to disk. A restart before then loses nothing: the server's
    // 1m bars have the same extreme.
    var moved = false;
    final all = List<TradeEntry>.from(await load());
    for (var i = 0; i < all.length; i++) {
      final t = all[i];
      if (!t.isOpen) continue;
      final p = tick(t);
      if (p == null) continue;
      final w = t.withExtremes(high: p, low: p);
      if (!identical(w, t)) {
        all[i] = w;
        moved = true;
      }
    }
    if (moved) _cache = all;

    // PASS TWO: the closes, each through `autoClose`, which re-reads the
    // log for itself. NOTHING FROM PASS ONE IS WRITTEN BACK AFTER THIS
    // POINT. The first version kept `all` across the closes and, on the
    // second entry, wrote that list -- with the first entry still open in
    // it -- over the cache. Two entries hitting on one tick lost a close.
    final settled = <TradeEntry>[];
    for (final t in all) {
      if (!t.isOpen) continue;
      final p = tick(t);
      if (p == null) continue;
      final hit = levelHitBy(t, high: p, low: p);
      if (hit == null) continue;
      final closed = await autoClose(t.id, hit);
      if (closed != null) settled.add(closed);
    }
    return (settled: settled, extremesMoved: moved);
  }

  Future<List<TradeEntry>> settle(ApiClient client) async {
    final open = (await load())
        .where((t) => t.isOpen && (t.takeProfit != null || t.stopLoss != null))
        .toList();
    if (open.isEmpty) return const [];

    // IN PARALLEL, not one after another.
    //
    // This was a sequential loop awaiting one round trip per open position,
    // and the dashboard awaited the whole thing before it could draw a
    // position. Ten open trades on a slow connection was ten times the
    // latency of one, in the path of a screen someone is waiting on.
    // The requests are independent, so they go together.
    final ranges = await Future.wait(
      open.map((t) async {
        try {
          return MapEntry(t, await client.priceRange(t.symbol,
              since: t.openedAt));
        } catch (e) {
          // Offline, or a pair the server does not carry. Leaving the trade
          // open is the safe failure: the next pass settles it.
          debugPrint('[trades] could not settle ${t.symbol}: $e');
          return MapEntry(t, <String, dynamic>{});
        }
      }),
    );

    // THE RANGE IS WORTH KEEPING whether or not a level was hit: it is the
    // furthest price has been since the entry, which the bar draws as the
    // lighter fill. Merged into the stored entries and written once.
    var all = List<TradeEntry>.from(await load());
    var widened = false;
    for (final e in ranges) {
      final hi = (e.value['high'] as num?)?.toDouble();
      final lo = (e.value['low'] as num?)?.toDouble();
      if (hi == null || lo == null) continue;
      final i = all.indexWhere((t) => t.id == e.key.id);
      if (i < 0) continue;
      final w = all[i].withExtremes(high: hi, low: lo);
      if (!identical(w, all[i])) {
        all[i] = w;
        widened = true;
      }
    }
    if (widened) await _save(all);

    final settled = <TradeEntry>[];
    for (final e in ranges) {
      final hit = levelHitBy(e.key,
          high: (e.value['high'] as num?)?.toDouble(),
          low: (e.value['low'] as num?)?.toDouble());
      if (hit == null) continue;
      final closed = await autoClose(e.key.id, hit);
      if (closed != null) settled.add(closed);
    }
    return settled;
  }

  Future<void> close(String id, double price, {String by = ''}) async {
    final all = List<TradeEntry>.from(await load());
    final i = all.indexWhere((t) => t.id == id);
    if (i < 0) return;
    all[i] = all[i].closedAtPrice(price, by: by);
    await _save(all);
  }

  Future<void> remove(String id) async {
    final all = List<TradeEntry>.from(await load())
      ..removeWhere((t) => t.id == id);
    await _save(all);
  }

  Future<void> _save(List<TradeEntry> all) async {
    if (_readFailed) {
      // The list handed to us was built on a read that failed, so it is not
      // a picture of your trades — it is a picture of a storage error.
      // Writing it would make the error permanent.
      debugPrint('[trades] refusing to save over a store that would not read');
      return;
    }
    _cache = all;
    try {
      final prefs = await SharedPreferences.getInstance();
      final encoded = json.encode(all.map((t) => t.toJson()).toList());
      // Keep what was there before this write. If a bug ever empties the
      // live key again, `load()` finds the trades here instead of nothing.
      if (all.isEmpty) {
        // AN INTENTIONAL EMPTY. `load()` treats an empty live key as a
        // reason to check the backup, so leaving one behind would resurrect
        // trades you deliberately deleted. This save only happens after a
        // successful read, so the emptiness is a decision, not a failure.
        await prefs.remove(_backupKey);
      } else {
        final previous = prefs.getString(_key);
        if (previous != null && previous.isNotEmpty && previous != '[]') {
          await prefs.setString(_backupKey, previous);
        }
      }
      await prefs.setString(_key, encoded);
    } catch (e) {
      debugPrint('[trades] could not save: $e');
    }
    // After the write, whether it succeeded or not: the in-memory list has
    // already changed, and that is what listeners read. The shell uses this
    // to re-ship the open positions to the push relay, so a signal on a
    // coin you just logged can say so on the lock screen within seconds
    // rather than at the next fifteen-minute poll.
    notifyListeners();
  }

  /// Drop the in-memory cache so the next `load()` re-reads storage.
  ///
  /// Exists for tests, which need to simulate a relaunch or a background
  /// isolate reading the same store fresh. Nothing in the app calls it.
  @visibleForTesting
  void resetForTest() {
    _cache = null;
    _readFailed = false;
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
