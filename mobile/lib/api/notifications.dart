/// Phone notifications for signals, filings, news and scheduled events.
///
/// WHAT IS RELIABLE HERE, AND WHAT IS NOT
/// --------------------------------------
/// These are LOCAL notifications. This app holds no APNs certificate and no
/// Firebase project, so the honest split is:
///
///   SCHEDULED events (FOMC and anything in calendar.json) are fully
///   reliable. The OS is handed the fire time in advance and delivers it
///   whether the app is running, backgrounded or killed. The "warn me before
///   the news" case therefore works properly.
///
///   EVENT-DRIVEN alerts (a signal flipping to BUY, a new filing, a spike)
///   are only as timely as the app's next poll. Foreground: seconds.
///   Backgrounded: whenever iOS grants a refresh window, which can be hours.
///   Killed: not at all.
///
/// That second line is not fixable from inside this file. Waking an iOS app
/// on someone else's schedule needs a push through APNs, and the entitlement
/// for it is tied to an App ID under a paid developer account.
///
/// SO THE SECOND CATEGORY HAS A SECOND PATH NOW. See `push.dart`: the server
/// relays each alert to a notification app that DOES hold that entitlement,
/// which reaches the phone whatever this app is doing. Everything below still
/// runs and is still what draws the notification when the app is awake — the
/// relay is a parallel route, not a replacement, and either one arriving is
/// better than the old answer of neither.
///
/// EVERY NOTIFICATION SAYS WHEN THE THING HAPPENED
/// -----------------------------------------------
/// Not when it was noticed. An SEC filing can disclose a trade from five days
/// ago; a notification that reads as "now" would imply a freshness the data
/// does not have. So the body carries the event time in local time, plus how
/// long ago it was, plus — for filings — the disclosure lag.
library;

import 'dart:async';
import 'dart:io' show Platform;

import 'package:flutter/foundation.dart' show debugPrint, kIsWeb;
import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:timezone/data/latest_all.dart' as tzdata;
import 'package:timezone/timezone.dart' as tz;

import 'models.dart';
import 'dart:ui' show DartPluginRegistrant;
import 'settings.dart';
import 'client.dart';
import 'format.dart';
import 'trades.dart';

String describeWhen(DateTime at, {DateTime? now}) {
  final n = now ?? DateTime.now();
  final local = at.toLocal();
  final d = n.difference(local);
  final hhmm =
      '${local.hour.toString().padLeft(2, '0')}:${local.minute.toString().padLeft(2, '0')}';

  String rel;
  if (d.isNegative) {
    final ahead = -d.inMinutes;
    rel = ahead < 60 ? 'in ${ahead}m' : 'in ${(-d.inHours)}h';
  } else if (d.inMinutes < 1) {
    rel = 'just now';
  } else if (d.inMinutes < 60) {
    rel = '${d.inMinutes}m ago';
  } else if (d.inHours < 48) {
    rel = '${d.inHours}h ago';
  } else {
    rel = '${d.inDays}d ago';
  }

  final sameDay =
      local.year == n.year && local.month == n.month && local.day == n.day;
  final date = sameDay
      ? ''
      : ' ${local.day.toString().padLeft(2, '0')}/${local.month.toString().padLeft(2, '0')}';
  return '$hhmm$date · $rel';
}

/// "Open entry: LONG @ 79,200.00" — or null when the log holds nothing open
/// on that symbol. Mirrors `entry_line` in `api/push.py` word for word, and
/// the price goes through `priceText` so it reads as the same number the
/// dashboard shows.
Future<String?> heldLine(String symbol,
    {String? alertInterval, String? to}) async {
  final open = (await Trades.instance.load())
      .where((t) => t.isOpen && t.symbol == symbol);
  if (open.isEmpty) return null;
  final t = open.first;
  final line = 'Open entry: ${t.side} @ ${priceText(t.entryPrice, prefix: '')}';
  final adv = adviceLine(
      side: t.side, entryInterval: t.interval,
      alertInterval: alertInterval, to: to);
  return adv == null ? line : '$line\n$adv';
}

