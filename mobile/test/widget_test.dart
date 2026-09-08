/// Widget and model tests.
///
/// These check the parts that would fail silently rather than loudly: the
/// JSON contract with the Python service, and the null handling that keeps a
/// missing value from being rendered as a confident zero.
library;

import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:tradingbot_app/api/models.dart';
import 'dart:convert';

import 'package:tradingbot_app/api/background.dart';
import 'package:tradingbot_app/api/muted.dart';
import 'package:tradingbot_app/api/format.dart';
import 'package:tradingbot_app/api/journal.dart';
import 'package:tradingbot_app/api/trades.dart';
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

    await tester.pumpWidget(const ThusIldyApp());
    await tester.pump();                                  // kick off restore
    await tester.pump(const Duration(milliseconds: 50));   // let it land

    expect(find.text('THUSILDY'), findsOneWidget);
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

    await tester.pumpWidget(const ThusIldyApp());
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 50));

    await tester.tap(find.text('SIGN IN'));
    await tester.pump();

    expect(find.text('Both fields are required.'), findsOneWidget);
    expect(find.text('THUSILDY'), findsOneWidget);      // still on sign-in
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
    final now = DateTime.utc(2026, 9, 8, 12);
    final todayLoss = TradeEntry.create(
            symbol: 'BTCUSDT', side: 'LONG', size: 1, entryPrice: 100)
        .closedAtPrice(90);
    final s = JournalStats.of([todayLoss], now: now);
    // closedAtPrice stamps "now", so this one is today
    expect(s.realisedToday, closeTo(-10, 1e-9));

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

    await tester.pumpWidget(const ThusIldyApp());
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

    await tester.pumpWidget(const ThusIldyApp());
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

    // a sensible default before anything is chosen: 1h is the only timeframe
    // whose models survived the fold-spread guard
    expect(await s.lastInterval(), '1h');

    await s.saveInterval('4h');
    expect(await s.lastInterval(), '4h');

    // a value that is not a real timeframe falls back rather than being
    // handed to an API that would 400 on it
    await s.saveInterval('3h');
    expect(await s.lastInterval(), '1h');
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
}
