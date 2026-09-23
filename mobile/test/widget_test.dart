/// Widget and model tests.
///
/// These check the parts that would fail silently rather than loudly: the
/// JSON contract with the Python service, and the null handling that keeps a
/// missing value from being rendered as a confident zero.
library;

import 'package:flutter_test/flutter_test.dart';
import 'package:tradingbot_app/theme/liquid_obsidian.dart';
import 'package:tradingbot_app/widgets/positions_panel.dart';
import 'package:tradingbot_app/widgets/acknowledgement.dart';
import 'package:tradingbot_app/widgets/signals_panel.dart';
import 'package:tradingbot_app/widgets/momentum_panel.dart';
import 'package:tradingbot_app/widgets/record_panel.dart';
import 'package:tradingbot_app/widgets/trade_entry.dart';
import 'package:tradingbot_app/widgets/analysis_panels.dart';
import 'package:tradingbot_app/widgets/smart_money_panel.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:tradingbot_app/api/models.dart';
import 'dart:convert';
import 'dart:io';

import 'package:tradingbot_app/api/background.dart';
import 'package:tradingbot_app/api/muted.dart';
import 'package:tradingbot_app/api/format.dart';
import 'package:tradingbot_app/api/journal.dart';
import 'package:tradingbot_app/api/trades.dart';
import 'package:tradingbot_app/api/notifications.dart';
import 'package:tradingbot_app/api/push.dart';
import 'package:tradingbot_app/api/settings.dart';
import 'package:tradingbot_app/api/watchlist.dart';
import 'package:tradingbot_app/main.dart';
import 'package:tradingbot_app/widgets/patient_loader.dart';
import 'package:tradingbot_app/api/alert_feed.dart';
import 'package:tradingbot_app/api/market_mode.dart';
import 'package:tradingbot_app/widgets/frosted_nav.dart';
import 'package:tradingbot_app/screens/dashboard_screen.dart';
import 'package:flutter/material.dart';