/// What to do with the entry you hold when the call on its coin changes.
///
/// Mirrors `advice_line` in `api/push.py` -- the relay writes this for the
/// lock screen, the app writes it for its own banner, and the two must say
/// the same thing. The numbers are from the 4h held-out year: leaving when
/// the call is withdrawn was no better than holding to the levels; entries
/// that saw the opposite call lost 1.7% on average and leaving at the call
/// was slightly better than holding. A call on another timeframe than the
/// entry's is context: the entry's own timeframe manages its exit.
String? adviceLine({
  required String side,
  String? entryInterval,
  String? alertInterval,
  String? to,
}) {
  final s = side.toUpperCase();
  final t = (to ?? '').toUpperCase();
  if ((s != 'LONG' && s != 'SHORT') || !const {'BUY', 'SELL', 'FLAT'}.contains(t)) {
    return null;
  }
  final piv = entryInterval ?? '';
  if (piv.isNotEmpty && alertInterval != null && alertInterval.isNotEmpty &&
      piv != alertInterval) {
    final manager = piv == '1d' ? 'the trailing stop decides' : 'its $piv levels decide';
    return 'Your entry is on $piv; this call is on $alertInterval. Stay — $manager the exit.';
  }
  final same = (t == 'BUY') == (s == 'LONG');
  if (t == 'FLAT') {
    return piv == '1d'
        ? 'Stay — the trailing stop decides. A withdrawn call is not an exit.'
        : 'Stay — your levels decide. Leaving when the call is withdrawn was '
            'measured no better than holding to them.';
  }
  if (same) return 'Stay in — the call agrees with your entry.';
  final other = t == 'BUY' ? 'LONG' : 'SHORT';
  if (piv == '1d') {
    return 'The model now calls $other. Your trailing stop stays where it is; '
        'closing or tightening is your call — this case was not measured on 1d.';
  }
  return 'Close it — the model now calls the other way. Entries that saw this '
      'lost 1.7% on average in the test year; leaving at the call was slightly '
      'better than holding. A fresh $other entry is on the card if you want to reverse.';
}

/// Where a tapped notification wants the app to go.
///
/// `interval` is null when the notification had no timeframe of its own --
/// a trade closing, say -- and the shell keeps whatever it is on.
class OpenRequest {
  const OpenRequest({required this.symbol, this.interval});
  final String symbol;
  final String? interval;

  /// The payload string. Symbols and intervals never contain '|', and the
  /// third field is free for an id so two alerts on one pair still get
  /// distinct payloads.
  static String encode(String symbol, String? interval, String id) =>
      'open|$symbol|${interval ?? ''}|$id';

  static OpenRequest? decode(String? payload) {
    if (payload == null) return null;
    final p = payload.split('|');
    if (p.length < 3 || p[0] != 'open' || p[1].isEmpty) return null;
    return OpenRequest(symbol: p[1], interval: p[2].isEmpty ? null : p[2]);
  }
}

/// The "keep or close?" question: its payload, its advice, its answer.
class LimitQuestion {
  /// `limit|<symbol>|<interval>|<tradeId>|<price>`. Distinct prefix from
  /// `open|...` so a body tap still opens the coin and an action does not.
  static String encode(TradeEntry t, double price) =>
      'limit|${t.symbol}|${t.interval ?? ''}|${t.id}|$price';

  static bool isAnswer(NotificationResponse r) =>
      (r.payload ?? '').startsWith('limit|') &&
      (r.actionId == 'keep' || r.actionId == 'close');

  /// The line that makes the question answerable: what the model says NOW.
  /// Honest by construction -- it is the live call, not a forecast of how
  /// the trade will end, which nothing here can make.
  static String advice(TradeEntry t, String? call) {
    if (call == null) {
      return 'The model has no call on this pair right now \u2014 no view '
          'either way.';
    }
    final same = (t.isShort && call == 'SELL') || (!t.isShort && call == 'BUY');
    return same
        ? 'The current call is still $call \u2014 keeping is consistent '
            'with the model.'
        : 'The current call has turned to $call, against this entry \u2014 '
            'closing is consistent with the model.';
  }

