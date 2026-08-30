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

import 'package:tradingbot_app/api/watchlist.dart';
import 'package:tradingbot_app/main.dart';

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
}
