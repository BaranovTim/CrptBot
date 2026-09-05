/// Whether Android is allowed to defer this app's background work.
///
/// WHY THIS EXISTS
///     The alert poll is an ordinary periodic WorkManager job and it is
///     registered correctly — the merged manifest carries WorkManager's boot
///     receiver, its job service and every permission it needs, all verified
///     against the built APK. What stops it running is App Standby: Android
///     sorts apps into buckets by how recently they were used, and a job in
///     the "rare" or "restricted" bucket can wait most of a day for its
///     fifteen-minute window.
///
///     An app opened a few times a day sits in a low bucket almost
///     permanently. So the job runs the moment the app is opened — opening it
///     promotes the bucket — and then goes quiet. Which is precisely the
///     symptom: alerts arriving late, in a clump, on reopen.
///
/// WHAT AN EXEMPTION ACTUALLY BUYS
///     It takes the app out of those buckets, so the fifteen-minute period is
///     roughly honoured instead of being stretched to hours. It does NOT make
///     the floor lower than fifteen minutes, and it does not touch the extra
///     killers some manufacturers run on top of Android's own — Xiaomi,
///     Huawei, OnePlus and some Samsungs each have their own, reachable only
///     through their own settings screens.
///
///     The relay in `push.dart` is the path that depends on none of this.
library;

import 'dart:io' show Platform;

import 'package:flutter/foundation.dart' show debugPrint, kIsWeb;
import 'package:flutter/services.dart';

class Power {
  Power._();
  static final Power instance = Power._();

  static const _channel = MethodChannel('thusildy/power');

  bool get supported => !kIsWeb && Platform.isAndroid;

  /// True when Android will NOT throttle this app's background work.
  ///
  /// Answers true on every platform that has no such concept, so a caller can
  /// treat "not exempt" as a real finding rather than as "not Android".
  Future<bool> isExempt() async {
    if (!supported) return true;
    try {
      return await _channel.invokeMethod<bool>('isExempt') ?? true;
    } on PlatformException catch (e) {
      debugPrint('[power] isExempt failed: $e');
      return true;                 // unknown is not a reason to nag
    } on MissingPluginException {
      return true;                 // an older build of the host app
    }
  }

  /// Opens the system dialog. The user grants it, not the app.
  Future<void> requestExemption() async {
    if (!supported) return;
    try {
      await _channel.invokeMethod<void>('requestExemption');
    } catch (e) {
      debugPrint('[power] requestExemption failed: $e');
    }
  }

  Future<void> openBatterySettings() async {
    if (!supported) return;
    try {
      await _channel.invokeMethod<void>('openBatterySettings');
    } catch (e) {
      debugPrint('[power] openBatterySettings failed: $e');
    }
  }
}