  static Future<void> answer(NotificationResponse r, {ApiClient? client}) async {
    final p = (r.payload ?? '').split('|');
    if (p.length < 5) return;
    final id = p[3];
    final asked = double.tryParse(p[4]);
    if (r.actionId == 'keep') {
      await Trades.instance.answerLimit(id, close: false);
      return;
    }
    // Close at the live price if we can get one, else at the price the
    // question was asked at -- the honest fallback, and never a level.
    double? price;
    if (client != null) {
      try {
        final coins = await client.coins(symbols: [p[1]]);
        price = coins.firstWhere((c) => c.symbol == p[1]).price;
      } catch (_) {}
    }
    await Trades.instance.answerLimit(id, close: true, price: price ?? asked);
  }
}

/// Runs in a fresh isolate when an action is tapped with the app closed.
///
/// `vm:entry-point` is load-bearing, exactly as in `background.dart`:
/// nothing in Dart calls this, the plugin does, by name, and without the
/// pragma the tree shaker removes it from a release build -- the symptom
/// being buttons that work in debug and silently do nothing in the APK.
@pragma('vm:entry-point')
void onLimitAnswerInBackground(NotificationResponse r) {
  unawaited(() async {
    DartPluginRegistrant.ensureInitialized();
    final client = ApiClient();
    try {
      await Settings.instance.restore(client);
    } catch (_) {}
    await LimitQuestion.answer(r, client: client.token.isEmpty ? null : client);
  }());
}

class Notifications {
  Notifications._();
  static final Notifications instance = Notifications._();

  final _plugin = FlutterLocalNotificationsPlugin();
  bool _ready = false;
  bool granted = false;

  /// Silence everything. Read from settings at init, set by the switch.
  ///
  /// Checked at every post below rather than at the callers, so a new
  /// caller cannot forget. A plain field, not a future: `Trades` reads it
  /// on a hot path and in tests where no plugin exists.
  bool silenced = false;

  Future<void> setSilenced(bool v) async {
    silenced = v;
    await Settings.instance.saveSilenced(v);
    if (v && _ready) {
      // the calendar warnings already booked with the OS would still fire;
      // a silence that lets them through is not one
      try {
        await _plugin.cancelAll();
      } catch (_) {}
    }
  }

  /// Re-read the OS state. Cheap, never prompts, and the thing to call
  /// when a screen that shows the state comes back into view -- the person
  /// may have just changed it in system settings.
  Future<bool> refreshGranted() async {
    if (kIsWeb) return granted;
    try {
      final android = _plugin.resolvePlatformSpecificImplementation<
          AndroidFlutterLocalNotificationsPlugin>();
      if (android != null) {
        granted = await android.areNotificationsEnabled() ?? granted;
      }
    } catch (_) {}
    return granted;
  }

  static const _channelSignals = AndroidNotificationChannel(
    'signals', 'Trade signals',
    description: 'When the recommended action changes to BUY or SELL',
    importance: Importance.max,
  );
  static const _channelMarket = AndroidNotificationChannel(
    'market', 'News, filings and spikes',
    description: 'Whale filings, news releases and intra-bar spikes',
    importance: Importance.high,
  );
  static const _channelCalendar = AndroidNotificationChannel(
    'calendar', 'Scheduled events',
    description: 'Warnings before scheduled market events such as FOMC',
    importance: Importance.high,
  );
  static const _channelSmart = AndroidNotificationChannel(
    'smart', 'Smart money',
    description:
        'When a followed top trader opens, closes or flips a position',
    importance: Importance.high,
  );
  static const _channelMomentum = AndroidNotificationChannel(
    'momentum', 'Momentum rotation',
    description: "This week's momentum picks, once every Monday",
    importance: Importance.defaultImportance,
  );

  /// Taps, as they happen. The shell listens and navigates. A broadcast
  /// stream because the shell can be rebuilt (sign-out, sign-in) and each
  /// instance subscribes afresh.
  final _taps = StreamController<OpenRequest>.broadcast();
  Stream<OpenRequest> get taps => _taps.stream;

