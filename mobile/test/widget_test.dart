/// Widget and model tests.
///
/// These check the parts that would fail silently rather than loudly: the
/// JSON contract with the Python service, and the null handling that keeps a
/// missing value from being rendered as a confident zero.
library;

import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:tradingbot_app/api/models.dart';
import 'package:tradingbot_app/main.dart';

void main() {
  testWidgets('boots to the secure link screen, not straight to a dashboard',
      (tester) async {
    // The app restores a saved API host and token before painting anything,
    // because probing the connection against the compile-time default first
    // would report "no link" for a server that is perfectly reachable.
    // Without a mock store that read throws on the test binding.
    SharedPreferences.setMockInitialValues({});

    await tester.pumpWidget(const TradingBotApp());
    await tester.pump();                                  // kick off restore
    await tester.pump(const Duration(milliseconds: 50));   // let it land

    expect(find.text('SECURE NEURAL TRADING LINK'), findsOneWidget);
    expect(find.text('INITIALIZE CONNECTION'), findsOneWidget);
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
}
