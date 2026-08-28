/// Phone notifications for signals, filings, news and scheduled events.
///
/// WHAT IS RELIABLE HERE, AND WHAT IS NOT
/// --------------------------------------
/// These are LOCAL notifications. There is no push server, no Firebase and no
/// APNs certificate, so the honest split is:
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
/// Making the second category as reliable as the first needs real push, which
/// needs an Apple Developer account and a server holding APNs/FCM credentials.
/// The alert payloads are already shaped for it — `/api/alerts` would become
/// the thing that pushes rather than the thing that is polled — but nothing
/// here pretends to be that today.
///
/// EVERY NOTIFICATION SAYS WHEN THE THING HAPPENED
/// -----------------------------------------------
/// Not when it was noticed. An SEC filing can disclose a trade from five days
/// ago; a notification that reads as "now" would imply a freshness the data
/// does not have. So the body carries the event time in local time, plus how
/// long ago it was, plus — for filings — the disclosure lag.
library;

import 'package:flutter/foundation.dart' show kIsWeb;
import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:timezone/data/latest_all.dart' as tzdata;
import 'package:timezone/timezone.dart' as tz;

import 'models.dart';

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

class Notifications {
  Notifications._();
  static final Notifications instance = Notifications._();

  final _plugin = FlutterLocalNotificationsPlugin();
  bool _ready = false;
  bool granted = false;

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

  Future<void> init() async {
    if (_ready || kIsWeb) return;
    tzdata.initializeTimeZones();
    try {
      tz.setLocalLocation(tz.getLocation(DateTime.now().timeZoneName));
    } catch (_) {
      // timeZoneName is an abbreviation like "EEST" and often is not an IANA
      // id. UTC is a safe base: every schedule below is built from an
      // absolute instant, so only the displayed wall clock would differ.
      tz.setLocalLocation(tz.getLocation('UTC'));
    }

    await _plugin.initialize(const InitializationSettings(
      android: AndroidInitializationSettings('@mipmap/ic_launcher'),
      iOS: DarwinInitializationSettings(
        requestAlertPermission: true,
        requestBadgePermission: true,
        requestSoundPermission: true,
      ),
    ));

    final android = _plugin.resolvePlatformSpecificImplementation<
        AndroidFlutterLocalNotificationsPlugin>();
    if (android != null) {
      for (final c in [_channelSignals, _channelMarket, _channelCalendar]) {
        await android.createNotificationChannel(c);
      }
      granted = await android.requestNotificationsPermission() ?? false;
    }
    final ios = _plugin.resolvePlatformSpecificImplementation<
        IOSFlutterLocalNotificationsPlugin>();
    if (ios != null) {
      granted = await ios.requestPermissions(
              alert: true, badge: true, sound: true) ??
          false;
    }
    _ready = true;
  }

  NotificationDetails _details(String kind, String severity) {
    final channel = switch (kind) {
      'signal' => _channelSignals,
      'calendar' => _channelCalendar,
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
  /// This goes through `zonedSchedule` one second out rather than `show()`,
  /// and the reason is empirical: on this device `show()` was accepted and
  /// then never displayed — no banner, no entry in Notification Center, no
  /// error — while a scheduled notification delivered reliably every time,
  /// foreground and background alike. Rather than keep guessing at
  /// presentation options, immediate alerts use the mechanism that is known
  /// to work. One second is imperceptible and the delivery is the same object
  /// either way.
  Future<void> showAlert(Alert a) async {
    if (!_ready) await init();
    final when = tz.TZDateTime.now(tz.local).add(const Duration(seconds: 1));
    await _plugin.zonedSchedule(
      _idFor(a.id),
      a.title,
      '${a.body}\n${a.whenLine()}',
      when,
      _details(a.kind, a.severity),
      androidScheduleMode: AndroidScheduleMode.inexactAllowWhileIdle,
      uiLocalNotificationDateInterpretation:
          UILocalNotificationDateInterpretation.absoluteTime,
      payload: a.id,
    );
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