  void _onResponse(NotificationResponse r) {
    // An answer to "keep or close?" is an ACTION, not a tap: it must not
    // open the coin, and it must be honoured even from the shade.
    if (LimitQuestion.isAnswer(r)) {
      unawaited(LimitQuestion.answer(r));
      return;
    }
    final req = OpenRequest.decode(r.payload);
    if (req != null) _taps.add(req);
  }

  /// If the app was STARTED by tapping a notification, what it asked for.
  ///
  /// A tap on a closed app does not fire `_onResponse` -- there is no
  /// isolate yet to receive it. The plugin records it instead, and the
  /// shell asks here once at startup. Null on a normal launch.
  Future<OpenRequest?> launchRequest() async {
    if (kIsWeb) return null;
    if (!_ready) await init();
    try {
      final d = await _plugin.getNotificationAppLaunchDetails();
      if (d?.didNotificationLaunchApp != true) return null;
      return OpenRequest.decode(d!.notificationResponse?.payload);
    } catch (_) {
      return null;
    }
  }

  Future<void> init() async {
    if (_ready || kIsWeb) return;
    silenced = await Settings.instance.silenced();
    tzdata.initializeTimeZones();
    try {
      tz.setLocalLocation(tz.getLocation(DateTime.now().timeZoneName));
    } catch (_) {
      // timeZoneName is an abbreviation like "EEST" and often is not an IANA
      // id. UTC is a safe base: every schedule below is built from an
      // absolute instant, so only the displayed wall clock would differ.
      tz.setLocalLocation(tz.getLocation('UTC'));
    }

    // The whole plugin is wrapped, not just the permission request.
    //
    // `flutter_local_notifications` throws a LateInitializationError when no
    // platform implementation is registered — under `flutter test`, and on
    // any host where the plugin is unavailable. That exception was escaping
    // into `Shell.initState`, which takes the entire app down: a phone that
    // cannot do notifications would show a dead screen rather than an app
    // without alerts. Alerts are a feature; the app is not.
    try {
      await _plugin.initialize(
        const InitializationSettings(
          android: AndroidInitializationSettings('@drawable/ic_notification'),
          iOS: DarwinInitializationSettings(
            requestAlertPermission: true,
            requestBadgePermission: true,
            requestSoundPermission: true,
          ),
        ),
        // THE TAP. Without this a notification about DOGEUSDT 4h opened
        // the app on whatever it showed last, which is not what a tap on
        // "DOGEUSDT 4h" means.
        onDidReceiveNotificationResponse: _onResponse,
        // The app may be closed when "Close now" is tapped. This runs in
        // its own isolate then; see `onLimitAnswerInBackground`.
        onDidReceiveBackgroundNotificationResponse: onLimitAnswerInBackground,
      );

      final android = _plugin.resolvePlatformSpecificImplementation<
          AndroidFlutterLocalNotificationsPlugin>();
      if (android != null) {
        for (final c in [
          _channelSignals, _channelMarket, _channelCalendar, _channelSmart,
          _channelMomentum,
        ]) {
          await android.createNotificationChannel(c);
        }
        // ASK THE OS WHAT IS TRUE, not what the request returned.
        //
        // `requestNotificationsPermission()` is null on any Android with no
        // runtime notification permission, and `?? false` read that null
        // as "denied". On those phones the bell showed a snackbar forever
        // and the sheet behind it could never be opened -- while
        // notifications were, in fact, arriving. `areNotificationsEnabled`
        // answers the real question on every Android version.
        final requested = await android.requestNotificationsPermission();
        final enabled = await android.areNotificationsEnabled();
        granted = enabled ?? requested ?? true;
      }
      final ios = _plugin.resolvePlatformSpecificImplementation<
          IOSFlutterLocalNotificationsPlugin>();
      if (ios != null) {
        granted = await ios.requestPermissions(
                alert: true, badge: true, sound: true) ??
            false;
      }
    } catch (e) {
      // `granted` stays false, so the bell renders as off and the Profile
      // screen reports the truth: nothing will be delivered.
      granted = false;
      debugPrint('[notifications] unavailable on this platform: $e');
    }
    _ready = true;
  }

