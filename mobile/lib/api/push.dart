/// Notifications that arrive with this app closed.
///
/// WHY THE BACKGROUND JOB IS NOT ENOUGH, AND WHY THAT IS NOT A BUG
///     `background.dart` is correct. The WorkManager registration, the
///     BGTaskScheduler identifier, the Info.plist declarations and the
///     AppDelegate handler were all checked line by line against the plugin's
///     own source, and they agree. The problem is that on iOS a
///     BGAppRefreshTask runs WHEN iOS FEELS LIKE IT — a few times a day for
///     an app used a few times a day, and never at all once you swipe it out
///     of the app switcher or turn on Low Power Mode.
///
///     Which is exactly the symptom: alerts appearing in a clump the moment
///     the app is reopened, each one stamped with a time that has already
///     passed. The phone was not failing its checks. It was not being woken
///     up to make them.
///
/// WHY IT CANNOT BE FIXED INSIDE THIS APP
///     The only thing that wakes an iOS app on someone else's schedule is a
///     push through APNs, and the Push Notifications capability is an
///     entitlement bound to an App ID — which needs a paid Apple Developer
///     account. Background modes are a plist declaration and cost nothing;
///     push is not and does not. No amount of polling harder helps, because
///     the app is not running to poll.
///
/// SO THE DELIVERY MOVES OFF THIS APP
///     The alert engine already runs on the server, on a thirty-second loop.
///     It knows about a signal flip long before the phone does. This
///     registers a topic with that server, which relays each alert to ntfy —
///     a free notification service whose own iOS app holds a real APNs
///     entitlement, and can therefore wake a phone that has ours closed.
///
///     You install ntfy from the App Store once, subscribe to the topic this
///     file generates, and alerts arrive within about a minute of the server
///     noticing them, forever, whatever this app is doing.
///
/// THE TOPIC IS A PASSWORD
///     ntfy has no accounts. Anyone who knows a topic can read it and post to
///     it. So the topic is 128 bits from a cryptographic generator, never
///     anything derived from a name or a symbol, and it is not shown anywhere
///     it could be shoulder-read casually. What someone who learned it would
///     get: the alert text, which says which pairs you watch and what the
///     model said about them. Not the account, not the token, not a position.
///
/// THE SERVER HAS TO KNOW YOUR SETTINGS
///     Sensitivity, news level and the mute list all live on this device,
///     which was right while this device did the filtering. Now the relay
///     decides, so the settings are shipped up with the subscription and
///     re-sent whenever they change. The phone is still where they are set;
///     the server is only applying the answer.
library;

import 'dart:convert';
import 'dart:math';

import 'package:flutter/foundation.dart' show debugPrint;
import 'package:shared_preferences/shared_preferences.dart';

import 'client.dart';
import 'muted.dart';
import 'settings.dart';

class PushDelivery {
  PushDelivery._();
  static final PushDelivery instance = PushDelivery._();

  static const _topicKey = 'push.topic.v1';
  static const _serverKey = 'push.server.v1';

  /// What was last successfully registered. Compared against the current
  /// settings so `sync` can be called from anywhere, as often as anyone
  /// likes, and cost nothing when nothing has changed.
  ///
  /// This is why there is no list of "call sync here" sites scattered through
  /// the settings screens: every one of those would be a place to forget, and
  /// a forgotten one means the lock screen quietly disagreeing with the app.
  static const _fingerprintKey = 'push.fingerprint.v1';

  /// A fresh topic: 128 bits of entropy, hex, behind a recognisable prefix.
  ///
  /// `Random.secure()` and not `Random()`. The topic is the only thing
  /// protecting the alert stream, and a seedable PRNG would make it
  /// reconstructible by anyone who could guess when it was generated.
  static String newTopic() {
    final r = Random.secure();
    final b = List<int>.generate(16, (_) => r.nextInt(256));
    final hex = b.map((x) => x.toRadixString(16).padLeft(2, '0')).join();
    return 'thusildy-$hex';
  }

  Future<String?> topic() async {
    try {
      final v = (await SharedPreferences.getInstance()).getString(_topicKey);
      return (v == null || v.isEmpty) ? null : v;
    } catch (_) {
      return null;
    }
  }

  Future<String> server() async {
    try {
      final v = (await SharedPreferences.getInstance()).getString(_serverKey);
      return (v == null || v.isEmpty) ? 'https://ntfy.sh' : v;
    } catch (_) {
      return 'https://ntfy.sh';
    }
  }

  /// Where a person subscribes. Opening this on a phone with the ntfy app
  /// installed hands it straight to that app.
  String subscribeUrl(String topic, String server) =>
      '${server.replaceAll(RegExp(r'/+$'), '')}/$topic';

  /// Turn it on. Generates a topic if there is not one already, and registers
  /// it with the current settings.
  ///
  /// The topic SURVIVES being turned off and on again, deliberately: a new
  /// one every time would mean re-subscribing in ntfy every time, and the
  /// most likely reason someone toggles this is that it did not seem to be
  /// working.
  Future<String?> enable(ApiClient client) async {
    var t = await topic();
    if (t == null) {
      t = newTopic();
      await _put(_topicKey, t);
    }
    final ok = await _register(client, t, force: true);
    return ok ? t : null;
  }

  Future<void> disable(ApiClient client) async {
    final t = await topic();
    if (t == null) return;
    try {
      await client.pushUnsubscribe(t);
    } catch (e) {
      debugPrint('[push] unsubscribe failed: $e');
    }
    // The topic is kept, the fingerprint is not: re-enabling must re-register
    // rather than believe a registration the server has already forgotten.
    await _put(_fingerprintKey, '');
  }

  /// Re-register if — and only if — something the server filters on changed.
  ///
  /// Safe to call on every launch and every poll. Does nothing at all when
  /// push has never been turned on.
  Future<void> sync(ApiClient client) async {
    final t = await topic();
    if (t == null) return;
    await _register(client, t);
  }

  Future<bool> sendTest(ApiClient client) async {
    final t = await topic();
    if (t == null) return false;
    try {
      final r = await client.pushTest(t);
      return r['ok'] == true;
    } catch (e) {
      debugPrint('[push] test failed: $e');
      return false;
    }
  }

  Future<bool> _register(ApiClient client, String t,
      {bool force = false}) async {
    final sensitivity = await Settings.instance.sensitivity();
    final news = await Settings.instance.newsAlerts();
    final muted = (await Muted.instance.load()).toList()..sort();
    final srv = await server();

    final print = json.encode([t, srv, sensitivity, news, muted]);
    if (!force && print == await _read(_fingerprintKey)) return true;

    try {
      final r = await client.pushSubscribe(
        topic: t,
        server: srv,
        sensitivity: sensitivity,
        news: news,
        muted: muted,
      );
      if (r['ok'] != true) {
        debugPrint('[push] register refused: ${r['error']}');
        return false;
      }
      await _put(_fingerprintKey, print);
      return true;
    } catch (e) {
      // Offline, most likely. The fingerprint is left alone so the next
      // sync tries again rather than believing a registration that never
      // landed.
      debugPrint('[push] register failed: $e');
      return false;
    }
  }

  Future<String> _read(String key) async {
    try {
      return (await SharedPreferences.getInstance()).getString(key) ?? '';
    } catch (_) {
      return '';
    }
  }

  Future<void> _put(String key, String v) async {
    try {
      await (await SharedPreferences.getInstance()).setString(key, v);
    } catch (_) {
      // in-memory for this session; the next launch re-registers
    }
  }
}