void main() {
  testWidgets('boots to sign-in, never straight into a signed-in session',
      (tester) async {
    // The app restores a saved API host and token before painting anything,
    // because probing the connection against the compile-time default first
    // would report "no link" for a server that is perfectly reachable.
    // Without a mock store that read throws on the test binding.
    //
    // With no stored session it must land on the sign-in screen. This is the
    // one that would matter if it regressed: a build that fell through to the
    // shell would hand an unauthenticated user the paid screens, and the only
    // thing stopping them seeing data would be the server's 402.
    SharedPreferences.setMockInitialValues({});

    await tester.pumpWidget(const VanthApp());
    await tester.pump();                                  // kick off restore
    await tester.pump(const Duration(milliseconds: 50));   // let it land

    expect(find.text('Vanth'), findsOneWidget);
    expect(find.text('SIGN IN'), findsOneWidget);
    expect(find.text('Sign in to your account'), findsOneWidget);
  });

  testWidgets('the sign-in form does not let an empty password through',
      (tester) async {
    // The screen it replaced was a prop: it accepted anything, including
    // nothing, because there was no account server to ask. There is one now,
    // and a form that still waved people through would be worse than the
    // prop — it would look like it was checking.
    SharedPreferences.setMockInitialValues({});

    await tester.pumpWidget(const VanthApp());
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 50));

    await tester.tap(find.text('SIGN IN'));
    await tester.pump();

    expect(find.text('Both fields are required.'), findsOneWidget);
    expect(find.text('Vanth'), findsOneWidget);      // still on sign-in
  });

  test('absent numbers stay null and never become zero', () {
    // the service sends null for anything it does not know. rendering that
    // as 0 would claim "the level is exactly here", the opposite of "there
    // is no level" - the same trap the Python side documents at length
    final d = Dashboard.fromJson({
      'symbol': 'BTCUSDT',
      'pair': 'BTC / USDT',
      'interval': '1h',
      'stale': false,
      'price': 79000.0,
      'change_pct': null,
      'status': {
        'active': true,
        'label': 'WATCHING',
        'detail': 'reading every closed bar',
        'trades': false,
      },
      'live': null,
      'indicators': [],
      'analyses': [],
      'recommendation': {'action': 'FLAT', 'tone': 'flat', 'detail': 'x'},
      'levels': {'current': 79000.0, 'take_profit': null, 'stop_loss': null},
      'calibration_note': '',
    });
    expect(d.changePct, isNull);
    expect(d.takeProfit, isNull);
    expect(d.stopLoss, isNull);
    expect(d.live, isNull);
    expect(d.price, 79000.0);
  });

  test('a SELL puts take profit below the live price and the stop above', () {
    // THE BUG THIS PINS
    //
    // `tp_pct`/`sl_pct` are magnitudes: they say how far, never which way.
    // `liveTakeProfit` used to move take profit UP and `liveStopLoss` move
    // the stop DOWN from the live price, whatever the call was. On a SELL
    // that drew the target above the price and the stop below — both on the
    // wrong side, directly under a card reading SELL.
    //
    // The server's own `take_profit`/`stop_loss` were always right. These are
    // what the app draws whenever a websocket price is available, which on
    // crypto is essentially always, so the wrong ones were the visible ones.
    Map<String, dynamic> withLevels(Map<String, dynamic> levels) => {
          'symbol': 'BTCUSDT',
          'pair': 'BTC / USDT',
          'interval': '1h',
          'stale': false,
          'price': 100.0,
          'change_pct': null,
          'status': {
            'active': true,
            'label': 'WATCHING',
            'detail': '',
            'trades': false,
          },
          'live': null,
          'indicators': [],
          'analyses': [],
          'recommendation': {'action': 'SELL', 'tone': 'down', 'detail': 'x'},
          'levels': levels,
          'calibration_note': '',
        };

    final sell = Dashboard.fromJson(withLevels({
      'current': 100.0,
      'anchor': 100.0,
      'side': 'SHORT',
      'take_profit': 98.0,
      'stop_loss': 102.0,
      'tp_pct': 2.0,
      'sl_pct': 2.0,
      'tp_offset_pct': -2.0,
      'sl_offset_pct': 2.0,
    }));
    expect(sell.isShort, isTrue);
    expect(sell.liveTakeProfit(90.0), lessThan(90.0));
    expect(sell.liveStopLoss(90.0), greaterThan(90.0));

    final buy = Dashboard.fromJson(withLevels({
      'current': 100.0,
      'anchor': 100.0,
      'side': 'LONG',
      'take_profit': 102.0,
      'stop_loss': 98.0,
      'tp_pct': 2.0,
      'sl_pct': 2.0,
      'tp_offset_pct': 2.0,
      'sl_offset_pct': -2.0,
    }));
    expect(buy.liveTakeProfit(90.0), greaterThan(90.0));
    expect(buy.liveStopLoss(90.0), lessThan(90.0));

    // AND WITHOUT THE SIGNED FIELDS. A server that predates them still sends
    // magnitudes, and `side` is enough to put them the right way round —
    // otherwise the app would be correct only after both ends are updated.
    final old = Dashboard.fromJson(withLevels({
      'current': 100.0,
      'anchor': 100.0,
      'side': 'SHORT',
      'take_profit': 98.0,
      'stop_loss': 102.0,
      'tp_pct': 2.0,
      'sl_pct': 2.0,
    }));
    expect(old.liveTakeProfit(90.0), lessThan(90.0));
    expect(old.liveStopLoss(90.0), greaterThan(90.0));
  });

  test('a topic is long, random and never repeats', () {
    // The topic is the ONLY thing protecting the alert stream — ntfy has no
    // accounts, so anyone who knows it can read it. A short or predictable
    // one hands over which pairs you watch and what the model said.
    final seen = <String>{};
    for (var i = 0; i < 200; i++) {
      seen.add(PushDelivery.newTopic());
    }
    expect(seen.length, 200);
    for (final t in seen) {
      expect(t.length, greaterThanOrEqualTo(32));
      expect(RegExp(r'^[A-Za-z0-9_-]+$').hasMatch(t), isTrue);
    }
  });

  test('a background run is recorded even when signed out', () async {
    // WHY THIS MATTERS
    //
    // "Android throttled the job" and "the job ran, there was nothing to
    // fetch" look identical from outside the phone, and they need opposite
    // responses — one is a battery setting, the other is patience. The
    // Profile screen can only tell them apart if the run is recorded before
    // the sign-out check, not after it.
    SharedPreferences.setMockInitialValues({});
    var (last, runs) = await lastBackgroundRun();
    expect(last, isNull);
    expect(runs, 0);

    // no token, so the poll returns immediately — and must still count
    await pollOnce();
    (last, runs) = await lastBackgroundRun();
    expect(runs, 1);
    expect(last, isNotNull);
    expect(DateTime.now().difference(last!).inMinutes, lessThan(1));
  });

  test('a short that falls is a WIN, not a loss', () {
    // THE BUG THIS PINS
    //
    // Profit has to be read in the direction of the trade. A short entered
    // at 100 and marked at 90 has made 10%, and printing that as -10% in red
    // is the single most confusing thing a position card can do — it tells
    // you a winning trade is losing, on the screen you check when deciding
    // whether to get out.
    final short = TradeEntry.create(
        symbol: 'BTCUSDT', side: 'SHORT', size: 2, entryPrice: 100);
    expect(short.pnlPct(90), closeTo(10, 1e-9));
    expect(short.pnl(90), closeTo(20, 1e-9));      // 2 units x 10 move
    expect(short.pnlPct(110), closeTo(-10, 1e-9));

    final long = TradeEntry.create(
        symbol: 'BTCUSDT', side: 'LONG', size: 2, entryPrice: 100);
    expect(long.pnlPct(90), closeTo(-10, 1e-9));
    expect(long.pnl(110), closeTo(20, 1e-9));
  });

  test('an unknown price gives null profit, never zero', () {
    // "+\$0.00" reads as flat, which is a claim about the market. "—" reads
    // as "not known", which is the truth before the first websocket tick.
    final t = TradeEntry.create(
        symbol: 'BTCUSDT', side: 'LONG', size: 1, entryPrice: 100);
    expect(t.pnl(null), isNull);
    expect(t.pnlPct(null), isNull);
    expect(t.towardTarget(null), isNull);
  });

  test('a closed trade marks against its exit, not against the live price',
      () {
    // Otherwise last week's closed trade would keep moving with the market,
    // and the realised total in Profile would change every time you opened
    // the app.
    final t = TradeEntry.create(
            symbol: 'BTCUSDT', side: 'LONG', size: 1, entryPrice: 100)
        .closedAtPrice(120);
    expect(t.isOpen, isFalse);
    expect(t.pnlPct(500), closeTo(20, 1e-9));
    expect(t.markPrice(500), 120);
  });

  test('progress toward the target is direction-aware and clamped', () {
    final long = TradeEntry(
        id: 'a', symbol: 'BTCUSDT', side: 'LONG', size: 1, entryPrice: 100,
        openedAt: DateTime.now(), takeProfit: 120);
    expect(long.towardTarget(110), closeTo(0.5, 1e-9));
    expect(long.towardTarget(200), 1.0);            // clamped, never over 1
    expect(long.towardTarget(90), 0.0);             // and never negative

    final short = TradeEntry(
        id: 'b', symbol: 'BTCUSDT', side: 'SHORT', size: 1, entryPrice: 100,
        openedAt: DateTime.now(), takeProfit: 80);
    expect(short.towardTarget(90), closeTo(0.5, 1e-9));
  });

  test('position between the barriers: stop at -1, entry at 0, target at +1',
      () {
    // THE CASE THE OLD BAR COULD NOT SHOW. `towardTarget` clamped every
    // adverse move to 0, so price a hair above the stop and price sitting
    // at entry were drawn identically. The sign is the point.
    final long = TradeEntry(
        id: 'a', symbol: 'BTCUSDT', side: 'LONG', size: 1, entryPrice: 100,
        openedAt: DateTime.now(), takeProfit: 130, stopLoss: 90);
    expect(long.barrierPosition(100), 0.0);
    expect(long.barrierPosition(115), closeTo(0.5, 1e-9));   // half to TP
    expect(long.barrierPosition(95), closeTo(-0.5, 1e-9));   // half to SL
    expect(long.barrierPosition(130), 1.0);
    expect(long.barrierPosition(90), -1.0);
    expect(long.barrierPosition(500), 1.0);                  // clamped
    expect(long.barrierPosition(1), -1.0);                   // clamped
    // Each side scaled to ITS OWN level: 30 up is the whole right half,
    // 10 down is the whole left half. The same 5 dollars is 1/6 of the way
    // to the target but half the way to the stop.
    expect(long.barrierPosition(105), closeTo(1 / 6, 1e-9));
    expect(long.barrierPosition(95), closeTo(-0.5, 1e-9));

    final short = TradeEntry(
        id: 'b', symbol: 'BTCUSDT', side: 'SHORT', size: 1, entryPrice: 100,
        openedAt: DateTime.now(), takeProfit: 80, stopLoss: 110);
    expect(short.barrierPosition(90), closeTo(0.5, 1e-9));   // down = good
    expect(short.barrierPosition(105), closeTo(-0.5, 1e-9)); // up = bad
  });

  test('a missing level borrows the other side\'s span; none means no bar',
      () {
    final tpOnly = TradeEntry(
        id: 'c', symbol: 'BTCUSDT', side: 'LONG', size: 1, entryPrice: 100,
        openedAt: DateTime.now(), takeProfit: 120);
    expect(tpOnly.barrierPosition(90), closeTo(-0.5, 1e-9),
        reason: 'an adverse move with no stop must still show as movement');
    final none = TradeEntry(
        id: 'd', symbol: 'BTCUSDT', side: 'LONG', size: 1, entryPrice: 100,
        openedAt: DateTime.now());
    expect(none.barrierPosition(90), isNull);
    expect(tpOnly.barrierPosition(null), isNull);
  });

  test('a logged trade survives being written and read back', () {
    final t = TradeEntry.create(
        symbol: 'ethusdt', side: 'SHORT', size: 0.5, entryPrice: 3000,
        takeProfit: 2800, stopLoss: 3100);
    final back = TradeEntry.fromJson(t.toJson());
    expect(back.symbol, 'ETHUSDT');            // normalised on the way in
    expect(back.side, 'SHORT');
    expect(back.size, 0.5);
    expect(back.entryPrice, 3000);
    expect(back.takeProfit, 2800);
    expect(back.stopLoss, 3100);
    expect(back.isOpen, isTrue);
    expect(back.id, t.id);
  });

  test('the store keeps trades per symbol and closes them in place', () async {
    SharedPreferences.setMockInitialValues({});
    final store = Trades.instance;
    await store.add(TradeEntry.create(
        symbol: 'BTCUSDT', side: 'LONG', size: 1, entryPrice: 100));
    await store.add(TradeEntry.create(
        symbol: 'ETHUSDT', side: 'SHORT', size: 2, entryPrice: 50));

    expect((await store.forSymbol('BTCUSDT')).length, 1);
    expect((await store.forSymbol('ETHUSDT')).length, 1);
    expect((await store.forSymbol('SOLUSDT')).length, 0);

    final btc = (await store.forSymbol('BTCUSDT')).single;
    await store.close(btc.id, 130);
    // gone from the OPEN list for that pair...
    expect((await store.forSymbol('BTCUSDT')).length, 0);
    // ...but still in the full log, with its exit recorded
    final all = await store.load();
    final done = all.firstWhere((t) => t.id == btc.id);
    expect(done.isOpen, isFalse);
    expect(done.closePrice, 130);
    expect(done.pnlPct(null), closeTo(30, 1e-9));
  });

  test('a long is stopped by the low and a short by the high', () {
    // THE BUG THIS PINS
    //
    // A long takes profit on the HIGH and is stopped on the LOW; a short is
    // the mirror image. Applying a long's rule to a short closes winners as
    // losses and losses as winners — and because this rewrites the journal
    // without asking, it would be wrong silently and permanently.
    final long = TradeEntry(
        id: 'l', symbol: 'BTCUSDT', side: 'LONG', size: 1, entryPrice: 100,
        openedAt: DateTime.now(), takeProfit: 110, stopLoss: 90);
    expect(levelHitBy(long, high: 111, low: 99), 'take_profit');
    expect(levelHitBy(long, high: 105, low: 89), 'stop_loss');
    expect(levelHitBy(long, high: 105, low: 95), isNull);

    final short = TradeEntry(
        id: 's', symbol: 'BTCUSDT', side: 'SHORT', size: 1, entryPrice: 100,
        openedAt: DateTime.now(), takeProfit: 90, stopLoss: 110);
    // price FELL to 89: a short's target, not its stop
    expect(levelHitBy(short, high: 101, low: 89), 'take_profit');
    // price ROSE to 111: a short's stop
    expect(levelHitBy(short, high: 111, low: 95), 'stop_loss');
    expect(levelHitBy(short, high: 105, low: 95), isNull);
  });

  test('a bar spanning both levels resolves as the stop, like the labeller',
      () {
    // OHLC cannot say which barrier came first — the data does not contain
    // the answer. `monitor.py` resolves that tie as a LOSS and says so; this
    // agrees deliberately. A journal that broke ties in your favour would
    // flatter every statistic built on top of it.
    final long = TradeEntry(
        id: 'l', symbol: 'BTCUSDT', side: 'LONG', size: 1, entryPrice: 100,
        openedAt: DateTime.now(), takeProfit: 110, stopLoss: 90);
    expect(levelHitBy(long, high: 115, low: 85), 'stop_loss');

    final short = TradeEntry(
        id: 's', symbol: 'BTCUSDT', side: 'SHORT', size: 1, entryPrice: 100,
        openedAt: DateTime.now(), takeProfit: 90, stopLoss: 110);
    expect(levelHitBy(short, high: 115, low: 85), 'stop_loss');
  });

  test('nothing is settled without a range, or on a closed trade', () {
    final t = TradeEntry(
        id: 'x', symbol: 'BTCUSDT', side: 'LONG', size: 1, entryPrice: 100,
        openedAt: DateTime.now(), takeProfit: 110, stopLoss: 90);
    // no bar has closed since the entry: silence is not "nothing was hit"
    expect(levelHitBy(t, high: null, low: null), isNull);
    expect(levelHitBy(t, high: 115, low: null), isNull);
    // a trade with no levels can never be settled automatically
    final bare = TradeEntry.create(
        symbol: 'BTCUSDT', side: 'LONG', size: 1, entryPrice: 100);
    expect(levelHitBy(bare, high: 999, low: 1), isNull);
    // and a closed one is never reopened or re-settled
    final done = t.closedAtPrice(105);
    expect(levelHitBy(done, high: 115, low: 85), isNull);
  });

  test('an auto-closed trade records which level ended it', () {
    // "Closed at 90" does not say whether you took that price or your stop
    // did. The journal records which, rather than letting the UI guess.
    final t = TradeEntry(
        id: 'l', symbol: 'BTCUSDT', side: 'LONG', size: 2, entryPrice: 100,
        openedAt: DateTime.now(), takeProfit: 110, stopLoss: 90);
    final stopped = t.closedAtPrice(90, by: 'stop_loss');
    expect(stopped.closedBy, 'stop_loss');
    expect(stopped.autoClosed, isTrue);
    expect(stopped.pnl(null), closeTo(-20, 1e-9));   // 2 units x -10
    expect(TradeEntry.fromJson(stopped.toJson()).closedBy, 'stop_loss');

    final byHand = t.closedAtPrice(105);
    expect(byHand.autoClosed, isFalse);
    expect(byHand.closedBy, '');
  });

  test('one unreadable entry does not take the whole journal with it', () async {
    // Mapping over the decoded list threw on the first bad element and lost
    // every good one behind it.
    SharedPreferences.setMockInitialValues({
      'trades.entries.v1': '['
          '{"id":"a","symbol":"BTCUSDT","side":"LONG","size":1,'
          '"entry_price":100,"opened_at":"2026-09-01T00:00:00.000Z"},'
          '{"id":"broken","symbol":"ETHUSDT"},'
          '{"id":"c","symbol":"SOLUSDT","side":"SHORT","size":2,'
          '"entry_price":50,"opened_at":"2026-09-02T00:00:00.000Z"}'
          ']',
    });
    final store = Trades.instance;
    store.resetForTest();
    final all = await store.load();
    expect(all.length, 2);
    expect(all.map((t) => t.id), containsAll(<String>['a', 'c']));
  });

  test('a store that will not read is never written over', () async {
    // THE DATA-LOSS BUG THIS PINS
    //
    // `load()` used to cache an empty list on ANY read failure, and the next
    // write persisted it. One transient error became `[]` on disk and every
    // logged trade was gone for good — and `settle()` runs on each dashboard
    // and profile load, so a write was never far away.
    SharedPreferences.setMockInitialValues({
      'trades.entries.v1': 'this is not json at all',
    });
    final store = Trades.instance;
    store.resetForTest();

    final all = await store.load();
    expect(all, isEmpty, reason: 'a bad read reports nothing it can show');

    // ...but that emptiness must not be written back
    await store.add(TradeEntry.create(
        symbol: 'BTCUSDT', side: 'LONG', size: 1, entryPrice: 100));
    final prefs = await SharedPreferences.getInstance();
    expect(prefs.getString('trades.entries.v1'), 'this is not json at all',
        reason: 'the unreadable store was overwritten, destroying it');
  });

  test('trades survive a write, and the previous version is kept', () async {
    SharedPreferences.setMockInitialValues({});
    final store = Trades.instance;
    store.resetForTest();

    await store.add(TradeEntry.create(
        symbol: 'BTCUSDT', side: 'LONG', size: 1, entryPrice: 100));
    await store.add(TradeEntry.create(
        symbol: 'ETHUSDT', side: 'SHORT', size: 2, entryPrice: 50));
    expect((await store.load()).length, 2);

    // a fresh read of the same storage, as a new isolate or a relaunch does
    store.resetForTest();
    expect((await store.load()).length, 2);

    // and the backup holds the state before the last write
    final prefs = await SharedPreferences.getInstance();
    final backup = prefs.getString('trades.entries.backup.v1');
    expect(backup, isNotNull);
    expect((json.decode(backup!) as List).length, 1);
  });

  test('an emptied store recovers from its backup', () async {
    // Exactly the shape of the bug that lost a real journal: the live key
    // ends up as `[]` while the backup still holds the trades.
    SharedPreferences.setMockInitialValues({
      'trades.entries.v1': '[]',
      'trades.entries.backup.v1': '['
          '{"id":"a","symbol":"BTCUSDT","side":"LONG","size":1,'
          '"entry_price":100,"opened_at":"2026-09-01T00:00:00.000Z"}'
          ']',
    });
    final store = Trades.instance;
    store.resetForTest();
    final all = await store.load();
    expect(all.length, 1);
    expect(all.single.symbol, 'BTCUSDT');
  });

  test('deleting every trade stays deleted', () async {
    // The backup makes an empty store suspicious, which is right after a
    // failure and wrong after a decision. Emptying it on purpose must not
    // be undone on the next read.
    SharedPreferences.setMockInitialValues({});
    final store = Trades.instance;
    store.resetForTest();
    final t = TradeEntry.create(
        symbol: 'BTCUSDT', side: 'LONG', size: 1, entryPrice: 100);
    await store.add(t);
    await store.add(TradeEntry.create(
        symbol: 'ETHUSDT', side: 'LONG', size: 1, entryPrice: 50));
    final all = await store.load();
    for (final x in all) {
      await store.remove(x.id);
    }
    store.resetForTest();
    expect(await store.load(), isEmpty,
        reason: 'deleted trades came back from the backup');
  });

  test('win rate and profit factor count only decided trades', () {
    TradeEntry closed(String sym, double entry, double exit, double size) =>
        TradeEntry.create(
                symbol: sym, side: 'LONG', size: size, entryPrice: entry)
            .closedAtPrice(exit);

    final s = JournalStats.of([
      closed('BTCUSDT', 100, 120, 1),   // +20
      closed('ETHUSDT', 100, 110, 1),   // +10
      closed('SOLUSDT', 100, 85, 1),    // -15
      closed('XRPUSDT', 100, 100, 1),   // flat — neither a win nor a loss
      TradeEntry.create(
          symbol: 'ADAUSDT', side: 'LONG', size: 1, entryPrice: 100),
    ], prices: {'ADAUSDT': 150});

    expect(s.total, 5);
    expect(s.open, 1);
    expect(s.closed, 4);
    expect(s.wins, 2);
    expect(s.losses, 1);
    // A break-even trade is neither, and counting it as a win is the easiest
    // possible way to flatter a record.
    expect(s.winRate, closeTo(200 / 3, 1e-9));
    expect(s.profitFactor, closeTo(30 / 15, 1e-9));
    expect(s.realised, closeTo(15, 1e-9));
    // the open one is marked live, and stays OUT of the win rate
    expect(s.unrealised, closeTo(50, 1e-9));
    expect(s.net, closeTo(65, 1e-9));
  });

  test('an undecided record reports nothing rather than zero', () {
    // "No trades yet" and "you lose every time" are very different claims and
    // a 0% would print the second when it means the first.
    final empty = JournalStats.of(const []);
    expect(empty.winRate, isNull);
    expect(empty.profitFactor, isNull);

    // an unbeaten record has no finite profit factor either
    final unbeaten = JournalStats.of([
      TradeEntry.create(
              symbol: 'BTCUSDT', side: 'LONG', size: 1, entryPrice: 100)
          .closedAtPrice(150),
    ]);
    expect(unbeaten.winRate, 100);
    expect(unbeaten.profitFactor, isNull);
  });

  test('the daily figure counts today only, and closed trades only', () {
    // THE REAL CLOCK, NOT A HARDCODED DATE.
    //
    // A previous version pinned `now` to a fixed day while `closedAtPrice`
    // stamps the actual time. It passed for one day and failed at the next
    // midnight — a test that fails on a calendar boundary teaches people to
    // ignore a red suite, which is worse than not having it.
    final now = DateTime.now().toUtc();
    final todayLoss = TradeEntry.create(
            symbol: 'BTCUSDT', side: 'LONG', size: 1, entryPrice: 100)
        .closedAtPrice(90);
    final s = JournalStats.of([todayLoss], now: now);
    expect(s.realisedToday, closeTo(-10, 1e-9));

    // and one closed yesterday is NOT today's, however recently it was made
    final yesterday = TradeEntry(
        id: 'y', symbol: 'BTCUSDT', side: 'LONG', size: 1, entryPrice: 100,
        openedAt: now.subtract(const Duration(days: 2)),
        closedAt: now.subtract(const Duration(days: 1)), closePrice: 80);
    final s3 = JournalStats.of([yesterday], now: now);
    expect(s3.realised, closeTo(-20, 1e-9));
    expect(s3.realisedToday, 0);

    // an OPEN position deep in drawdown is not a loss you have taken
    final floating = TradeEntry.create(
        symbol: 'ETHUSDT', side: 'LONG', size: 1, entryPrice: 100);
    final s2 = JournalStats.of([floating],
        prices: {'ETHUSDT': 1}, now: now);
    expect(s2.realisedToday, 0);
    expect(s2.unrealised, closeTo(-99, 1e-9));
  });

  test('a call carries its timeframe, and grades read as words', () {
    // A call is a property of a pair AND a horizon: the same coin can be
    // SELL on 1h and FLAT on 1d in the same second. Losing the interval on
    // the way to the dashboard would show a different answer from the one
    // that was tapped.
    final s = LiveSignal.fromJson({
      'symbol': 'SUIUSDT',
      'interval': '1h',
      'action': 'SELL',
      'strength': 'small',
      'ev': 1.62,
      'price': 0.82,
    });
    expect(s.interval, '1h');
    expect(s.isSell, isTrue);
    expect(s.short, 'SUI');
    expect(s.isEquity, isFalse);
    // 'small' is accurate inside the model and reads as a typo on a list
    expect(s.strengthLabel, 'LOW');

    final equity = LiveSignal.fromJson({
      'symbol': 'NVDA', 'interval': '1d', 'action': 'BUY',
      'strength': 'strong',
    });
    expect(equity.isEquity, isTrue);
    expect(equity.short, 'NVDA');
    expect(equity.strengthLabel, 'STRONG');
    expect(equity.isSell, isFalse);
  });

  test('the calls list separates crypto from equities', () {
    final all = LiveSignals.fromJson({
      'watched_symbols': 15,
      'intervals': ['15m', '1h', '4h', '1d'],
      'signals': [
        {'symbol': 'BTCUSDT', 'interval': '1d', 'action': 'BUY',
         'strength': 'strong'},
        {'symbol': 'NVDA', 'interval': '1h', 'action': 'SELL',
         'strength': 'medium'},
      ],
    });
    expect(all.signals.length, 2);
    expect(all.watched, 15);
    expect(all.signals.where((s) => s.isEquity).length, 1);
    expect(all.signals.where((s) => !s.isEquity).single.symbol, 'BTCUSDT');
  });

  test('a cheap coin keeps the digits it moves in', () {
    // THE BUG THIS PINS
    //
    // Five files each carried `toStringAsFixed(v >= 100 ? 2 : 4)`. Right for
    // BTC, wrong for anything cheap: 1000PEPEUSDT trades at 0.003624 and
    // rendered as "0.0036", throwing away the two digits that carry the move.
    // A half-percent change was invisible.
    expect(priceText(0.003624), r'$0.003624');
    expect(priceText(0.0000012), r'$0.0000012');
    expect(priceText(0.824), r'$0.824');

    // and the expensive end is unchanged
    expect(priceText(79000.12), r'$79,000.12');
    expect(priceText(150.0), r'$150.00');
    expect(priceText(1.3976), r'$1.3976');

    // null is a dash, never zero — "$0.00" reads as a level
    expect(priceText(null), '—');
  });

  test('the status badge never claims the bot trades', () {
    final s = BotStatus.fromJson({
      'active': true,
      'label': 'WATCHING',
      'detail': 'reading every closed bar',
      'trades': false,
    });
    expect(s.trades, isFalse);
    expect(s.label, isNot(contains('TRADING')));
  });

  test('an untrained pair is reported as untrained', () {
    final c = Coin.fromJson({
      'symbol': 'ADAUSDT',
      'name': 'Cardano',
      'short': 'ADA',
      'pair': 'ADA / USDT',
      'price': 0.45,
      'change_pct': 0.2,
      'trained': false,
    });
    expect(c.trained, isFalse);
  });

  testWidgets('a saved session opens the app even with no network',
      (tester) async {
    // THE BUG THIS PINS. `_restore()` used to catch every failure from
    // `me()` identically and fall through to the sign-in screen. In a test
    // — and on a phone opened before wifi settles — that call cannot reach
    // the server, so a perfectly good session looked exactly like being
    // logged out, and the app asked for a password on every cold start.
    //
    // No network is available here, so this exercises the unreachable path
    // specifically: the cached account must carry the app into the shell.
    SharedPreferences.setMockInitialValues({
      'api.token.v1': 'a-session-token',
      'api.account.v1': json.encode({
        'identifier': 'tim',
        'tier': 'admin',
        'entitled': true,
        'operator': false,
      }),
    });

    await tester.pumpWidget(const VanthApp());
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 400));

    expect(find.text('SIGN IN'), findsNothing,
        reason: 'an unreachable server was treated as a dead session');
    expect(find.text('Dashboard'), findsOneWidget);
  });

  testWidgets('a token with no cached account still asks for a password',
      (tester) async {
    // The other side of it: falling back to a cache is only defensible when
    // there IS one. A token alone says nothing about who it belongs to, and
    // guessing would put someone in a session that may not be theirs.
    SharedPreferences.setMockInitialValues({
      'api.token.v1': 'a-session-token',
    });

    await tester.pumpWidget(const VanthApp());
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 400));

    expect(find.text('SIGN IN'), findsOneWidget);
  });

  test('dragging a coin down lands it where it was dropped', () async {
    // The off-by-one that makes drag-to-reorder feel broken: with the old
    // `onReorder` the framework reported a PRE-removal index, so removing
    // first and inserting at the raw value put every downward drag one row
    // short. `onReorderItem` adjusts it, and Watchlist.reorder must therefore
    // NOT adjust it again — double-correcting is the same bug mirrored.
    SharedPreferences.setMockInitialValues({});
    final w = Watchlist.instance;

    await w.load();
    // start from a known order
    for (final s in ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'ADAUSDT']) {
      await w.add(s);
    }
    final start = await w.load();
    expect(start.first, 'BTCUSDT');

    // drag the first item to position 2 (post-removal coordinates)
    final moved = await w.reorder(0, 2);
    expect(moved[2], start.first,
        reason: 'the dragged pair did not land at the index it was dropped on');
    expect(moved.length, start.length, reason: 'reorder changed the count');
    expect(moved.toSet(), start.toSet(), reason: 'reorder lost or added a pair');

    // and dragging it back restores the original order exactly
    final back = await w.reorder(2, 0);
    expect(back, start);
  });

  test('reordering survives an out-of-range index instead of throwing', () async {
    // The list can be reloaded underneath a drag in flight.
    SharedPreferences.setMockInitialValues({});
    final w = Watchlist.instance;
    final before = await w.load();
    final after = await w.reorder(99, 0);
    expect(after, before);
  });

  test('a coin can be silenced whole, or one timeframe at a time', () async {
    // Muting was per-coin only, which forced you to silence a coin entirely
    // to escape its noisiest timeframe.
    SharedPreferences.setMockInitialValues({});
    final m = Muted.instance;
    await m.load();

    expect(m.isMuted('BTCUSDT', '1h'), isFalse);

    // one timeframe: that timeframe goes quiet, the others do not
    await m.toggle('BTCUSDT', '1m');
    expect(m.isMuted('BTCUSDT', '1m'), isTrue);
    expect(m.isMuted('BTCUSDT', '1h'), isFalse);

    // the coin-level mute wins over anything more specific
    await m.toggle('BTCUSDT');
    expect(m.isMuted('BTCUSDT', '1h'), isTrue,
        reason: 'a coin-level mute must silence every timeframe');
    expect(m.isMuted('BTCUSDT', ''), isTrue);

    // and an alert with no timeframe at all follows the coin
    await m.toggle('BTCUSDT');
    expect(m.isMuted('BTCUSDT', ''), isFalse);
    expect(m.isMuted('BTCUSDT', '1m'), isTrue,
        reason: 'un-muting the coin must not clear per-timeframe choices');
  });

  test('dropping a coin forgets its timeframe mutes too', () async {
    // Otherwise re-adding a coin brings back settings you have no memory of
    // making, and the bell is off for reasons that look like a bug.
    SharedPreferences.setMockInitialValues({});
    final m = Muted.instance;
    await m.load();
    await m.toggle('SOLUSDT');
    await m.toggle('SOLUSDT', '15m');
    await m.toggle('ADAUSDT', '1h');

    await m.forget('SOLUSDT');
    expect(m.isMuted('SOLUSDT'), isFalse);
    expect(m.isMuted('SOLUSDT', '15m'), isFalse);
    // an unrelated coin is untouched
    expect(m.isMuted('ADAUSDT', '1h'), isTrue);
  });

  test('no sensitivity setting ever shows a losing trade as a call', () {
    // Three levels, all at or above breakeven after costs. The app gates on
    // the strength the SERVER reports, and the server never labels a
    // negative-expected-value entry — so there is no combination of
    // settings that turns a losing trade into a BUY.
    Recommendation make(String strength, String action) =>
        Recommendation.fromJson({
          'action': action,
          'strength': strength,
          'detail': '',
        });

    // a strong call shows at every setting
    for (final s in ['strong', 'medium', 'small']) {
      expect(make('strong', 'BUY').clears(s), isTrue, reason: s);
    }
    // a medium call is withheld only from the strictest setting
    expect(make('medium', 'BUY').clears('strong'), isFalse);
    expect(make('medium', 'BUY').clears('medium'), isTrue);
    expect(make('medium', 'BUY').clears('small'), isTrue);
    // a small call shows only at the loosest
    expect(make('small', 'BUY').clears('strong'), isFalse);
    expect(make('small', 'BUY').clears('small'), isTrue);
    // and no strength at all never clears anything, whatever is chosen
    for (final s in ['strong', 'medium', 'small']) {
      expect(make('', 'FLAT').clears(s), isFalse, reason: s);
    }
  });

  test('nothing witty is said while the wait is still ordinary', () {
    // Most loads finish inside a few seconds. A joke about how slow this is
    // would arrive after the data did.
    expect(quipFor(const Duration(seconds: 0)), isNull);
    expect(quipFor(const Duration(seconds: 59)), isNull);
    expect(quipFor(const Duration(minutes: 1)), kWaitingQuips.first);
  });

  test('each minute gets its own line, and the last one holds', () {
    for (var m = 1; m <= kWaitingQuips.length; m++) {
      expect(quipFor(Duration(minutes: m)), kWaitingQuips[m - 1],
          reason: 'minute $m');
    }
    // Clamped, not wrapped: restarting the sequence would put "damn, why does
    // it take so looong" on screen beside a clock reading 6:00.
    expect(quipFor(const Duration(minutes: 30)), kWaitingQuips.last);
  });

  test('the clock reads as a clock', () {
    expect(formatWaited(const Duration(seconds: 7)), '0:07');
    expect(formatWaited(const Duration(minutes: 2, seconds: 6)), '2:06');
    expect(formatWaited(const Duration(minutes: 12)), '12:00');
  });

  test('an error is only an error after five whole minutes', () {
    // THE BUG: one HTTP timeout, twenty seconds in, used to paint "No link to
    // the service" over a perfectly good session that was about to recover.
    expect(patienceExhausted(const Duration(seconds: 20)), isFalse);
    expect(patienceExhausted(const Duration(minutes: 4, seconds: 59)), isFalse);
    expect(patienceExhausted(kPatience), isTrue);
    expect(kPatience, const Duration(minutes: 5));
  });

  testWidgets('the panel says how long it has waited, and jokes about it',
      (tester) async {
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: WaitingPanel(
            since: DateTime.now().subtract(const Duration(minutes: 2, seconds: 5))),
      ),
    ));
    await tester.pump(const Duration(seconds: 1));

    // A real mm:ss clock, not a fake progress bar creeping toward a number
    // nobody chose. Matched by shape rather than by value: the widget reads
    // the wall clock, so asserting '2:06' would be asserting on the speed of
    // the machine running the test.
    expect(
        find.byWidgetPredicate((w) =>
            w is Text &&
            w.data != null &&
            RegExp(r'^\d+:[0-5]\d$').hasMatch(w.data!)),
        findsOneWidget);
    expect(find.text(kWaitingQuips[1]), findsOneWidget);
    // and it admits this is taking a while rather than restarting the message
    expect(find.textContaining('Still'), findsOneWidget);

    await tester.pumpWidget(const SizedBox());   // dispose, cancelling the tick
  });

  testWidgets('giving up is announced once, by the clock, not by a retry',
      (tester) async {
    var calls = 0;
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: WaitingPanel(
          since: DateTime.now().subtract(const Duration(minutes: 6)),
          onPatienceExhausted: () => calls++,
        ),
      ),
    ));
    await tester.pump();                 // post-frame check
    await tester.pump(const Duration(seconds: 3));
    // Exactly once. A callback that fired every tick would rebuild the error
    // panel once a second for as long as the server stayed down.
    expect(calls, 1);
    await tester.pumpWidget(const SizedBox());
  });

  // ------------------------------------------------------------- alerts
  //
  // THE BUG THESE PIN: notifications for signals and news never arrived. The
  // app reset its cursor to "now" on every launch, so everything that
  // happened while it was closed — which is nearly everything — was fetched
  // by the server and then discarded by the app on the next line.

  Alert alert({
    String kind = 'signal',
    String strength = 'strong',
    String symbol = 'BTCUSDT',
    String interval = '1h',
    String bias = '',
    String impact = '',
    String to = '',
    Duration age = Duration.zero,
  }) {
    final at = DateTime.now().toUtc().subtract(age);
    return Alert.fromJson({
      'id': '$kind-$symbol-$interval-${at.microsecondsSinceEpoch}',
      'kind': kind,
      'title': '$symbol $interval',
      'body': 'b',
      'severity': 'high',
      'symbol': symbol,
      'interval': interval,
      'strength': strength,
      'bias': bias,
      'impact': impact,
      'url': '',
      'seq': at.millisecondsSinceEpoch,
      'at': at.toIso8601String(),
      'detected_at': at.toIso8601String(),
      'extra': to.isEmpty ? <String, String>{} : {'to': to},
    });
  }

  bool nothingMuted(String s, String i) => false;

  test('a signal weaker than the chosen setting never buzzes the phone', () {
    // The dashboard already withholds these. A notification for a call the
    // screen is hiding would be the app telling you two different things.
    final small = [alert(strength: 'small')];
    expect(
        selectDeliverable(small,
            sensitivity: 'strong', isMuted: nothingMuted),
        isEmpty);
    expect(
        selectDeliverable(small, sensitivity: 'small', isMuted: nothingMuted),
        hasLength(1));
  });

  test('news and filings are never gated by a signal setting', () {
    // They have no strength — they are not calls, and the strictest signal
    // setting must not silence the news.
    final feed = [
      alert(kind: 'news', strength: '', symbol: '', interval: ''),
      alert(kind: 'whale', strength: '', symbol: '', interval: ''),
    ];
    expect(
        selectDeliverable(feed, sensitivity: 'strong', isMuted: nothingMuted),
        hasLength(2));
  });

  test('a muted pair stays silent, including one timeframe of it', () {
    final feed = [
      alert(symbol: 'ADAUSDT', interval: '1h'),
      alert(symbol: 'BTCUSDT', interval: '1h'),
    ];
    final kept = selectDeliverable(feed,
        sensitivity: 'strong',
        isMuted: (s, i) => s == 'ADAUSDT' && i == '1h');
    expect(kept.map((a) => a.symbol), ['BTCUSDT']);
  });

  test('a whole category can be silenced without silencing the rest', () async {
    // Seventy headlines a day is a buzz every twenty minutes. If the only way
    // to stop it were the system notification switch, the BUY signals would
    // go with it — so news is separable from signals.
    SharedPreferences.setMockInitialValues({});
    await Muted.instance.load();
    await Muted.instance.toggleKind('news');

    final feed = [
      alert(kind: 'news', strength: '', symbol: '', interval: ''),
      alert(kind: 'signal'),
    ];
    final kept = selectDeliverable(feed,
        sensitivity: 'strong',
        isMuted: nothingMuted,
        isKindMuted: Muted.instance.isKindMuted);
    expect(kept.map((a) => a.kind), ['signal']);

    // and a category key must never be mangled into a symbol on the way in
    expect(Muted.instance.isKindMuted('news'), isTrue);
    expect(Muted.instance.isMuted('NEWS'), isFalse);
  });

  test('a backlog is capped, and the important half survives the cut', () {
    // Coming back to a phone that was off all night must not post twenty
    // notifications — that is what teaches you to clear the shade unread.
    final feed = [
      for (var i = 0; i < 10; i++)
        alert(kind: 'news', strength: '', symbol: '', interval: ''),
      alert(kind: 'signal'),
    ];
    final kept =
        selectDeliverable(feed, sensitivity: 'strong', isMuted: nothingMuted);
    expect(kept, hasLength(maxCatchUp));
    // the signal outranks ten headlines rather than being crowded out by them
    expect(kept.first.kind, 'signal');
  });

  test('stale alerts are not delivered late as though they were new', () {
    // A signal describes a window that has since closed. Waking someone at
    // 09:00 for a call made at 01:00 is worse than staying quiet.
    final old = [alert(age: const Duration(hours: 9))];
    expect(
        selectDeliverable(old, sensitivity: 'strong', isMuted: nothingMuted),
        isEmpty);
    expect(
        selectDeliverable([alert(age: const Duration(hours: 1))],
            sensitivity: 'strong', isMuted: nothingMuted),
        hasLength(1));
  });

  test('the cursor survives a restart, so a closed app misses nothing', () async {
    // THE ACTUAL BUG. `_cursor` was a field on the shell's State, set to
    // "now" on every launch — so the app asked the server for everything
    // since it opened, which is by definition nothing.
    SharedPreferences.setMockInitialValues({});
    expect(await loadCursor(), isNull, reason: 'a first run has no position');

    await saveCursor(1788194410735);
    expect(await loadCursor(), 1788194410735);

    // and a zero or negative stored value reads as "no position", not as
    // "the beginning of time" — which would replay the entire log
    await saveCursor(0);
    expect(await loadCursor(), isNull);
  });

  Alert story(String bias, String impact) => alert(
      kind: 'news', strength: '', symbol: '', interval: '',
      bias: bias, impact: impact);

  test('the four news levels each mean exactly what they say', () {
    final readable = story('BULL', 'STRONG IMPACT');
    final mixedStrong = story('MIXED', 'STRONG IMPACT');
    final unread = story('NO READING', 'NO READING');
    final quietBear = story('BEAR', 'ALMOST NO IMPACT');

    List<String> pass(String level) => [readable, mixedStrong, unread,
            quietBear]
        .where((a) => clearsNewsLevel(a.bias, a.impact, level))
        .map((a) => '${a.bias}/${a.impact}')
        .toList();

    expect(pass('all'), hasLength(4), reason: 'all means all');
    expect(pass('none'), isEmpty);
    // direction, whichever size — including a bearish story too small to
    // matter, because the setting asks "which way", not "how much"
    expect(pass('directional'),
        ['BULL/STRONG IMPACT', 'BEAR/ALMOST NO IMPACT']);
    // size, whichever direction — including MIXED, which `directional` drops
    expect(pass('strong'), ['BULL/STRONG IMPACT', 'MIXED/STRONG IMPACT']);
  });

  test('the news level never touches signals, filings or releases', () {
    // "None" for news must not silence a BUY. They are different questions
    // and one control answering both is how a setting becomes a trap.
    final feed = [
      story('BULL', 'STRONG IMPACT'),
      alert(kind: 'signal'),
      alert(kind: 'whale', strength: '', symbol: '', interval: ''),
      alert(kind: 'calendar', strength: '', symbol: '', interval: ''),
    ];
    final kept = selectDeliverable(feed,
        sensitivity: 'strong', isMuted: nothingMuted, newsLevel: 'none');
    expect(kept.map((a) => a.kind), ['signal', 'calendar', 'whale']);
  });

  test('an unreadable headline is dropped by every level except all', () {
    // Half of all headlines get no reading. That is the scorer's honest
    // limit, and it is exactly what the three narrow settings are choosing
    // to accept — so the setting screen has to say so, and this pins that
    // the behaviour matches the claim.
    final feed = [story('NO READING', 'NO READING')];
    for (final level in ['directional', 'strong', 'none']) {
      expect(
          selectDeliverable(feed,
              sensitivity: 'strong', isMuted: nothingMuted, newsLevel: level),
          isEmpty,
          reason: level);
    }
    expect(
        selectDeliverable(feed,
            sensitivity: 'strong', isMuted: nothingMuted, newsLevel: 'all'),
        hasLength(1));
  });

  test('an unknown stored level falls back to everything, not to silence', () {
    // A corrupt or future setting value must fail LOUD. Failing to silence
    // is a bug you notice; failing to silent is one you never do.
    expect(clearsNewsLevel('NO READING', 'NO READING', 'wat'), isTrue);
    expect(clearsNewsLevel('NO READING', 'NO READING', ''), isTrue);
  });

  // ------------------------------------------------------------ screener
  test('a preset hands over COPIES, so editing one cannot rewrite it', () {
    // THE REQUIREMENT: "when you recommend these recommendations the
    // parameters change also for a user so that he could change them any
    // time." If `instantiate()` returned the preset's own Filter objects,
    // typing a new threshold would silently redefine the recommendation for
    // every later use — and there would be no way back to the original.
    final preset = ScreenerPreset.fromJson({
      'id': 'oversold_bounce',
      'name': 'Recommended for oversold bounce plays',
      'note': '',
      'filters': [
        {'field': 'rsi14', 'op': 'lt', 'value': 30},
        {'field': 'price', 'op': 'gt', 'value': 5},
      ],
      'unavailable': [],
    });

    final mine = preset.instantiate();
    mine[0].value = 45;
    mine[0].op = 'gt';

    expect(preset.filters[0].value, 30, reason: 'the preset was mutated');
    expect(preset.filters[0].op, 'lt');
    expect(mine[0].value, 45);
    // and a second application starts from the original again
    expect(preset.instantiate()[0].value, 30);
  });

  test('a field needing analyst estimates is flagged, not hidden', () {
    final f = ScreenerField.fromJson({
      'id': 'peg', 'label': 'PEG', 'group': 'Growth',
      'kind': 'ratio', 'needs': 'estimates',
    });
    expect(f.unavailable, isTrue);
    final ok = ScreenerField.fromJson({
      'id': 'rsi14', 'label': 'RSI(14)', 'group': 'Price & volume',
      'kind': 'number', 'needs': 'price',
    });
    expect(ok.unavailable, isFalse);
  });

  test('a missing metric renders as a dash, never as zero', () {
    // A blank ROE shown as "0.00%" reads as a company that earns nothing,
    // which is a claim about the business rather than about our data.
    final f = ScreenerField.fromJson({
      'id': 'roe', 'label': 'Return on equity', 'group': 'Company',
      'kind': 'percent', 'needs': 'filings',
    });
    expect(f.format(null), '—');
    expect(f.format(0), '0.00%');
    // Two decimals below 10, one above: a 2.31% dividend yield needs the
    // precision, a 15.5% return on equity does not.
    expect(f.format(2.31), '2.31%');
    expect(f.format(15.5), '15.5%');
  });

  test('big numbers are compacted the way a screener reads them', () {
    final cap = ScreenerField.fromJson({
      'id': 'market_cap', 'label': 'Market cap', 'group': 'Company',
      'kind': 'currency', 'needs': 'filings',
    });
    expect(cap.format(4.749e12), '\$4.75T');
    expect(cap.format(3.79e11), '\$379.00B');
    expect(cap.format(50e6), '\$50.00M');
  });

  test('a filter describes itself as a sentence', () {
    final rsi = ScreenerField.fromJson({
      'id': 'rsi14', 'label': 'RSI(14)', 'group': 'p', 'kind': 'number',
    });
    final sma = ScreenerField.fromJson({
      'id': 'above_sma50', 'label': 'Price vs SMA50', 'group': 'p',
      'kind': 'bool',
    });
    expect(ScreenerFilter(field: 'rsi14', op: 'lt', value: 30).describe(rsi),
        'RSI(14) under 30.00');
    // the combination easiest to get backwards, spelled out
    expect(ScreenerFilter(field: 'above_sma50', op: 'is_false').describe(sma),
        'Price vs SMA50 — below');
    expect(ScreenerFilter(field: 'above_sma50', op: 'is_true').describe(sma),
        'Price vs SMA50 — above');
  });

  test('the market switch defaults to the half that actually works', () async {
    // Crypto has fitted models, a running collector and alerts. Stocks has a
    // screener. Opening into the less finished half is a worse introduction.
    SharedPreferences.setMockInitialValues({});
    final store = MarketModeStore.instance;
    expect(store.mode, MarketMode.crypto);
    expect(store.isStocks, isFalse);

    await store.set(MarketMode.stocks);
    expect(store.isStocks, isTrue);
    await store.set(MarketMode.crypto);
  });

  test('no tab forces a market switch any more', () {
    // The screener WAS stocks-only, and selecting it flipped the market for
    // you. It has its own field set and presets in both markets now — a
    // perpetual has no P/E and a stock has no funding rate, so they are two
    // catalogues rather than one filtered — and nothing should silently move
    // you between markets.
    expect(FrostedNav.stocksOnly, isEmpty);
  });

  test('the countdown never contradicts the date printed beside it', () {
    // THE BUG THIS PINS. `Duration.inDays` truncates, so an event 3 days and
    // 20 hours away rendered as "in 3 days" directly above a date four
    // calendar days later. Two halves of one panel disagreeing, with the
    // smaller number the one people act on.
    String c(Duration d) => DashboardScreen.countdown(d);

    expect(c(const Duration(days: 3, hours: 20)), 'in 3d 20h');
    expect(c(const Duration(days: 1, hours: 13, minutes: 44)), 'in 1d 13h');
    // exactly on a day boundary still says the hours, so the format never
    // changes shape underneath you as it counts down
    expect(c(const Duration(days: 2)), 'in 2d 0h');

    // under a day it switches to hours and minutes
    expect(c(const Duration(hours: 5, minutes: 20)), 'in 5h 20m');
    expect(c(const Duration(hours: 47, minutes: 59)), 'in 1d 23h');
    // and under an hour, to minutes
    expect(c(const Duration(minutes: 45)), 'in 45 min');
    expect(c(const Duration(minutes: 1)), 'in 1 min');
    // a release already under way is not "in -3 minutes"
    expect(c(const Duration(minutes: -3)), 'now');
  });

  test('a stock drag lands where it was dropped, like the crypto list', () async {
    // I wrote this one WITH the off-by-one correction that `onReorderItem`
    // already applies, so every downward drag would have landed a row short
    // in storage while looking right on screen — until the next load snapped
    // it back. The crypto version carries a "do not add a correction here"
    // comment for exactly this reason.
    SharedPreferences.setMockInitialValues({});
    final w = StockWatchlist.instance;
    for (final s in ['AAPL', 'NVDA', 'META', 'GOOGL']) {
      await w.add(s);
    }
    final start = await w.load();
    expect(start, ['AAPL', 'NVDA', 'META', 'GOOGL']);

    // post-removal coordinates: move AAPL to index 2
    final moved = await w.reorder(0, 2);
    expect(moved[2], 'AAPL', reason: 'landed short of where it was dropped');
    expect(moved.toSet(), start.toSet(), reason: 'reorder lost a symbol');

    final back = await w.reorder(2, 0);
    expect(back, start);
  });

  test('the timeframe is remembered across launches and across markets', () async {
    // Opening a stock always landed on 1d regardless of what you had been
    // reading a moment earlier on a coin. The timeframe is a question you
    // are asking — "how far ahead am I looking" — not a property of the
    // instrument, so it should not reset when you cross markets.
    SharedPreferences.setMockInitialValues({});
    final s = Settings.instance;

    // a sensible default before anything is chosen: 4h, the shorter of the
    // two timeframes still offered (15m and 1h could not clear a fee)
    expect(await s.lastInterval(), '4h');

    await s.saveInterval('1d');
    expect(await s.lastInterval(), '1d');
    // a timeframe the server no longer serves falls back too
    await s.saveInterval('1h');
    expect(await s.lastInterval(), '4h');

    // a value that is not a real timeframe falls back rather than being
    // handed to an API that would 400 on it
    await s.saveInterval('3h');
    expect(await s.lastInterval(), '4h');
  });

  test('an exit to FLAT is delivered at every sensitivity setting', () {
    // THE BUG THIS PINS. A FLAT recommendation carries strength "" — there is
    // no expected value to grade because nothing is being opened. Running it
    // through the ENTRY-strength gate rejected every exit: the server sent
    // them, the phone dropped them, and nothing anywhere recorded it.
    //
    // The exit is the alert you most want while you are holding something.
    final exit = alert(strength: '', to: 'FLAT');
    for (final setting in ['strong', 'medium', 'small']) {
      expect(
          selectDeliverable([exit],
              sensitivity: setting, isMuted: nothingMuted),
          hasLength(1),
          reason: 'exit dropped at sensitivity "$setting"');
    }
    expect(exit.isExit, isTrue);
  });

  test('an entry is still graded, so the gate did not simply get removed', () {
    // The fix must not become "deliver every signal": a weak ENTRY should
    // still be withheld when the setting asks for strong ones.
    final weakEntry = alert(strength: 'small', to: 'BUY');
    expect(weakEntry.isExit, isFalse);
    expect(
        selectDeliverable([weakEntry],
            sensitivity: 'strong', isMuted: nothingMuted),
        isEmpty);
    expect(
        selectDeliverable([weakEntry],
            sensitivity: 'small', isMuted: nothingMuted),
        hasLength(1));
  });

  test('a muted pair silences its exits too', () {
    // Exits bypass the STRENGTH gate, not every gate. Someone who silenced a
    // coin does not want its exits either.
    final exit = alert(strength: '', to: 'FLAT', symbol: 'ADAUSDT');
    expect(
        selectDeliverable([exit],
            sensitivity: 'strong', isMuted: (s, i) => s == 'ADAUSDT'),
        isEmpty);
  });

  // THE PAYWALL THAT SURVIVED THE PAYMENT
  //
  // `launchUrl` completes when the browser opens, not when the customer
  // comes back. The first version asked the server about the account on the
  // line after it -- while they were still typing their card number -- got
  // "free", and never asked again, so a successful payment left the paywall
  // up until the app was force-quit.
  //
  // Neither half is reachable from a widget test: one is an OS lifecycle
  // callback, the other launches an external browser. Both are asserted
  // against the source instead.
  //
  // The FIRST version of these tests searched the whole file and passed with
  // the bug deliberately put back, because `_pollForUpgrade()` still matched
  // its own declaration further down. Hence the brace matching: the
  // assertion has to be about the method body, not the file.
  String bodyOf(String src, String signature) {
    final start = src.indexOf(signature);
    expect(start, greaterThan(-1), reason: 'no $signature');
    final open = src.indexOf('{', start);
    var depth = 0;
    for (var j = open; j < src.length; j++) {
      if (src[j] == '{') depth++;
      if (src[j] == '}') {
        depth--;
        if (depth == 0) return src.substring(open, j + 1);
      }
    }
    fail('unbalanced braces after $signature');
  }

  group('coming back from Stripe', () {
    test('the shell looks for the upgrade on resume', () {
      final body = bodyOf(File('lib/screens/shell.dart').readAsStringSync(),
          'void didChangeAppLifecycleState');
      expect(body.contains('AppLifecycleState.resumed'), isTrue);
      expect(body.contains('_pollForUpgrade()'), isTrue,
          reason: 'resume must look for the upgrade the webhook granted');
    });

    test('it asks more than once, because the webhook may not have landed',
        () {
      final body = bodyOf(File('lib/screens/shell.dart').readAsStringSync(),
          'Future<void> _pollForUpgrade');
      expect('Duration(seconds:'.allMatches(body).length,
          greaterThanOrEqualTo(2),
          reason: 'one check races Stripe and shows a payer the paywall');
    });

    test('the subscribe screen never decides this for itself', () {
      final src =
          File('lib/screens/subscribe_screen.dart').readAsStringSync();
      expect(src.contains('client.me()'), isFalse,
          reason: 'asking here runs before the customer has paid');
      expect(src.contains('onCheckoutStarted()'), isTrue);
    });
  });

  // The same table lives in `tests/test_alerts.py`. The notification is
  // formatted in Python and the dashboard behind it in Dart, so a price that
  // renders differently in the two means the level you tapped is not the
  // level you were shown. Change one, change the other.
  group('prices read the same in the notification and the app', () {
    const table = <List<Object>>[
      [79607.25, r'$79,607.25'],
      [2517.085, r'$2,517.09'],
      [0.82615, r'$0.8262'],
      [0.003624, r'$0.003624'],
      [0.00001234, r'$0.00001234'],
      [0.0, r'$0.00'],
      [-1234.5, r'-$1,234.50'],
    ];
    test('every entry matches the Python table', () {
      for (final row in table) {
        expect(priceText(row[0] as double), row[1] as String,
            reason: 'price ${row[0]} disagrees with api/alerts.py');
      }
    });
  });

  // "YOU HOLD THIS"
  //
  // The trade log lives on this phone; the notification text is built on
  // the server. So the line is added in two places -- here for the in-app
  // banner, and in api/push.py for the lock-screen push -- and the wording
  // is pinned to the Python side's `entry_line` character for character.
  // If one drifts, the same alert reads differently depending on which
  // path delivered it.
  group('a signal on a coin you hold says so', () {
    test('the line, word for word as api/push.py writes it', () async {
      SharedPreferences.setMockInitialValues({});
      Trades.instance.resetForTest();
      await Trades.instance.add(TradeEntry.create(
          symbol: 'BTCUSDT', side: 'LONG', size: 1, entryPrice: 79200));
      expect(await heldLine('BTCUSDT'), 'Open entry: LONG @ 79,200.00');
      expect(await heldLine('ETHUSDT'), isNull);
    });

    test('a closed entry no longer counts', () async {
      SharedPreferences.setMockInitialValues({});
      Trades.instance.resetForTest();
      final t = TradeEntry.create(
          symbol: 'SOLUSDT', side: 'SHORT', size: 1, entryPrice: 200);
      await Trades.instance.add(t);
      expect(await heldLine('SOLUSDT'), 'Open entry: SHORT @ 200.00');
      await Trades.instance.close(t.id, 190);
      expect(await heldLine('SOLUSDT'), isNull);
    });

    test('a cheap coin keeps its digits, same as the levels', () async {
      SharedPreferences.setMockInitialValues({});
      Trades.instance.resetForTest();
      await Trades.instance.add(TradeEntry.create(
          symbol: '1000PEPEUSDT', side: 'LONG', size: 1,
          entryPrice: 0.003624));
      expect(await heldLine('1000PEPEUSDT'), 'Open entry: LONG @ 0.003624');
    });

    test('what the phone ships to the relay', () async {
      SharedPreferences.setMockInitialValues({});
      Trades.instance.resetForTest();
      await Trades.instance.add(TradeEntry.create(
          symbol: 'ETHUSDT', side: 'SHORT', size: 1, entryPrice: 2500));
      await Trades.instance.add(TradeEntry.create(
          symbol: 'BTCUSDT', side: 'LONG', size: 1, entryPrice: 79200));
      final closed = TradeEntry.create(
          symbol: 'SOLUSDT', side: 'LONG', size: 1, entryPrice: 200);
      await Trades.instance.add(closed);
      await Trades.instance.close(closed.id, 210);

      final shipped = await PushDelivery.instance.openPositions();
      // sorted by symbol, closed one absent, exactly the three keys the
      // server validates
      expect(shipped, [
        {'symbol': 'BTCUSDT', 'side': 'LONG', 'entry': 79200.0},
        {'symbol': 'ETHUSDT', 'side': 'SHORT', 'entry': 2500.0},
      ]);
    });

    test('opening an entry notifies, so the shell can re-sync', () async {
      SharedPreferences.setMockInitialValues({});
      Trades.instance.resetForTest();
      var fired = 0;
      void bump() => fired++;
      Trades.instance.addListener(bump);
      try {
        await Trades.instance.add(TradeEntry.create(
            symbol: 'BTCUSDT', side: 'LONG', size: 1, entryPrice: 1));
        expect(fired, 1);
      } finally {
        Trades.instance.removeListener(bump);
      }
    });
  });

  // CLOSING ON THE TICK
  //
  // `settle` ran on screen load and on the background poll, and nowhere
  // else. Between those a position sat open on a screen whose live price
  // had visibly crossed the level; if price came back before the next
  // poll, the touch was never recorded at all. `checkLive` runs on every
  // socket tick. `autoClose` is the one door both paths go through, so a
  // touch both of them see still closes once and notifies once.
  group('a level hit on the live price closes the entry now', () {
    TradeEntry open(String sym, String side, double entry,
        {double? tp, double? sl}) =>
        TradeEntry(
            id: '$sym-$side', symbol: sym, side: side, size: 1,
            entryPrice: entry, openedAt: DateTime.now(),
            takeProfit: tp, stopLoss: sl);

    Future<void> fresh(List<TradeEntry> entries) async {
      SharedPreferences.setMockInitialValues({});
      Trades.instance.resetForTest();
      for (final t in entries) {
        await Trades.instance.add(t);
      }
    }

    test('a long at its take profit, a short at its stop', () async {
      await fresh([
        open('BTCUSDT', 'LONG', 100, tp: 110, sl: 90),
        open('ETHUSDT', 'SHORT', 100, tp: 90, sl: 110),
      ]);
      final closed = (await Trades.instance
              .checkLive({'BTCUSDT': 110.0, 'ETHUSDT': 110.0}))
          .settled;
      expect(closed.map((t) => '${t.symbol}:${t.closedBy}').toSet(),
          {'BTCUSDT:take_profit', 'ETHUSDT:stop_loss'});
      final all = await Trades.instance.load();
      expect(all.where((t) => t.isOpen), isEmpty);
      // closed AT the level you set, not at whatever the tick was
      expect(all.firstWhere((t) => t.symbol == 'BTCUSDT').closePrice, 110);
    });

    test('a price between the levels changes nothing', () async {
      await fresh([open('BTCUSDT', 'LONG', 100, tp: 110, sl: 90)]);
      expect((await Trades.instance.checkLive({'BTCUSDT': 105.0})).settled, isEmpty);
      expect((await Trades.instance.load()).single.isOpen, isTrue);
    });

    test('a coin with no tick, and an entry with no levels, are left alone',
        () async {
      await fresh([
        open('BTCUSDT', 'LONG', 100, tp: 110, sl: 90),
        open('SOLUSDT', 'LONG', 100),               // no levels at all
      ]);
      expect((await Trades.instance.checkLive({'ETHUSDT': 1.0})).settled, isEmpty);
      expect((await Trades.instance.checkLive({'SOLUSDT': 999.0})).settled, isEmpty);
      expect((await Trades.instance.load()).every((t) => t.isOpen), isTrue);
    });

    test('a zero or broken tick is never believed', () async {
      // A reconnecting socket can emit 0. A long with a stop at 90 would
      // close on it -- permanently, in the journal.
      await fresh([open('BTCUSDT', 'LONG', 100, tp: 110, sl: 90)]);
      expect((await Trades.instance.checkLive({'BTCUSDT': 0.0})).settled, isEmpty);
      expect((await Trades.instance.checkLive({'BTCUSDT': double.nan}))
          .settled, isEmpty);
      expect((await Trades.instance.checkLive({'BTCUSDT': -5.0})).settled, isEmpty);
      expect((await Trades.instance.load()).single.isOpen, isTrue);
    });

    test('two paths seeing one touch produce one close', () async {
      // The tick closes it; then settle, having asked the server, sees the
      // same touch. The second must find it already closed and do nothing
      // -- not close it again, and not notify again.
      await fresh([open('BTCUSDT', 'LONG', 100, tp: 110, sl: 90)]);
      final first = await Trades.instance.autoClose('BTCUSDT-LONG',
          'take_profit');
      final second = await Trades.instance.autoClose('BTCUSDT-LONG',
          'take_profit');
      expect(first, isNotNull);
      expect(second, isNull, reason: 'closed twice');
      final t = (await Trades.instance.load()).single;
      expect(t.isOpen, isFalse);
      expect(t.closedBy, 'take_profit');
    });

    test('a manual close beats the tick, and the tick then says nothing',
        () async {
      await fresh([open('BTCUSDT', 'LONG', 100, tp: 110, sl: 90)]);
      await Trades.instance.close('BTCUSDT-LONG', 104);   // you closed it
      expect((await Trades.instance.checkLive({'BTCUSDT': 110.0})).settled, isEmpty);
      final t = (await Trades.instance.load()).single;
      expect(t.closePrice, 104, reason: 'the tick overwrote a manual close');
      expect(t.closedBy, '');
    });
  });

  // THE BARRIER BAR
  //
  // Stop at the left edge, target at the right, entry in the middle; the
  // fill grows from the centre toward price and is red on the stop side,
  // green on the target side. Rendered and checked by colour, because the
  // bar it replaced could not show an adverse move at all.
  group('the barrier bar', () {
    TradeEntry e(String side, double tp, double sl) => TradeEntry(
        id: 'x', symbol: 'BTCUSDT', side: side, size: 1, entryPrice: 100,
        openedAt: DateTime.now(), takeProfit: tp, stopLoss: sl);

    Future<Set<Color>> fillColours(WidgetTester t, TradeEntry entry,
        double live) async {
      await t.pumpWidget(MaterialApp(
          home: Scaffold(
              body: SizedBox(
                  width: 300,
                  child: BarrierBar(entry: entry, livePrice: live)))));
      return t
          .widgetList<Container>(find.byType(Container))
          .map((c) => (c.decoration as BoxDecoration?)?.color)
          .whereType<Color>()
          .toSet();
    }

    testWidgets('red toward the stop, green toward the target', (t) async {
      var c = await fillColours(t, e('LONG', 130, 90), 95);
      expect(c, contains(Obsidian.red));
      expect(c, isNot(contains(Obsidian.green)));
      expect(find.text('50% to SL'), findsOneWidget);

      c = await fillColours(t, e('LONG', 130, 90), 118);
      expect(c, contains(Obsidian.green));
      expect(c, isNot(contains(Obsidian.red)));
      expect(find.text('60% to TP'), findsOneWidget);
    });

    testWidgets('a short colours by its own direction', (t) async {
      // down is good for a short: 90 on a 100 short with target 80 is
      // halfway to the target, and must be green, not red
      final c = await fillColours(t, e('SHORT', 80, 110), 90);
      expect(c, contains(Obsidian.green));
      expect(c, isNot(contains(Obsidian.red)));
      expect(find.text('50% to TP'), findsOneWidget);
    });

    testWidgets('the ends are labelled with the levels', (t) async {
      await fillColours(t, e('LONG', 130, 90), 100);
      expect(find.textContaining('SL '), findsOneWidget);
      expect(find.textContaining('TP '), findsOneWidget);
      expect(find.textContaining('130'), findsOneWidget);
      expect(find.textContaining('90'), findsOneWidget);
    });
  });

  // THE FURTHEST IT HAS BEEN
  //
  // The lighter band on the bar: "it reached 90% to TP, it is at 85% now".
  // Fed from two places that must merge and never overwrite -- the server's
  // range since entry (the hours the app was closed) and the live ticks
  // (while a screen is open) -- and shown on both sides at once when a
  // trade has been both ways.
  group('the furthest price has been since entry', () {
    TradeEntry e(String side, double tp, double sl, {double? hi, double? lo}) =>
        TradeEntry(id: 'x', symbol: 'BTCUSDT', side: side, size: 1,
            entryPrice: 100, openedAt: DateTime.now(), takeProfit: tp,
            stopLoss: sl, highSince: hi, lowSince: lo);

    test('best and worst are scaled like the live position', () {
      final t = e('LONG', 130, 90, hi: 127, lo: 94);
      expect(t.bestPosition, closeTo(0.9, 1e-9));    // 27 of 30 up
      expect(t.worstPosition, closeTo(0.6, 1e-9));   // 6 of 10 down
      expect(t.barrierPosition(125.5), closeTo(0.85, 1e-9));
      // a short reads the other way round
      final sh = e('SHORT', 80, 110, hi: 104, lo: 86);
      expect(sh.bestPosition, closeTo(0.7, 1e-9));   // 14 of 20 down
      expect(sh.worstPosition, closeTo(0.4, 1e-9));  // 4 of 10 up
      // never seen past entry on a side -> 0 on that side, not negative
      expect(e('LONG', 130, 90, hi: 120, lo: 100).worstPosition, 0.0);
      expect(e('LONG', 130, 90).bestPosition, isNull);
    });

    test('extremes only ever widen, from either source', () {
      var t = e('LONG', 130, 90);
      t = t.withExtremes(high: 110, low: 97);
      expect((t.highSince, t.lowSince), (110.0, 97.0));
      // a single tick above entry sets the high; the low stays at ENTRY,
      // because price was there when the trade opened
      final one = e('LONG', 130, 90).withExtremes(high: 118, low: 118);
      expect((one.highSince, one.lowSince), (118.0, 100.0));
      // the server says the range was narrower than a tick already saw:
      // nothing shrinks
      final same = t.withExtremes(high: 105, low: 98);
      expect(identical(same, t), isTrue, reason: 'narrower must be a no-op');
      // and a tick beyond it widens one side without touching the other
      t = t.withExtremes(high: 127, low: 127);
      expect((t.highSince, t.lowSince), (127.0, 97.0));
      // garbage is ignored, not recorded
      expect(identical(t.withExtremes(high: 0, low: -1), t), isTrue);
      expect(identical(t.withExtremes(high: double.nan), t), isTrue);
    });

    test('a live tick widens the bar without a disk write', () async {
      SharedPreferences.setMockInitialValues({});
      Trades.instance.resetForTest();
      await Trades.instance.add(e('LONG', 130, 90));
      final r = await Trades.instance.checkLive({'BTCUSDT': 118.0});
      expect(r.extremesMoved, isTrue);
      expect(r.settled, isEmpty);
      expect((await Trades.instance.load()).single.highSince, 118.0);
      // a quiet tick inside the range moves nothing
      final again = await Trades.instance.checkLive({'BTCUSDT': 110.0});
      expect(again.extremesMoved, isFalse);
    });

    test('closing keeps the extremes, so a finished trade still shows them',
        () {
      final t = e('LONG', 130, 90, hi: 127, lo: 94).closedAtPrice(125);
      expect((t.highSince, t.lowSince), (127.0, 94.0));
    });

    test('they survive being written and read back', () {
      final t = TradeEntry.fromJson(
          e('LONG', 130, 90, hi: 127, lo: 94).toJson());
      expect((t.highSince, t.lowSince), (127.0, 94.0));
      // and an entry saved before the fields existed reads as unknown
      final old = e('LONG', 130, 90).toJson()
        ..remove('high_since')
        ..remove('low_since');
      expect(TradeEntry.fromJson(old).highSince, isNull);
    });

    testWidgets('the lighter band is drawn beyond the live fill', (t) async {
      await t.pumpWidget(MaterialApp(
          home: Scaffold(
              body: SizedBox(
                  width: 300,
                  child: BarrierBar(
                      entry: e('LONG', 130, 90, hi: 127, lo: 94),
                      livePrice: 125.5)))));
      final colours = t
          .widgetList<Container>(find.byType(Container))
          .map((c) => (c.decoration as BoxDecoration?)?.color)
          .whereType<Color>()
          .toSet();
      // solid green for now, light green for where it got to, light red
      // for the dip -- all three at once
      expect(colours, contains(Obsidian.green));
      expect(colours, contains(Obsidian.green.withValues(alpha: 0.28)));
      expect(colours, contains(Obsidian.red.withValues(alpha: 0.28)));
      expect(colours, isNot(contains(Obsidian.red)),
          reason: 'price is on the target side; no solid red');
    });
  });

  // TAP TO OPEN
  //
  // A notification about DOGEUSDT 4h used to open the app on whatever it
  // showed last, because no tap handler was registered at all. Now every
  // "take me there" -- notification, logged entry, call on the market
  // page -- goes through one door in the shell, carrying symbol AND
  // timeframe. What can be tested without a device: the payload survives
  // the round trip, the entry remembers its timeframe, and the market is
  // worked out correctly from the symbol.
  group('tapping a thing opens the right pair on the right timeframe', () {
    test('the notification payload round-trips symbol and timeframe', () {
      final p = OpenRequest.encode('DOGEUSDT', '4h', 'alert-1');
      final r = OpenRequest.decode(p)!;
      expect((r.symbol, r.interval), ('DOGEUSDT', '4h'));
      // a trade-closed notification has a pair but no timeframe of its own
      final c = OpenRequest.decode(OpenRequest.encode('BTCUSDT', null, 'x'))!;
      expect((c.symbol, c.interval), ('BTCUSDT', null));
      // two alerts on the same pair get distinct payloads, so Android does
      // not collapse one tap onto the other
      expect(OpenRequest.encode('DOGEUSDT', '4h', 'a'),
          isNot(OpenRequest.encode('DOGEUSDT', '4h', 'b')));
    });

    test('garbage payloads open nothing rather than something wrong', () {
      for (final bad in [null, '', 'open', 'open||1h|x', 'nope|DOGEUSDT|4h|x',
                         'a.id']) {
        expect(OpenRequest.decode(bad), isNull, reason: '$bad');
      }
    });

    test('a logged entry remembers the timeframe it was logged from', () {
      final t = TradeEntry.create(
          symbol: 'DOGEUSDT', side: 'LONG', size: 1, entryPrice: 0.1,
          interval: '4h');
      expect(t.interval, '4h');
      expect(TradeEntry.fromJson(t.toJson()).interval, '4h');
      expect(t.closedAtPrice(0.12).interval, '4h');
      expect(t.withExtremes(high: 0.2, low: 0.05).interval, '4h');
      // an entry from before the field opens on the current timeframe
      final old = t.toJson()..remove('interval');
      expect(TradeEntry.fromJson(old).interval, isNull);
    });

    test('the market is decided by the symbol, not by where you were', () {
      expect(marketFor('DOGEUSDT'), MarketMode.crypto);
      expect(marketFor('1000pepeusdt'), MarketMode.crypto);
      expect(marketFor('AAPL'), MarketMode.stocks);
      expect(marketFor('MSTR'), MarketMode.stocks);
    });
  });

  // THE MODEL'S TIME LIMIT
  //
  // Every model answers "which barrier first, WITHIN N bars". An entry held
  // past N is a symmetric coin flip it never predicted, and on 1d -- where a
  // barrier is ~4% away and N is two days -- half of all entries were being
  // decided in that tail. The horizon table mirrors `BARRIERS` in
  // core/timeframes.py; a mismatch means the app closes trades on a window
  // the model was not trained on.
  group("the model's time limit", () {
    test('the horizon per timeframe matches core/timeframes.py BARRIERS', () {
      expect(TradeEntry.horizonOf('15m'), const Duration(hours: 2));
      expect(TradeEntry.horizonOf('1h'), const Duration(hours: 2));
      expect(TradeEntry.horizonOf('4h'), const Duration(hours: 64));
      expect(TradeEntry.horizonOf('1d'), const Duration(days: 10));
      // no longer trading timeframes: an old entry on them is never expired
      expect(TradeEntry.horizonOf('1m'), isNull);
      expect(TradeEntry.horizonOf('5m'), isNull);
      expect(TradeEntry.horizonOf(null), isNull);
    });

    TradeEntry aged(String iv, Duration age) => TradeEntry(
        id: 'x-$iv', symbol: 'BTCUSDT', side: 'LONG', size: 1,
        entryPrice: 100, openedAt: DateTime.now().subtract(age),
        takeProfit: 130, stopLoss: 90, interval: iv);

    test('an expired entry is ASKED about, not closed', () async {
      SharedPreferences.setMockInitialValues({});
      Trades.instance.resetForTest();
      await Trades.instance.add(aged('4h', const Duration(hours: 70)));
      final r = await Trades.instance.checkLive({'BTCUSDT': 103.0});
      expect(r.settled, isEmpty, reason: 'the window closing must not close it');
      final t = (await Trades.instance.load()).single;
      expect(t.isOpen, isTrue);
      expect(t.limitAskedAt, isNotNull, reason: 'the question was not sent');
      // and it is asked ONCE: the next tick past the window asks nothing
      final asked = t.limitAskedAt;
      await Trades.instance.checkLive({'BTCUSDT': 104.0});
      expect((await Trades.instance.load()).single.limitAskedAt, asked);
    });

    test('"close now" closes at the price given, marked TIME LIMIT', () async {
      SharedPreferences.setMockInitialValues({});
      Trades.instance.resetForTest();
      final e = aged('4h', const Duration(hours: 70));
      await Trades.instance.add(e);
      await Trades.instance.checkLive({'BTCUSDT': 103.0});
      final closed = await Trades.instance.answerLimit(e.id, close: true, price: 103.5);
      expect(closed, isNotNull);
      expect(closed!.closedBy, 'time_limit');
      expect(closed.closePrice, 103.5, reason: 'must close where price IS');
      expect(closed.pnlPct(null), closeTo(3.5, 1e-9));
    });

    test('"keep open" leaves it open and it is never asked again', () async {
      SharedPreferences.setMockInitialValues({});
      Trades.instance.resetForTest();
      final e = aged('4h', const Duration(hours: 70));
      await Trades.instance.add(e);
      await Trades.instance.checkLive({'BTCUSDT': 103.0});
      final kept = await Trades.instance.answerLimit(e.id, close: false);
      expect(kept!.isOpen, isTrue);
      expect(kept.keptPastLimit, isTrue);
      expect(kept.needsLimitQuestion(DateTime.now()), isFalse);
      // a level still closes a kept entry
      final r = await Trades.instance.checkLive({'BTCUSDT': 131.0});
      expect(r.settled.single.closedBy, 'take_profit');
    });

    test('silenced: the question is not asked and the grace clock never starts',
        () async {
      SharedPreferences.setMockInitialValues({});
      Trades.instance.resetForTest();
      Notifications.instance.silenced = true;
      try {
        await Trades.instance.add(aged('4h', const Duration(hours: 70)));
        final r = await Trades.instance.checkLive({'BTCUSDT': 103.0});
        expect(r.settled, isEmpty);
        final t = (await Trades.instance.load()).single;
        expect(t.isOpen, isTrue);
        expect(t.limitAskedAt, isNull,
            reason: 'a question nobody sees must not start the auto-close');
        // silence lifts: the next check asks, as it would have
        Notifications.instance.silenced = false;
        await Trades.instance.checkLive({'BTCUSDT': 103.0});
        expect((await Trades.instance.load()).single.limitAskedAt, isNotNull);
      } finally {
        Notifications.instance.silenced = false;
      }
    });

    test('a level on the same tick as expiry wins, and no question is sent',
        () async {
      SharedPreferences.setMockInitialValues({});
      Trades.instance.resetForTest();
      await Trades.instance.add(aged('4h', const Duration(hours: 70)));
      final r = await Trades.instance.checkLive({'BTCUSDT': 131.0});
      expect(r.settled.single.closedBy, 'take_profit');
      expect(r.settled.single.closePrice, 130.0);
      expect(r.settled.single.limitAskedAt, isNull);
    });

    test('the grace per timeframe is 5m / 10m / 1h, then it closes itself',
        () async {
      expect(TradeEntry.graceOf('1h'), const Duration(minutes: 5));
      expect(TradeEntry.graceOf('4h'), const Duration(minutes: 10));
      expect(TradeEntry.graceOf('1d'), const Duration(hours: 1));

      SharedPreferences.setMockInitialValues({});
      Trades.instance.resetForTest();
      // asked 4 minutes ago on 1h: still waiting for an answer
      var e = aged('1h', const Duration(hours: 3)).withLimitState(
          asked: DateTime.now().subtract(const Duration(minutes: 4)));
      await Trades.instance.add(e);
      var r = await Trades.instance.checkLive({'BTCUSDT': 103.0});
      expect(r.settled, isEmpty);
      expect((await Trades.instance.load()).single.isOpen, isTrue);

      // asked 6 minutes ago on 1h, no answer: closed at the tick, TIME LIMIT
      Trades.instance.resetForTest();
      SharedPreferences.setMockInitialValues({});
      e = aged('1h', const Duration(hours: 3)).withLimitState(
          asked: DateTime.now().subtract(const Duration(minutes: 6)));
      await Trades.instance.add(e);
      r = await Trades.instance.checkLive({'BTCUSDT': 103.0});
      expect(r.settled.single.closedBy, 'time_limit');
      expect(r.settled.single.closePrice, 103.0);

      // asked 8 minutes ago on 4h: inside its ten minutes, still open
      Trades.instance.resetForTest();
      SharedPreferences.setMockInitialValues({});
      e = aged('4h', const Duration(hours: 70)).withLimitState(
          asked: DateTime.now().subtract(const Duration(minutes: 8)));
      await Trades.instance.add(e);
      expect((await Trades.instance.checkLive({'BTCUSDT': 103.0})).settled,
          isEmpty);
    });

    test('"keep open" is final: the grace never closes a kept entry',
        () async {
      SharedPreferences.setMockInitialValues({});
      Trades.instance.resetForTest();
      final e = aged('1h', const Duration(days: 2)).withLimitState(
          asked: DateTime.now().subtract(const Duration(days: 1)), kept: true);
      await Trades.instance.add(e);
      final r = await Trades.instance.checkLive({'BTCUSDT': 103.0});
      expect(r.settled, isEmpty);
      expect((await Trades.instance.load()).single.isOpen, isTrue);
    });

    test('inside the window nothing is asked', () async {
      SharedPreferences.setMockInitialValues({});
      Trades.instance.resetForTest();
      await Trades.instance.add(aged('4h', const Duration(hours: 40)));
      await Trades.instance.checkLive({'BTCUSDT': 103.0});
      expect((await Trades.instance.load()).single.limitAskedAt, isNull);
    });

    test('the advice is the live call, read against the side', () {
      final long = aged('1h', Duration.zero);
      final short = TradeEntry(id: 's', symbol: 'BTCUSDT', side: 'SHORT',
          size: 1, entryPrice: 100, openedAt: DateTime.now(), interval: '1h');
      expect(LimitQuestion.advice(long, 'BUY'), contains('keeping is consistent'));
      expect(LimitQuestion.advice(long, 'SELL'), contains('closing is consistent'));
      expect(LimitQuestion.advice(short, 'SELL'), contains('keeping is consistent'));
      expect(LimitQuestion.advice(short, 'BUY'), contains('closing is consistent'));
      expect(LimitQuestion.advice(long, null), contains('no view'));
    });

    test('the question and its answers survive a write and a read', () {
      final t = TradeEntry.fromJson(aged('4h', const Duration(hours: 70))
          .withLimitState(asked: DateTime(2026, 9, 13), kept: true)
          .toJson());
      expect(t.limitAskedAt, DateTime(2026, 9, 13));
      expect(t.keptPastLimit, isTrue);
    });

    test('an entry with no timeframe recorded is never expired', () async {
      SharedPreferences.setMockInitialValues({});
      Trades.instance.resetForTest();
      await Trades.instance.add(TradeEntry(
          id: 'old', symbol: 'BTCUSDT', side: 'LONG', size: 1,
          entryPrice: 100,
          openedAt: DateTime.now().subtract(const Duration(days: 30)),
          takeProfit: 130, stopLoss: 90));
      expect((await Trades.instance.checkLive({'BTCUSDT': 103.0})).settled,
          isEmpty);
    });

    test('switched off in Preferences, nothing is asked', () async {
      SharedPreferences.setMockInitialValues({'trades.time_limit.v1': false});
      Trades.instance.resetForTest();
      await Trades.instance.add(aged('1h', const Duration(days: 3)));
      await Trades.instance.checkLive({'BTCUSDT': 103.0});
      final t = (await Trades.instance.load()).single;
      expect(t.isOpen, isTrue);
      expect(t.limitAskedAt, isNull);
    });

    test('the countdown reads in days and hours, and says when it has closed', () {
      final now = DateTime(2026, 9, 13, 12);
      TradeEntry at(Duration age) => TradeEntry(
          id: 'c', symbol: 'BTCUSDT', side: 'LONG', size: 1, entryPrice: 1,
          openedAt: now.subtract(age), interval: '4h');
      expect(JournalCard.timeLeftText(at(const Duration(hours: 6)), now),
          '2d 10h left');
      expect(JournalCard.timeLeftText(at(const Duration(hours: 63)), now),
          '1h 0m left');
      expect(JournalCard.timeLeftText(at(const Duration(hours: 65)), now),
          'window closed');
      // a daily entry has no window to count down
      final daily = TradeEntry(
          id: 'd', symbol: 'BTCUSDT', side: 'LONG', size: 1, entryPrice: 1,
          openedAt: now.subtract(const Duration(days: 30)), interval: '1d');
      expect(JournalCard.timeLeftText(daily, now), isNull);
    });
  });

  // GENERAL SETTINGS APPLY TO EVERY COIN; THE BELL OVERRIDES ONE COIN
  //
  // Absence is stored as absence. If setting an override copied the general
  // level into the map, changing the general setting later would silently
  // stop applying to that coin. The relay mirrors this map, so a coin that
  // buzzes on the lock screen is the same coin that buzzes in the app.
  group('silence everything', () {
    test('the setting round-trips and defaults off', () async {
      SharedPreferences.setMockInitialValues({});
      expect(await Settings.instance.silenced(), isFalse);
      await Settings.instance.saveSilenced(true);
      expect(await Settings.instance.silenced(), isTrue);
      await Settings.instance.saveSilenced(false);
      expect(await Settings.instance.silenced(), isFalse);
    });
  });

  group('per-coin signal strength', () {
    test('a coin with no override follows the general setting', () async {
      SharedPreferences.setMockInitialValues({});
      await Settings.instance.saveSensitivity('medium');
      expect(await Settings.instance.sensitivityFor('BTCUSDT'), 'medium');
      await Settings.instance.saveSensitivityOverride('BTCUSDT', 'small');
      expect(await Settings.instance.sensitivityFor('BTCUSDT'), 'small');
      expect(await Settings.instance.sensitivityFor('ETHUSDT'), 'medium');
      // the general setting still moves every coin without an override
      await Settings.instance.saveSensitivity('strong');
      expect(await Settings.instance.sensitivityFor('ETHUSDT'), 'strong');
      expect(await Settings.instance.sensitivityFor('BTCUSDT'), 'small');
      // clearing returns the coin to the general setting
      await Settings.instance.saveSensitivityOverride('BTCUSDT', null);
      expect(await Settings.instance.sensitivityFor('BTCUSDT'), 'strong');
      expect(await Settings.instance.sensitivityOverrides(), isEmpty);
    });

    test('the delivery filter uses the override for that coin only', () {
      Alert small(String sym) => Alert.fromJson({
            'id': 'a-$sym', 'kind': 'signal', 'symbol': sym,
            'interval': '1h', 'strength': 'small',
            'title': '$sym: 1h; FLAT \u2192 BUY', 'body': '',
            'severity': 'high', 'at': DateTime.now().toIso8601String(),
            'detected_at': DateTime.now().toIso8601String(),
            'extra': {'from': 'FLAT', 'to': 'BUY'},
          });
      final out = selectDeliverable(
        [small('BTCUSDT'), small('ETHUSDT')],
        sensitivity: 'strong',
        overrides: const {'BTCUSDT': 'small'},
        isMuted: (_, _) => false,
      );
      expect(out.map((a) => a.symbol), ['BTCUSDT'],
          reason: 'BTC allowed by its override; ETH held to the general');
    });
  });

  group('smart money', () {
    final payload = {
      'available': true,
      'tracked': 25,
      'selected_at': '2026-09-16T08:00:00+00:00',
      'polled_at': '2026-09-16T12:00:00+00:00',
      'note': 'information, not a call',
      'consensus': {
        'symbol': 'BTCUSDT', 'tracked': 25, 'long': 7, 'short': 2,
        'long_notional': 12300000.0, 'short_notional': 1100000.0,
        'holders': [
          {'address': '0xe867fbdad3291530e41530301ecb77693850c78e',
           'short': '0xe867…c78e', 'name': '', 'side': 'LONG',
           'notional': 2100000.0, 'entry': 63120.0, 'leverage': 10.0,
           'upnl': 1500.0, 'win_rate': 0.69, 'pnl_30d': 22100000.0},
          {'address': '0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
           'short': '0xbbbb…bbbb', 'name': 'maomao', 'side': 'SHORT',
           'notional': 840000.0, 'entry': 63500.0, 'leverage': 3.0,
           'upnl': -200.0, 'win_rate': 0.62, 'pnl_30d': 1000000.0},
        ],
      },
      'events': [
        {'id': 'e1', 'kind': 'opened',
         'address': '0xe867fbdad3291530e41530301ecb77693850c78e',
         'coin': 'BTC', 'symbol': 'BTCUSDT', 'side': 'LONG',
         'size': 33.0, 'notional': 2100000.0, 'entry': 63120.0,
         'leverage': 10.0, 'at': '2026-09-16T11:40:00+00:00',
         'price_at': 63130.0,
         'trader': {'win_rate': 0.69, 'pnl_30d': 22100000.0,
                    'closed_trades': 873, 'display_name': ''}},
      ],
      'traders': [
        {'address': '0xe867fbdad3291530e41530301ecb77693850c78e',
         'short': '0xe867…c78e', 'name': '', 'score': 3.1,
         'win_rate': 0.69, 'closed_trades': 873, 'profit_factor': 2.4,
         'pnl_30d': 22100000.0, 'roi_30d': 0.35, 'account_value': 85100000.0,
         'weeks_positive': 4, 'weeks_covered': 4, 'coins': ['BTCUSDT'],
         'open': ['BTC']},
      ],
    };

    test('the payload parses, with the trader named by address when unnamed', () {
      final sm = SmartMoney.fromJson(payload);
      expect(sm.available, isTrue);
      expect(sm.tracked, 25);
      expect(sm.consensus!.holding, 9);
      expect(sm.consensus!.holders.first.who, '0xe867…c78e');
      expect(sm.consensus!.holders[1].who, 'maomao');
      expect(sm.events.single.who, '0xe867…c78e');
      expect(sm.events.single.winRate, closeTo(0.69, 1e-9));
      expect(sm.events.single.priceAt, 63130.0);
      expect(sm.traders.single.closedTrades, 873);
    });

    test('money reads the way the notification writes it', () {
      expect(usdCompact(2100000), r'$2.1M');
      expect(usdCompact(840000), r'$840k');
      expect(usdCompact(12500), r'$13k');
      expect(usdCompact(950), r'$950');
      final now = DateTime.utc(2026, 9, 16, 12);
      expect(agoText(now.subtract(const Duration(minutes: 20)), now: now), '20m ago');
      expect(agoText(now.subtract(const Duration(hours: 5)), now: now), '5h ago');
      expect(agoText(now.subtract(const Duration(days: 3)), now: now), '3d ago');
    });

    testWidgets('the panel shows the split, the holders, the moves and the label',
        (t) async {
      final sm = SmartMoney.fromJson(payload);
      await t.pumpWidget(MaterialApp(
        home: Scaffold(
          body: SingleChildScrollView(
            child: SmartMoneyPanel(data: sm, now: DateTime.utc(2026, 9, 16, 12)),
          ),
        ),
      ));
      await t.pumpAndSettle();
      expect(find.text('SMART MONEY'), findsOneWidget);
      expect(find.text('25 followed'), findsOneWidget);
      expect(find.text(r'9 of 25 hold it · 7 long $12.3M · 2 short $1.1M'),
          findsOneWidget);
      expect(find.text('0xe867…c78e'), findsOneWidget);
      expect(find.text('maomao'), findsOneWidget);
      expect(find.text('69% win rate'), findsOneWidget);
      expect(find.textContaining('opened long'), findsOneWidget);
      expect(find.text('20m ago'), findsOneWidget);
      expect(find.textContaining('not a call'), findsOneWidget);
    });

    testWidgets('nothing followed draws nothing', (t) async {
      final sm = SmartMoney.fromJson({'available': true, 'tracked': 0});
      await t.pumpWidget(MaterialApp(home: SmartMoneyPanel(data: sm)));
      expect(find.text('SMART MONEY'), findsNothing);
    });

    test('the kind can be silenced from the phone like any other', () {
      Alert smart(String sym) => Alert.fromJson({
            'id': 's-$sym', 'kind': 'smart', 'symbol': sym, 'interval': '',
            'title': '$sym: 0xe867…c78e opened LONG', 'body': '',
            'severity': 'medium', 'at': DateTime.now().toIso8601String(),
            'detected_at': DateTime.now().toIso8601String(),
            'extra': {'event': 'opened', 'side': 'LONG'},
          });
      final kept = selectDeliverable([smart('BTCUSDT'), smart('ETHUSDT')],
          sensitivity: 'strong',
          isMuted: (s, _) => s == 'ETHUSDT',
          isKindMuted: (_) => false);
      expect(kept.map((a) => a.symbol), ['BTCUSDT'],
          reason: 'a coin mute applies; no strength gate applies');
      final none = selectDeliverable([smart('BTCUSDT')],
          sensitivity: 'strong',
          isMuted: (_, _) => false,
          isKindMuted: (k) => k == 'smart');
      expect(none, isEmpty);
    });
  });

  group('the calls list states the payoff, not an EV', () {
    Future<void> row(WidgetTester t, Map<String, dynamic> sig) async {
      await t.pumpWidget(MaterialApp(
          home: Scaffold(
              body: SingleChildScrollView(
                  child: SignalsPanel(
                      data: LiveSignals.fromJson({
                        'signals': [sig],
                        'watched_symbols': 1,
                        'intervals': ['4h'],
                      }),
                      onOpen: (_) {})))));
      await t.pumpAndSettle();
    }

    testWidgets('a pooled 4h call shows its target and stop distances',
        (t) async {
      await row(t, {
        'symbol': 'BTCUSDT', 'interval': '4h', 'action': 'BUY',
        'strength': 'strong', 'detail': '', 'side': 'LONG', 'ev': null,
        'price': 100.0, 'take_profit': 101.18, 'stop_loss': 92.95,
      });
      expect(find.text('TP +1.18% · SL −7.05%'), findsOneWidget);
      expect(find.textContaining('EV'), findsNothing);
    });

    testWidgets('a short reads its target below the price', (t) async {
      await row(t, {
        'symbol': 'ETHUSDT', 'interval': '4h', 'action': 'SELL',
        'strength': 'strong', 'detail': '', 'side': 'SHORT',
        'price': 200.0, 'take_profit': 196.0, 'stop_loss': 210.0,
      });
      expect(find.text('TP −2.00% · SL +5.00%'), findsOneWidget);
    });

    testWidgets('an older server without levels still shows its EV',
        (t) async {
      await row(t, {
        'symbol': 'BTCUSDT', 'interval': '4h', 'action': 'BUY',
        'strength': 'strong', 'detail': '', 'side': 'LONG', 'ev': 0.5,
      });
      expect(find.text('EV +0.50%'), findsOneWidget);
    });
  });

  group('IN TRADE is per timeframe', () {
    LiveSignals two() => LiveSignals.fromJson({
          'signals': [
            {'symbol': 'BTCUSDT', 'interval': '1h', 'action': 'BUY',
             'strength': 'strong', 'detail': '', 'side': 'LONG', 'ev': 0.3},
            {'symbol': 'BTCUSDT', 'interval': '4h', 'action': 'BUY',
             'strength': 'strong', 'detail': '', 'side': 'LONG', 'ev': 0.5},
          ],
          'watched_symbols': 1,
          'intervals': ['1h', '4h'],
        });

    Future<int> tags(WidgetTester t, Set<String> held) async {
      await t.pumpWidget(MaterialApp(
          home: Scaffold(
              body: SingleChildScrollView(
                  child: SignalsPanel(
                      data: two(), onOpen: (_) {}, held: held)))));
      await t.pumpAndSettle();
      return find.text('IN TRADE').evaluate().length;
    }

    testWidgets('a 4h entry tags the 4h row only', (t) async {
      expect(await tags(t, {SignalsPanel.heldKey('BTCUSDT', '4h')}), 1);
    });

    testWidgets('an entry with no timeframe tags every row of the coin',
        (t) async {
      expect(await tags(t, {SignalsPanel.heldKey('BTCUSDT', null)}), 2);
    });

    testWidgets('nothing held, nothing tagged', (t) async {
      expect(await tags(t, const {}), 0);
    });

    test('the key is what the market page builds from an entry', () {
      expect(SignalsPanel.heldKey('BTCUSDT', '4h'), 'BTCUSDT:4h');
      expect(SignalsPanel.heldKey('BTCUSDT', ''), 'BTCUSDT');
      expect(SignalsPanel.heldKey('BTCUSDT', null), 'BTCUSDT');
    });
  });

  group('daily entries are trailed, not timed', () {
    TradeEntry daily(String side, {double? sl, double? tp}) => TradeEntry(
        id: 'd-$side', symbol: 'BTCUSDT', side: side, size: 1, entryPrice: 100,
        openedAt: DateTime.now().subtract(const Duration(days: 30)),
        takeProfit: tp, stopLoss: sl, interval: '1d');

    test('a daily entry has no time limit and is never asked', () {
      final e = daily('LONG', sl: 90);
      expect(e.trailed, isTrue);
      expect(e.timeLimit, isNull);
      expect(e.expiredAt(DateTime.now()), isFalse);
      expect(e.needsLimitQuestion(DateTime.now()), isFalse);
      // other timeframes keep their clock
      final h4 = TradeEntry(
          id: 'x', symbol: 'BTCUSDT', side: 'LONG', size: 1, entryPrice: 100,
          openedAt: DateTime.now().subtract(const Duration(hours: 70)),
          stopLoss: 90, interval: '4h');
      expect(h4.trailed, isFalse);
      expect(h4.expiredAt(DateTime.now()), isTrue);
    });

    test('the trailed stop only tightens, and records when it moved', () {
      final at = DateTime.utc(2026, 9, 20);
      final e = daily('LONG', sl: 90);
      final up = e.withTrailedStop(95, at);
      expect(up.stopLoss, 95);
      expect(up.stopMovedAt, at);
      expect(identical(e.withTrailedStop(85, at), e), isTrue,
          reason: 'a looser stop is refused');
      expect(identical(up.withTrailedStop(95, at), up), isTrue,
          reason: 'the same stop is not a move');
      final sh = daily('SHORT', sl: 110);
      expect(sh.withTrailedStop(105, at).stopLoss, 105);
      expect(identical(sh.withTrailedStop(115, at), sh), isTrue);
      // an entry logged without a stop takes the first one
      expect(daily('LONG').withTrailedStop(88, at).stopLoss, 88);
    });

    test('the moved stop survives a round trip through storage', () {
      final at = DateTime.utc(2026, 9, 20, 0, 0);
      final e = daily('LONG', sl: 90).withTrailedStop(95, at);
      final back = TradeEntry.fromJson(json.decode(json.encode(e.toJson())));
      expect(back.stopLoss, 95);
      expect(back.stopMovedAt, at);
      expect(back.trailed, isTrue);
    });

    test('the instructions carry the swing rules', () {
      final titles = howToSteps.map((h) => h.title).join(' | ');
      expect(titles, contains('200-day average'));
      expect(titles, contains('no time limit'));
      expect(howToSteps.length, greaterThanOrEqualTo(10));
    });
  });

  group('what to do with the entry you hold', () {
    test('the advice mirrors the relay, case by case', () {
      // a call on the entry's own side: nothing to do
      expect(adviceLine(side: 'LONG', entryInterval: '4h', alertInterval: '4h', to: 'BUY'),
          startsWith('Stay in'));
      // withdrawn: stay, measured
      expect(adviceLine(side: 'LONG', entryInterval: '4h', alertInterval: '4h', to: 'FLAT'),
          startsWith('Stay — your levels decide'));
      // the opposite call: close, with the measured number and the reverse
      final sell = adviceLine(side: 'LONG', entryInterval: '4h', alertInterval: '4h', to: 'SELL')!;
      expect(sell, startsWith('Close it'));
      expect(sell, contains('fresh SHORT entry'));
      expect(adviceLine(side: 'SHORT', entryInterval: '4h', alertInterval: '4h', to: 'BUY'),
          contains('fresh LONG entry'));
      // daily is trailed
      expect(adviceLine(side: 'LONG', entryInterval: '1d', alertInterval: '1d', to: 'FLAT'),
          contains('trailing stop'));
      expect(adviceLine(side: 'LONG', entryInterval: '1d', alertInterval: '1d', to: 'SELL'),
          contains('not measured'));
      // another timeframe's call is context
      expect(adviceLine(side: 'LONG', entryInterval: '1d', alertInterval: '4h', to: 'SELL'),
          startsWith('Your entry is on 1d; this call is on 4h'));
      expect(adviceLine(side: 'LONG', entryInterval: '4h', alertInterval: '1d', to: 'SELL'),
          contains('its 4h levels decide'));
      // an old entry with no timeframe is judged on the call's own
      expect(adviceLine(side: 'LONG', entryInterval: null, alertInterval: '4h', to: 'SELL'),
          startsWith('Close it'));
      expect(adviceLine(side: 'LONG', entryInterval: '4h', alertInterval: '4h', to: 'STALE'), isNull);
    });

    test('the held line carries the advice on its second line', () async {
      SharedPreferences.setMockInitialValues({});
      Trades.instance.resetForTest();
      await Trades.instance.add(TradeEntry(
          id: 'h', symbol: 'ETHUSDT', side: 'SHORT', size: 1, entryPrice: 2500,
          openedAt: DateTime.now(), stopLoss: 2600, interval: '4h'));
      final line = await heldLine('ETHUSDT', alertInterval: '4h', to: 'BUY');
      final parts = line!.split('\n');
      expect(parts[0], 'Open entry: SHORT @ 2,500.00');
      expect(parts[1], startsWith('Close it'));
      expect(await heldLine('BTCUSDT', alertInterval: '4h', to: 'BUY'), isNull);
    });

    test('the relay is told the timeframe of each open entry', () async {
      SharedPreferences.setMockInitialValues({});
      Trades.instance.resetForTest();
      await Trades.instance.add(TradeEntry(
          id: 'p', symbol: 'BTCUSDT', side: 'LONG', size: 1, entryPrice: 80000,
          openedAt: DateTime.now(), stopLoss: 77000, interval: '1d'));
      final pos = await PushDelivery.instance.openPositions();
      expect(pos.single['interval'], '1d');
    });
  });

  group('smart money confirms a call', () {
    test('the note parses and is empty when absent', () {
      final r = Recommendation.fromJson({'action': 'BUY', 'tone': 'up', 'detail': 'd',
          'strength': 'medium',
          'smart_note': 'Smart money agrees: 2 followed traders opened LONG in the last 24h.'});
      expect(r.smartNote, startsWith('Smart money agrees'));
      expect(Recommendation.fromJson({'action': 'FLAT', 'detail': ''}).smartNote, '');
    });

    test('the instructions explain confluence', () {
      expect(howToSteps.map((h) => h.title).join(' | '), contains('confirms, it does not decide'));
    });
  });

  group('4h calls enter with a resting order', () {
    Map<String, dynamic> payload({String state = 'open', String action = 'BUY'}) => {
          'symbol': 'BTCUSDT', 'pair': 'BTC / USDT', 'interval': '4h', 'htf': '1d',
          'timeframes': [], 'generated_at': '2026-09-23T00:00:00+00:00',
          'last_closed_bar': '2026-09-23T00:00:00+00:00', 'stale': false,
          'status': {'active': true, 'label': 'WATCHING', 'detail': '', 'trades': false},
          'price': 86367.4, 'close_price': 86367.4, 'live': null, 'indicators': [],
          'analyses': [],
          'recommendation': {
            'action': action, 'tone': action == 'BUY' ? 'up' : 'flat', 'detail': 'd',
            'strength': 'strong', 'geometry': 'structure', 'size_pct': 5.0,
            'order': {'state': state, 'limit': 85900.0, 'stop': 79800.0,
                      'target': 87385.1, 'fill': state == 'filled' ? 85900.0 : null,
                      'placed_at': '2026-09-23T00:00:00+00:00',
                      'valid_until': '2026-09-23T16:00:00+00:00',
                      'hold_until': '2026-09-25T16:00:00+00:00', 'level': 'strong'},
          },
          'levels': {'current': 86367.4, 'window_bars': 16, 'p_up': 0.7,
                     'take_profit': 87385.1, 'stop_loss': 79800.0, 'side': 'LONG',
                     'anchor': 85900.0, 'geometry': 'structure',
                     'entry_limit': state == 'open' ? 85900.0 : null},
          'calibration_note': 'n',
        };

    test('the order parses, and a filled one is a trade', () {
      final d = Dashboard.fromJson(payload());
      expect(d.entryLimit, 85900.0);
      expect(d.recommendation.order!.isOpen, isTrue);
      expect(d.recommendation.order!.validUntil, DateTime.utc(2026, 9, 23, 16));
      expect(d.recommendation.inTrade, isFalse);
      final t = Dashboard.fromJson(payload(state: 'filled', action: 'WAIT'));
      expect(t.recommendation.inTrade, isTrue);
      expect(t.entryLimit, isNull);
    });

    test('an unfilled order is not over when the price runs past its target', () {
      final d = Dashboard.fromJson(payload());
      expect(d.outcome(88000.0), '', reason: 'the order stays good for its window');
      expect(d.outcome(79000.0), 'stop', reason: 'the stop sits beyond the order');
    });

    testWidgets('the levels panel leads with the order', (t) async {
      await t.pumpWidget(MaterialApp(
          home: Scaffold(
              body: SingleChildScrollView(
                  child: LevelsPanel(
                      price: 86367.4, takeProfit: 87385.1, stopLoss: 79800.0,
                      side: 'LONG', fixed: true, entryLimit: 85900.0,
                      entryUntil: DateTime.utc(2026, 9, 23, 16))))));
      expect(find.text('Entry (buy limit)'), findsOneWidget);
      expect(find.textContaining('good until Wed 16:00 UTC'), findsOneWidget);
    });

    testWidgets('the calls list leads with the order and measures from it', (t) async {
      await t.pumpWidget(MaterialApp(
          home: Scaffold(
              body: SingleChildScrollView(
                  child: SignalsPanel(
                      data: LiveSignals.fromJson({
                        'signals': [
                          {'symbol': 'BTCUSDT', 'interval': '4h', 'action': 'BUY',
                           'strength': 'strong', 'detail': '', 'side': 'LONG',
                           'price': 101.0, 'entry_limit': 100.0,
                           'take_profit': 102.0, 'stop_loss': 95.0},
                        ],
                        'watched_symbols': 1, 'intervals': ['4h'],
                      }),
                      onOpen: (_) {})))));
      await t.pumpAndSettle();
      expect(find.text(r'Limit $100.00 · TP +2.00% · SL −5.00%'), findsOneWidget);
    });

    test('the scale-out parses: the halfway price, the part, and taken', () {
      final j = payload(state: 'filled', action: 'WAIT');
      (j['recommendation'] as Map)['order'] = {
        ...((j['recommendation'] as Map)['order'] as Map),
        'scale_part': 1 / 3, 'scale_price': 86642.5, 'taken': false,
      };
      (j['levels'] as Map)['scale_out'] = 86642.5;
      final d = Dashboard.fromJson(j);
      final o = d.recommendation.order!;
      expect(o.scalePrice, 86642.5);
      expect(o.partWords, 'a third');
      expect(o.partShort, '⅓');
      expect(o.taken, isFalse);
      expect(d.scaleOut, 86642.5);
    });

    testWidgets('the levels panel names the halfway price, then says it is done', (t) async {
      await t.pumpWidget(MaterialApp(
          home: Scaffold(
              body: SingleChildScrollView(
                  child: LevelsPanel(
                      price: 86367.4, takeProfit: 87385.1, stopLoss: 79800.0,
                      side: 'LONG', fixed: true, scaleOut: 86642.5,
                      scalePart: 'a third')))));
      expect(find.text('Halfway: take a third off'), findsOneWidget);
      expect(find.textContaining('move your stop to your entry'), findsOneWidget);
      await t.pumpWidget(MaterialApp(
          home: Scaffold(
              body: SingleChildScrollView(
                  child: LevelsPanel(
                      price: 86700.0, takeProfit: 87385.1, stopLoss: 85900.0,
                      side: 'LONG', fixed: true, scalePart: 'a third',
                      scaleTaken: true)))));
      expect(find.text('Halfway — a third taken'), findsOneWidget);
      expect(find.textContaining('can no longer lose'), findsOneWidget);
    });

    testWidgets('the calls list shows where the third comes off', (t) async {
      await t.pumpWidget(MaterialApp(
          home: Scaffold(
              body: SingleChildScrollView(
                  child: SignalsPanel(
                      data: LiveSignals.fromJson({
                        'signals': [
                          {'symbol': 'BTCUSDT', 'interval': '4h', 'action': 'BUY',
                           'strength': 'strong', 'detail': '', 'side': 'LONG',
                           'price': 101.0, 'entry_limit': 100.0,
                           'take_profit': 102.0, 'stop_loss': 95.0,
                           'order': {'state': 'open', 'limit': 100.0, 'target': 102.0,
                                     'stop': 95.0, 'scale_part': 1 / 3, 'scale_price': 101.0}},
                        ],
                        'watched_symbols': 1, 'intervals': ['4h'],
                      }),
                      onOpen: (_) {})))));
      await t.pumpAndSettle();
      expect(find.text(r'Limit $100.00 · TP +2.00% · ⅓ at +1.00% · SL −5.00%'), findsOneWidget);
    });

    test('the instructions explain the order and the rotation', () {
      final titles = howToSteps.map((h) => h.title).join(' | ');
      expect(titles, contains('limit order'));
      expect(titles, contains('momentum rotation'));
    });
  });

  group('the momentum rotation', () {
    Map<String, dynamic> payload({bool available = true}) => {
          'available': available, 'week_start': '2026-09-21T00:00:00+00:00',
          'next_rebalance': '2026-09-28T00:00:00+00:00', 'universe': 15,
          'lookback_days': 30, 'week_pct': 1.25, 'measured': 'Sharpe 0.62-1.31',
          'longs': [
            {'symbol': 'ZECUSDT', 'ret_30d': 41.2, 'week_pct': 3.1, 'rank': 1},
            {'symbol': 'HYPEUSDT', 'ret_30d': 18.4, 'week_pct': -0.5, 'rank': 2},
            {'symbol': 'SOLUSDT', 'ret_30d': 9.9, 'week_pct': 1.0, 'rank': 3},
          ],
          'shorts': [
            {'symbol': 'ARBUSDT', 'ret_30d': -21.5, 'week_pct': -2.0, 'rank': 15},
            {'symbol': 'UNIUSDT', 'ret_30d': -12.0, 'week_pct': 0.4, 'rank': 14},
            {'symbol': 'NEARUSDT', 'ret_30d': -8.3, 'week_pct': 0.1, 'rank': 13},
          ],
          'ranking': [], 'live_ranking': [],
        };

    testWidgets('the picks, the week and when it turns over', (t) async {
      String? opened;
      await t.pumpWidget(MaterialApp(
          home: Scaffold(
              body: SingleChildScrollView(
                  child: MomentumPanel(
                      data: Momentum.fromJson(payload()),
                      onOpen: (s) => opened = s)))));
      expect(find.text('ZEC +41%'), findsOneWidget);
      expect(find.text('ARB −22%'), findsOneWidget);
      expect(find.textContaining('Week of 21 Sep'), findsOneWidget);
      expect(find.textContaining('rebalances 28 Sep 00:00 UTC'), findsOneWidget);
      expect(find.text('+1.25% so far'), findsOneWidget);
      await t.tap(find.text('ZEC +41%'));
      expect(opened, 'ZECUSDT');
    });

    testWidgets('too few coins says so instead of ranking', (t) async {
      await t.pumpWidget(MaterialApp(
          home: Scaffold(
              body: SingleChildScrollView(
                  child: MomentumPanel(data: Momentum.fromJson(payload(available: false)))))));
      expect(find.textContaining('needs at least eight'), findsOneWidget);
    });

    testWidgets('the new rule: ret_pct, the universe in words, and a coin '
        'the app does not follow opens nothing', (t) async {
      final j = payload()
        ..['lookback_days'] = 15
        ..['universe'] = 30
        ..['universe_rule'] = 'the 30 most-traded Binance perpetuals'
        ..['signal'] = '15-day momentum and a week of net taker buying, ranks averaged'
        ..['longs'] = [
          {'symbol': 'AKEUSDT', 'ret_pct': 214.6, 'week_pct': -3.0, 'rank': 1, 'followed': false},
          {'symbol': 'NEARUSDT', 'ret_pct': 90.2, 'week_pct': 1.0, 'rank': 2, 'followed': true},
        ];
      String? opened;
      await t.pumpWidget(MaterialApp(
          home: Scaffold(
              body: SingleChildScrollView(
                  child: MomentumPanel(
                      data: Momentum.fromJson(j), onOpen: (s) => opened = s)))));
      expect(find.text('AKE +215%'), findsOneWidget);
      expect(find.textContaining('Ranked on 15-day momentum and a week of net taker buying, '
          'ranks averaged, across the 30 most-traded Binance perpetuals.'), findsOneWidget);
      await t.tap(find.text('AKE +215%'));
      expect(opened, isNull);
      await t.tap(find.text('NEAR +90%'));
      expect(opened, 'NEARUSDT');
    });
  });

  group('the size in dollars', () {
    Future<void> pump(WidgetTester t, {double? entry, double live = 86000.0}) async {
      SharedPreferences.setMockInitialValues({});
      await t.pumpWidget(MaterialApp(
          home: Scaffold(
              body: SingleChildScrollView(
                  child: LogEntryCard(
                      symbol: 'BTCUSDT', short: 'BTC', interval: '4h',
                      livePrice: live, suggestedSide: 'LONG',
                      suggestedEntry: entry)))));
      await t.pumpAndSettle();
    }

    TextField sizeField(WidgetTester t) => t.widget<TextField>(find.byType(TextField).first);

    testWidgets('the buttons are dollars, and fill in the coins', (t) async {
      await pump(t, entry: 80000.0);
      expect(find.text('25%'), findsNothing);
      for (final l in [r'$100', r'$250', r'$1000', 'Your amount']) {
        expect(find.text(l), findsOneWidget);
      }
      await t.tap(find.text(r'$100'));
      await t.pump();
      expect(sizeField(t).controller!.text, '0.00125');       // 100 / 80,000
      await t.tap(find.text(r'$1000'));
      await t.pump();
      expect(sizeField(t).controller!.text, '0.0125');
    });

    testWidgets('the coins follow the entry price, until you type them', (t) async {
      await pump(t, entry: 80000.0);
      await t.tap(find.text(r'$250'));
      await t.pump();
      expect(sizeField(t).controller!.text, '0.003125');
      // the entry price is the second field
      await t.enterText(find.byType(TextField).at(1), '100000');
      await t.pump();
      expect(sizeField(t).controller!.text, '0.0025');         // still $250
      await t.enterText(find.byType(TextField).first, '0.01');
      await t.pump();
      await t.enterText(find.byType(TextField).at(1), '50000');
      await t.pump();
      expect(sizeField(t).controller!.text, '0.01', reason: 'typed coins are left alone');
    });

    testWidgets('your amount asks once and is remembered', (t) async {
      await pump(t, entry: 0.25);
      await t.tap(find.text('Your amount'));
      await t.pumpAndSettle();
      await t.enterText(find.byType(TextField).last, '500');
      await t.tap(find.text('Use it'));
      await t.pumpAndSettle();
      expect(sizeField(t).controller!.text, '2000');           // 500 / 0.25
      expect(find.text(r'$500'), findsOneWidget);
      final prefs = await SharedPreferences.getInstance();
      expect(prefs.getDouble('trades.custom_usd.v1'), 500.0);
    });
  });

  group('the live record', () {
    Map<String, dynamic> payload({int closed = 5}) => {
          'since': '2026-09-23T08:00:00+00:00', 'cost_pct': 0.1,
          'levels': {
            'strong': {'orders': 9, 'filled': closed + 1, 'expired': 2, 'open_orders': 1,
                       'in_trade': 1, 'closed': closed, 'targets': closed == 0 ? 0 : 3,
                       'stops': closed == 0 ? 0 : 2, 'timeouts': 0, 'wins': closed == 0 ? 0 : 3,
                       'win_rate': closed == 0 ? null : 0.6,
                       'avg_net_pct': closed == 0 ? null : 0.42,
                       'sum_net_pct': closed == 0 ? 0.0 : 2.1},
          },
          'recent': {
            'strong': [
              {'symbol': 'DOGEUSDT', 'side': 'long', 'state': 'target', 'fill': 0.098,
               'exit': 0.103, 'net_pct': 5.0, 'closed_at': '2026-09-24T12:00:00+00:00'},
            ],
          },
          'expected': {
            'levels': {
              'strong': {'win_rate': 0.608, 'avg_net_pct': 0.44, 'hold_win_rate': 0.58,
                         'hold_avg_net_pct': 0.16, 'trades_per_month': 66},
            },
            'note': 'three at a time did better',
          },
          'benchmarks': [
            {'what': 'ML bitcoin forecasts, 1-60 min', 'figure': '51-56% right',
             'source': 'Jaquart et al. 2021'},
          ],
        };

    testWidgets('the level you follow, its sample, and what was expected', (t) async {
      await t.pumpWidget(MaterialApp(
          home: Scaffold(
              body: SingleChildScrollView(
                  child: RecordPanel(data: LiveRecord.fromJson(payload()), level: 'strong')))));
      expect(find.textContaining('Since 23 Sep · your setting: strong'), findsOneWidget);
      expect(find.text('60%'), findsOneWidget);
      expect(find.text('3 of 5'), findsOneWidget);
      expect(find.text('+0.42%'), findsOneWidget);
      expect(find.text('+2.10%'), findsOneWidget);
      expect(find.textContaining('Too few to judge yet'), findsOneWidget);
      expect(find.textContaining('Expected from the walk-forward: 58–61% won'), findsOneWidget);
      expect(find.text('DOGE'), findsOneWidget);
    });

    testWidgets('nothing closed yet says so, with what is open', (t) async {
      await t.pumpWidget(MaterialApp(
          home: Scaffold(
              body: SingleChildScrollView(
                  child: RecordPanel(data: LiveRecord.fromJson(payload(closed: 0)), level: 'strong')))));
      expect(find.textContaining('No trade has closed yet.'), findsOneWidget);
      expect(find.textContaining('1 in a trade, 1 waiting to fill'), findsOneWidget);
    });

    test('the instructions carry the record', () {
      final titles = howToSteps.map((h) => h.title).join(' | ');
      expect(titles, contains('live record'));
    });
  });

  group('structure levels are prices, not distances', () {
    Map<String, dynamic> payload(String geometry) => {
          'symbol': 'BTCUSDT', 'pair': 'BTC / USDT', 'interval': '4h', 'htf': '1d',
          'timeframes': [], 'generated_at': '2026-09-21T00:00:00+00:00',
          'last_closed_bar': '2026-09-21T00:00:00+00:00', 'stale': false,
          'status': {'active': true, 'label': 'WATCHING', 'detail': '', 'trades': false},
          'price': 85346.0, 'close_price': 85346.0, 'live': null, 'indicators': [],
          'analyses': [],
          'recommendation': {'action': 'BUY', 'tone': 'up', 'detail': 'd', 'strength': 'medium',
                             'geometry': geometry},
          'levels': {'current': 85346.0, 'window_bars': 16, 'p_up': 0.6,
                     'take_profit': 88527.0, 'stop_loss': 77509.0, 'side': 'LONG',
                     'anchor': 85346.0, 'tp_pct': 3.73, 'sl_pct': 9.18,
                     'tp_offset_pct': 3.73, 'sl_offset_pct': -9.18, 'geometry': geometry},
          'calibration_note': 'n',
        };

    test('a structure level stays where the chart put it as the price moves', () {
      final d = Dashboard.fromJson(payload('structure'));
      expect(d.levelsAreFixed, isTrue);
      expect(d.liveTakeProfit(87000.0), 88527.0);
      expect(d.liveStopLoss(87000.0), 77509.0);
    });

    test('an ATR level still follows the price', () {
      final d = Dashboard.fromJson(payload('atr'));
      expect(d.levelsAreFixed, isFalse);
      expect(d.liveStopLoss(87000.0), closeTo(87000.0 * (1 - 0.0918), 1e-6));
    });

    // DOGE, 2026-09-21: +13% on the day, a +1.2% target gone in the first
    // hour of the bar, and the card still said BUY with the target printed
    // below the price. The trade the model scored was over.
    test('a call whose target the price is past is settled, not entered', () {
      final d = Dashboard.fromJson(payload('structure'));
      expect(d.outcome(87000.0), '');            // open: between the levels
      expect(d.outcome(88527.0), 'target');      // at the target
      expect(d.outcome(90000.0), 'target');      // past it
      expect(d.outcome(77000.0), 'stop');        // through the stop
      expect(d.outcome(null), '');               // no live price: nothing to say
    });

    test('a short settles the other way round', () {
      final j = payload('structure');
      j['recommendation'] = {'action': 'SELL', 'tone': 'down', 'detail': 'd',
                             'strength': 'medium', 'geometry': 'structure'};
      j['levels'] = {...(j['levels'] as Map<String, dynamic>),
                     'side': 'SHORT', 'take_profit': 77509.0, 'stop_loss': 88527.0};
      final d = Dashboard.fromJson(j);
      expect(d.outcome(80000.0), '');
      expect(d.outcome(77000.0), 'target');
      expect(d.outcome(89000.0), 'stop');
    });

    test('ATR levels, FLAT calls and the server reading', () {
      // ATR levels re-anchor to the price and are never behind it
      expect(Dashboard.fromJson(payload('atr')).outcome(90000.0), '');
      // no call, nothing to settle
      final flat = payload('structure');
      flat['recommendation'] = {'action': 'FLAT', 'tone': 'flat', 'detail': 'd'};
      expect(Dashboard.fromJson(flat).outcome(90000.0), '');
      // the server has already settled it: its reading wins whatever the price does
      final settled = payload('structure');
      settled['recommendation'] = {'action': 'WAIT', 'tone': 'flat', 'detail': 'gone',
                                   'called': 'BUY', 'outcome': 'target'};
      settled['levels'] = {...(settled['levels'] as Map<String, dynamic>), 'reached': 'target'};
      final d = Dashboard.fromJson(settled);
      expect(d.outcome(80000.0), 'target');
      expect(d.recommendation.called, 'BUY');
    });

    testWidgets('the levels panel says a reached target is reached', (t) async {
      await t.pumpWidget(MaterialApp(home: Scaffold(body: ListView(children: const [
        LevelsPanel(price: 90000.0, takeProfit: 88527.0, stopLoss: 77509.0,
                    side: 'LONG', outcome: 'target', fixed: true, interval: '4h'),
      ]))));
      expect(find.text('Take Profit (TP1) — reached'), findsOneWidget);
      expect(find.textContaining('not a level to enter against'), findsOneWidget);
      expect(find.text('Stop Loss (SL)'), findsOneWidget);
    });

    testWidgets('a FLAT band the price has left says so', (t) async {
      await t.pumpWidget(MaterialApp(home: Scaffold(body: ListView(children: const [
        LevelsPanel(price: 0.0992, takeProfit: 0.09131, stopLoss: 0.08369,
                    side: '', fixed: true, interval: '1d'),
      ]))));
      expect(find.textContaining('already left this band'), findsOneWidget);
      expect(find.text('Upper barrier'), findsOneWidget);
    });
  });

  group('levels keep every digit a coin moves in', () {
    test('a small-priced coin is not rounded to its own entry', () {
      // 1000PEPE at 0.003624: four decimals gave "0.0036" for entry, target
      // and stop alike, and the trade closed the moment it was logged
      expect(priceDecimals(0.003624), 6);
      expect(priceInput(0.003624), '0.003624');
      expect(priceInput(0.0036891), '0.003689');
      expect(priceInput(0.12345678), '0.1235');
      expect(priceInput(2.3456789), '2.3457');
      expect(priceInput(85346.123), '85346.12');
      expect(priceInput(null), '');
      // a stop seeded 1% under a small entry stays under it after formatting
      final entry = 0.003624, sl = entry * 0.99;
      expect(double.parse(priceInput(sl)), lessThan(double.parse(priceInput(entry))));
    });
  });

  group('changing the levels of an open entry', () {
    TradeEntry e(String side, {double? tp, double? sl, String? iv}) => TradeEntry(
        id: 'e-$side', symbol: 'ETHUSDT', side: side, size: 1, entryPrice: 100,
        openedAt: DateTime.now(), takeProfit: tp, stopLoss: sl, interval: iv ?? '4h');

    test('withLevels replaces both, allows clearing, and stamps a moved stop', () {
      final t = e('LONG', tp: 110, sl: 90);
      final w = t.withLevels(takeProfit: 115, stopLoss: 95);
      expect(w.takeProfit, 115);
      expect(w.stopLoss, 95);
      expect(w.stopMovedAt, isNotNull, reason: 'a hand-set stop is judged from now');
      final same = t.withLevels(takeProfit: 120, stopLoss: 90);
      expect(same.stopMovedAt, isNull, reason: 'the stop did not change');
      final cleared = t.withLevels(takeProfit: null, stopLoss: 90);
      expect(cleared.takeProfit, isNull);
    });

    test('setLevels writes through the store and leaves closed entries alone', () async {
      SharedPreferences.setMockInitialValues({});
      Trades.instance.resetForTest();
      final t = e('SHORT', tp: 90, sl: 110);
      await Trades.instance.add(t);
      final u = await Trades.instance.setLevels(t.id, takeProfit: 85, stopLoss: 105);
      expect(u!.takeProfit, 85);
      expect((await Trades.instance.load()).single.stopLoss, 105);
      await Trades.instance.close(t.id, 95);
      expect(await Trades.instance.setLevels(t.id, takeProfit: 1, stopLoss: 2), isNull);
    });
  });

  group('resetting the score', () {
    test('clearClosed removes the closed history and keeps open positions', () async {
      SharedPreferences.setMockInitialValues({});
      Trades.instance.resetForTest();
      TradeEntry e(String id) => TradeEntry(
          id: id, symbol: 'BTCUSDT', side: 'LONG', size: 1, entryPrice: 100,
          openedAt: DateTime.now(), stopLoss: 90, interval: '4h');
      await Trades.instance.add(e('open1'));
      await Trades.instance.add(e('done1'));
      await Trades.instance.add(e('done2'));
      await Trades.instance.close('done1', 105);
      await Trades.instance.close('done2', 95);
      expect(await Trades.instance.clearClosed(), 2);
      final left = await Trades.instance.load();
      expect(left.map((t) => t.id), ['open1']);
      expect(await Trades.instance.clearClosed(), 0);
    });
  });

  group('the shown name', () {
    test('survives the account cache', () async {
      SharedPreferences.setMockInitialValues({});
      final a = Account.fromJson({'identifier': 'a@x.com', 'username': 'Vanth_Tim',
          'tier': 'pro', 'entitled': true});
      await Settings.instance.saveAccount(a);
      final back = await Settings.instance.cachedAccount();
      expect(back!.username, 'Vanth_Tim');
      expect(back.displayName, 'Vanth_Tim');
    });
  });
}