  NotificationDetails _details(String kind, String severity) {
    final channel = switch (kind) {
      'signal' => _channelSignals,
      'calendar' => _channelCalendar,
      'smart' => _channelSmart,
      'momentum' => _channelMomentum,
      _ => _channelMarket,
    };
    return NotificationDetails(
      android: AndroidNotificationDetails(
        channel.id, channel.name,
        channelDescription: channel.description,
        importance: severity == 'high' ? Importance.max : Importance.high,
        priority: severity == 'high' ? Priority.high : Priority.defaultPriority,
        styleInformation: const BigTextStyleInformation(''),
      ),
      iOS: DarwinNotificationDetails(
        // All four of these default to FALSE, which means a notification
        // raised while the app is in the FOREGROUND is accepted, delivered,
        // and silently not shown — indistinguishable from "the alert never
        // fired", with nothing logged anywhere.
        //
        // presentAlert alone is not enough: it maps to the pre-iOS-14
        // presentation option. iOS 14 split that into a banner and a list
        // entry, so a modern device needs presentBanner and presentList or
        // the notification lands nowhere a human will see it.
        presentAlert: true,
        presentBanner: true,
        presentList: true,
        presentBadge: true,
        presentSound: true,
        interruptionLevel: severity == 'high'
            ? InterruptionLevel.timeSensitive
            : InterruptionLevel.active,
      ),
    );
  }

  /// Stable per-alert id so the OS replaces rather than stacks duplicates.
  int _idFor(String key) => key.hashCode & 0x7fffffff;

  /// Deliver an alert now.
  ///
  /// TWO MECHANISMS, BECAUSE THE TWO PLATFORMS MEASURED DIFFERENTLY
  ///     Android: `show()`, which posts straight to the shade. This used to
  ///     go through `zonedSchedule` one second out, and that was the bug —
  ///     `AndroidScheduleMode.inexactAllowWhileIdle` maps to
  ///     `setAndAllowWhileIdle`, which Android is free to batch and defer
  ///     while dozing. The same file already recorded this in
  ///     `sendTestSuite`: scheduling was abandoned there because it did not
  ///     arrive, and `show()` was verified to post within a second. The test
  ///     button therefore worked while real alerts did not, which is exactly
  ///     the shape of "I pressed test and saw notifications, but I never get
  ///     the real ones".
  ///
  ///     iOS: `zonedSchedule`, because there the measurement went the other
  ///     way — `show()` was accepted and never displayed, no banner, no entry
  ///     in Notification Center, no error, while a scheduled notification
  ///     delivered every time. One second is imperceptible.
  Future<void> showAlert(Alert a) async {
    if (!_ready) await init();
    if (silenced) return;
    // The relay adds this same line on the lock screen path; this is the
    // in-app path doing it from the local log. Signals only, first line,
    // for the reason in `api/push.py`: the collapsed notification shows one
    // line, and when you hold the coin a call just changed on, that line
    // is "you hold this".
    final held = a.kind == 'signal'
        ? await heldLine(a.symbol, alertInterval: a.interval, to: a.extra['to'])
        : null;
    final body = [
      ?held,
      a.body,
      a.whenLine(),
    ].join('\n');
    final details = _details(a.kind, a.severity);

    if (!kIsWeb && Platform.isIOS) {
      final when = tz.TZDateTime.now(tz.local).add(const Duration(seconds: 1));
      await _plugin.zonedSchedule(
        _idFor(a.id), a.title, body, when, details,
        androidScheduleMode: AndroidScheduleMode.inexactAllowWhileIdle,
        uiLocalNotificationDateInterpretation:
            UILocalNotificationDateInterpretation.absoluteTime,
        payload: OpenRequest.encode(a.symbol, a.interval, a.id),
      );
      return;
    }
    await _plugin.show(_idFor(a.id), a.title, body, details, payload: OpenRequest.encode(a.symbol, a.interval, a.id));
  }

