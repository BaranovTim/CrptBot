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

import 'package:tradingbot_app/api/muted.dart';
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