  /// The trail moved a daily entry's stop. One line, on the signals
  /// channel: it is about a position you hold.
  Future<void> showStopMoved({
    required String id,
    required String symbol,
    required String side,
    required double? from,
    required double to,
    int? moves,
  }) async {
    if (!_ready) await init();
    if (silenced) return;
    final short = symbol.endsWith('USDT')
        ? symbol.substring(0, symbol.length - 4)
        : symbol;
    final dir = side == 'SHORT' ? 'down' : 'up';
    final title = '$short: stop moved $dir to ${priceText(to, prefix: '')}';
    final body = [
      if (from != null) 'Was ${priceText(from, prefix: '')}.',
      'A new swing ${side == 'SHORT' ? 'high' : 'low'} confirmed on the daily '
          'chart; the stop follows it. It never moves back.',
      if (moves != null && moves > 1) 'Moved $moves times since entry.',
    ].join(' ');
    try {
      await _plugin.show(_idFor('trail-$id-$to'), title, body,
          _details('signal', 'medium'),
          payload: OpenRequest.encode(symbol, '1d', id));
    } catch (e) {
      debugPrint('[notifications] stop moved: $e');
    }
  }

  /// "Keep or close?" -- the model's window on a logged entry has closed
  /// and no level was reached. Two buttons, answerable from the shade.
  Future<void> askTimeLimit({
    required TradeEntry entry,
    required double price,
    String? currentCall,
  }) async {
    if (!_ready) await init();
    if (silenced) return;
    final short = entry.symbol.endsWith('USDT')
        ? entry.symbol.substring(0, entry.symbol.length - 4)
        : entry.symbol;
    final pct = entry.pnlPct(price) ?? 0;
    final pnl = '${pct >= 0 ? 'Up' : 'Down'} ${pct.abs().toStringAsFixed(2)}% '
        'at ${priceText(price, prefix: '')}';
    final title = "$short: the model's ${entry.interval ?? ''} window closed";
    final body = '$pnl. ${LimitQuestion.advice(entry, currentCall)}\n'
        'Keep it open, or close it now marked TIME LIMIT?';
    final details = NotificationDetails(
      android: AndroidNotificationDetails(
        _channelSignals.id, _channelSignals.name,
        channelDescription: _channelSignals.description,
        importance: Importance.high, priority: Priority.high,
        icon: '@drawable/ic_notification',
        styleInformation: BigTextStyleInformation(body),
        // Handled without opening the app, and the notification goes away
        // once answered -- a question that stays on screen after being
        // answered looks unanswered.
        actions: const [
          AndroidNotificationAction('keep', 'Keep open',
              showsUserInterface: false, cancelNotification: true),
          AndroidNotificationAction('close', 'Close now',
              showsUserInterface: false, cancelNotification: true),
        ],
      ),
    );
    try {
      await _plugin.show(_idFor('limit:${entry.id}'), title, body, details,
          payload: LimitQuestion.encode(entry, price));
    } catch (e) {
      debugPrint('[notify] time-limit question for ${entry.symbol} not shown: $e');
    }
  }

  Future<void> dismissTimeLimitQuestion(String tradeId) async {
    try {
      await _plugin.cancel(_idFor('limit:$tradeId'));
    } catch (_) {}
  }

  /// A logged trade reached the level you set and was closed in your journal.
  ///
  /// WHY THIS IS ITS OWN METHOD RATHER THAN AN `Alert`
  ///     Every other notification here comes from the server's alert engine
  ///     and describes the market. This one describes YOUR journal, is
  ///     decided entirely on the device, and has no id the server has ever
  ///     seen. Squeezing it into `Alert` would mean inventing server fields
  ///     to satisfy a constructor.
  ///
  ///     It uses the SIGNALS channel deliberately: a stop being hit is the
  ///     most consequential thing this app can tell you about a position, and
  ///     it belongs on the channel you are least likely to have silenced.
  ///
  ///     The text says "in your log" every time, because the app did not
  ///     close anything anywhere else — it has no exchange key, and a
  ///     notification reading "stop loss hit" with no qualifier would imply
  ///     an order this app cannot place.
  Future<void> showTradeClosed({
    required String id,
    required String symbol,
    required String by,
    required String level,
    required String pnl,
  }) async {
    if (!_ready) await init();
    if (silenced) return;
    final short =
        symbol.endsWith('USDT') ? symbol.substring(0, symbol.length - 4) : symbol;
    final takeProfit = by == 'take_profit';
    final title = switch (by) {
      'take_profit' => '$short hit your take profit',
      'stop_loss' => '$short hit your stop loss',
      _ => "$short reached the model's time limit",
    };
    final why = by == 'time_limit'
        ? ' — the model\'s window closed, neither level was reached, and '
            'no answer came in time'
        : '';
    final body = 'Closed in your log at $level · $pnl$why\n'
        'Vanth places no orders — check your exchange.';
    final details = _details('signal', by == 'stop_loss' ? 'high' : 'medium');
    // Keyed by the ENTRY, not by symbol and level. Two entries on the same
    // coin at the same target shared an id, and Android treats a repeated
    // id as an update -- so the second close silently replaced the first
    // notification instead of adding one.
    final nid = _idFor('trade:$id:${takeProfit ? 'tp' : 'sl'}');

    // BEST EFFORT, AND NEVER THROWN. This is called after the journal has
    // already been written; the close is a fact by now. A notification that
    // cannot be posted -- permission revoked, plugin not initialised, no
    // platform at all under test -- is worth a log line and nothing more.
    // Letting it throw out of `autoClose` would turn "the buzz did not
    // arrive" into "the caller's await failed", which is a different and
    // worse bug.
    try {
      final payload = OpenRequest.encode(symbol, null, id);
      if (!kIsWeb && Platform.isIOS) {
        final when =
            tz.TZDateTime.now(tz.local).add(const Duration(seconds: 1));
        await _plugin.zonedSchedule(
          nid, title, body, when, details,
          androidScheduleMode: AndroidScheduleMode.inexactAllowWhileIdle,
          uiLocalNotificationDateInterpretation:
              UILocalNotificationDateInterpretation.absoluteTime,
          payload: payload,
        );
        return;
      }
      await _plugin.show(nid, title, body, details, payload: payload);
    } catch (e) {
      debugPrint('[notify] trade close for $symbol not shown: $e');
    }
  }

  /// Fire one notification of every kind, so you can see what they look like.
  ///
  /// POSTED IMMEDIATELY, NOT SCHEDULED
  ///     The first version booked these 8s apart through `zonedSchedule`, to
  ///     leave time to lock the phone. That is the wrong mechanism for a
  ///     test: `AndroidScheduleMode.inexactAllowWhileIdle` maps to
  ///     `setAndAllowWhileIdle`, which Android is free to batch and defer —
  ///     correct for a news alert two hours out, unusable for "show me now",
  ///     where a delay is indistinguishable from a bug.
  ///
  ///     `show()` posting immediately was verified on an Android 15 emulator:
  ///     all five appear in the shade, correctly grouped, within a second.
  ///
  ///     `show()` posts straight to the shade. A high-importance channel makes
  ///     it a heads-up banner, which is exactly the thing worth looking at.
  ///
  ///     They are spaced ~1.4s so they arrive as separate banners instead of
  ///     collapsing into one group you cannot read. That delay is a plain
  ///     `await`, so keep the app open while it runs.
  ///
  ///     The bodies are real examples with real-looking numbers, because the
  ///     thing worth checking is whether a glanceable notification tells you
  ///     enough to act — and that depends on the wording, not on whether the
  ///     plumbing fires.
  Future<int> sendTestSuite({String symbol = 'BTCUSDT'}) async {
    if (!_ready) await init();
    final now = DateTime.now().toUtc();

    final samples = <Map<String, dynamic>>[
      {
        'kind': 'signal',
        'severity': 'high',
        'title': 'BUY · $symbol 1h',
        'body': 'p(up) 0.68 · EV +0.42R · entry 77,940 · '
            'stop 76,180 · target 81,460',
      },
      {
        'kind': 'spike',
        'severity': 'high',
        'title': 'Spike · $symbol',
        'body': '+2.4% in 3 minutes on 4.1x volume. '
            'The 1h analysis is being recomputed.',
      },
      {
        'kind': 'whale',
        'severity': 'medium',
        'title': 'Whale · MSTR',
        'body': 'Form 4: open-market purchase, 12,400 shares (~\$4.1M) '
            'by an officer.',
      },
      {
        'kind': 'news',
        'severity': 'medium',
        'title': 'News · crypto',
        'body': 'SEC closes its inquiry without action. '
            'Novelty 0.81, magnitude 0.64.',
      },
      {
        'kind': 'smart',
        'severity': 'medium',
        'title': '$symbol: 0xe867…c78e opened LONG',
        'body': '10× · \$2.1M @ 63,120.00\n'
            '69% win rate · +\$22.1M / 30d · 873 trades\n'
            '7 of 25 followed long, 2 short',
      },
      {
        'kind': 'calendar',
        'severity': 'high',
        'title': 'FOMC in 60 minutes',
        'body': 'Rate decision at 14:00 New York. '
            'Spreads usually widen beforehand.',
      },
    ];

    for (var i = 0; i < samples.length; i++) {
      final m = samples[i];
      final at = now.subtract(Duration(minutes: 2 + i));
      final a = Alert.fromJson({
        'id': 'test-${m['kind']}-${now.millisecondsSinceEpoch}',
        'kind': m['kind'],
        'title': m['title'],
        'body': m['body'],
        'severity': m['severity'],
        'symbol': symbol,
        'url': '',
        'seq': 0,
        'at': at.toIso8601String(),
        'detected_at': at.toIso8601String(),
        'extra': <String, String>{},
      });
      await _plugin.show(
        _idFor(a.id),
        a.title,
        '${a.body}\n${a.whenLine()}',
        _details(a.kind, a.severity),
        payload: OpenRequest.encode(a.symbol, a.interval, a.id),
      );
      if (i < samples.length - 1) {
        await Future<void>.delayed(const Duration(milliseconds: 1400));
      }
    }
    return samples.length;
  }

  /// Books the pre-event warnings the OS will deliver even if the app is gone.
  ///
  /// Re-booked wholesale on every calendar refresh: the Fed occasionally moves
  /// a date, and a notification for a meeting that is no longer happening is
  /// worse than no notification. Cancelling only our own id range leaves the
  /// event-driven notifications alone.
  Future<void> scheduleCalendar(List<ScheduledEvent> events,
      {List<int> leadsMinutes = const [60, 5]}) async {
    if (!_ready) await init();
    if (silenced) return;                  // nothing booked; see setSilenced
    final now = DateTime.now();

    for (final e in events) {
      for (final lead in leadsMinutes) {
        final fireAt = e.at.toLocal().subtract(Duration(minutes: lead));
        final id = _idFor('${e.key}-$lead');
        await _plugin.cancel(id);
        if (fireAt.isBefore(now)) continue;

        await _plugin.zonedSchedule(
          id,
          '${e.title} in ${lead}m',
          '${e.note}\nAt ${describeWhen(e.at, now: fireAt)}. '
              'Known to the whole market — expect volatility, not direction.',
          tz.TZDateTime.from(fireAt, tz.local),
          _details('calendar', e.impact == 'high' ? 'high' : 'medium'),
          // inexact keeps this free of Android 12+ exact-alarm permissions.
          // The OS may fire a few minutes late; for a one-hour heads-up that
          // is a fair trade against asking for a restricted permission.
          androidScheduleMode: AndroidScheduleMode.inexactAllowWhileIdle,
          uiLocalNotificationDateInterpretation:
              UILocalNotificationDateInterpretation.absoluteTime,
        );
      }
    }
  }

  Future<List<PendingNotificationRequest>> pending() =>
      _plugin.pendingNotificationRequests();
}
