/// Notifications while the app is closed.
///
/// WHAT WAS BROKEN
///     Every event-driven alert — a signal flipping to BUY, a new headline,
///     a filing — was collected by a 20-second timer inside the running app.
///     Foregrounded: seconds. Backgrounded: never, because the timer is
///     suspended. Closed: never. So the alerts only worked while you were
///     already looking at the thing they were telling you about.
///
/// WHAT THIS IS
///     An Android WorkManager job that wakes on its own, asks the server what
///     is new, and posts notifications — with the app closed and the phone in
///     a pocket. It runs in a separate isolate with no access to anything the
///     UI holds, which is why the collection rules live in `alert_feed.dart`
///     and both callers go through them.
///
/// WHAT IT IS HONESTLY NOT, ON ANDROID
///     FIFTEEN MINUTES is WorkManager's floor for a periodic job, and Android
///     treats even that as a target rather than a promise — a dozing phone can
///     stretch it. Fine for 1h, 4h and 1d calls and for news; a 15m signal can
///     arrive late. Real-time delivery to a sleeping phone needs a push
///     service (FCM), which needs a Firebase project and server credentials.
///
///     FORCE-STOPPING the app from Settings ends this until you open the app
///     again — that is Android's rule for every app, not something code can
///     opt out of. Swiping it out of the recents list does NOT.
///
///     AGGRESSIVE BATTERY MANAGEMENT on some manufacturers' phones (Xiaomi,
///     Huawei, OnePlus, some Samsungs) will kill background work regardless.
///     If alerts stop, that is the first thing to check.
///
/// AND ON iOS, WHERE IT IS WEAKER STILL
///     This runs as a BGAppRefreshTask. It needs no paid developer account —
///     background modes are Info.plist declarations, not entitlements tied to
///     an App ID, unlike Push Notifications, which is the one that does. So
///     this is the best iOS can do for free, and "best" is doing some work:
///
///       THE FIFTEEN MINUTES IS A REQUEST. iOS schedules refresh windows on
///       its own judgement — how often you open the app, battery level,
///       whether it is charging, network. A few times a day is normal. Every
///       fifteen minutes is not.
///
///       SWIPING THE APP AWAY in the app switcher stops background refresh
///       entirely until you open it again. On Android that gesture is
///       harmless; on iOS it is the off switch.
///
///       LOW POWER MODE disables background refresh outright, as does the
///       per-app toggle in Settings > General > Background App Refresh.
///
///     What is NOT weaker on iOS: the scheduled calendar warnings. Those are
///     handed to the OS in advance with a fire time, and arrive on the minute
///     whether the app is running or not.
library;

import 'dart:async';
import 'dart:io' show Platform;
import 'dart:ui' show DartPluginRegistrant;

import 'package:flutter/foundation.dart' show debugPrint, kIsWeb;
import 'package:workmanager/workmanager.dart';

import 'alert_feed.dart';
import 'client.dart';
import 'notifications.dart';
import 'settings.dart';

const String _taskName = 'thusildy.alerts.poll';

/// The scheduled job's name, which on iOS is also its BGTaskScheduler
/// identifier — so it has to match `Info.plist`'s
/// `BGTaskSchedulerPermittedIdentifiers` and the AppDelegate exactly.
///
/// TWO NAMES, DELIBERATELY. Android's has to stay what it already was: a
/// periodic WorkManager job is keyed by its unique name, so renaming it would
/// leave the old one running forever on every phone that had already
/// installed the app, polling under a name nothing cancels.
String get _uniqueName => (!kIsWeb && Platform.isIOS)
    ? 'com.tradingbot.tradingbotApp.alertPoll'
    : 'thusildy-alert-poll';

/// How often the OS is ASKED to run the job.
///
/// Android: fifteen minutes is WorkManager's floor and it is roughly honoured.
/// iOS: this is a lower bound on a request, and nothing more. iOS decides
/// when — and whether — to run a BGAppRefreshTask, based on how you use the
/// app, battery, and network. In practice a few times a day, and not at all
/// on a phone in Low Power Mode.
const Duration pollPeriod = Duration(minutes: 15);

/// The background isolate's entry point.
///
/// `@pragma('vm:entry-point')` is load-bearing: without it the tree shaker
/// removes this in a release build, because nothing in Dart calls it — the
/// Android side does, by name. The symptom is background alerts that work
/// perfectly in debug and never fire in the APK.
@pragma('vm:entry-point')
void alertPollEntry() {
  Workmanager().executeTask((task, inputData) async {
    // A fresh isolate has no plugin registrations. Without this,
    // shared_preferences and the notification plugin both throw
    // MissingPluginException and the job fails silently every time.
    DartPluginRegistrant.ensureInitialized();
    try {
      await pollOnce();
      return true;
    } catch (e) {
      debugPrint('[bg] poll failed: $e');
      // false asks WorkManager to retry with backoff. A failure here is
      // almost always the network being down, which is exactly the case
      // where a retry is the right answer.
      return false;
    }
  });
}

/// One background collection. Also called directly by the tests.
Future<int> pollOnce() async {
  final client = ApiClient();
  await Settings.instance.restore(client);
  if (client.token.isEmpty) {
    // signed out — nothing to ask for, and asking anyway would burn a wake-up
    return 0;
  }

  await Notifications.instance.init();
  final batch = await collectAlerts(client);
  for (final a in batch.deliver) {
    await Notifications.instance.showAlert(a);
  }
  debugPrint('[bg] delivered ${batch.deliver.length} of ${batch.total}');
  return batch.deliver.length;
}

/// Ask Android to start waking us up.
///
/// Idempotent — `ExistingPeriodicWorkPolicy.keep` means calling this on every
/// launch does not reset the schedule, so an app opened ten times an hour
/// still polls on the same cadence rather than never reaching the end of a
/// fresh fifteen-minute window.
Future<void> startBackgroundAlerts() async {
  // Android and iOS both, now. See the iOS caveats in the library doc above:
  // the mechanism is real and free, the schedule is not yours to control.
  if (kIsWeb || !(Platform.isAndroid || Platform.isIOS)) return;
  try {
    await Workmanager().initialize(alertPollEntry);
    await Workmanager().registerPeriodicTask(
      _uniqueName,
      _taskName,
      frequency: pollPeriod,
      // iOS reads `initialDelay`, not `frequency`: it becomes the
      // BGAppRefreshTaskRequest's `earliestBeginDate`, and the plugin
      // re-submits with the same value after every run — so on iOS this is
      // the period. Left unset it would be "as soon as iOS likes", which is
      // both greedier than needed and no faster in practice.
      //
      // Android is deliberately NOT given one: its schedule is already
      // verified working, and a fresh initial delay on every launch is a way
      // to never reach the end of a window.
      initialDelay: (!kIsWeb && Platform.isIOS) ? pollPeriod : null,
      // UPDATE, not KEEP.
      //
      // `keep` is the obvious choice for "do not reset the schedule on every
      // launch", and it has a trap: it also ignores the new definition, so a
      // fixed job would never reach a phone that had already registered a
      // broken one. `update` keeps the existing period's progress and swaps
      // the spec, which is both idempotent and upgradeable.
      existingWorkPolicy: ExistingPeriodicWorkPolicy.update,
      // NO NETWORK CONSTRAINT, on purpose.
      //
      // `NetworkType.connected` looks free and is not: it becomes a
      // NetworkRequest demanding INTERNET & VALIDATED & NOT_VCN_MANAGED &
      // NOT_BANDWIDTH_CONSTRAINED, and any capability a given device does not
      // report leaves the job permanently unsatisfied — it simply never runs,
      // with nothing in the app to say why. Observed on an Android 15
      // emulator, where the scheduled job sat with `unsatisfied:0x80000000`.
      //
      // Waking with no network costs one failed HTTP call, which returns
      // false and asks WorkManager to retry. That is a far cheaper failure
      // than an alert channel that is silently dead on some phones.
      backoffPolicy: BackoffPolicy.linear,
      backoffPolicyDelay: const Duration(minutes: 5),
    );
    debugPrint('[bg] periodic alert poll registered '
        '(${pollPeriod.inMinutes}m)');
  } catch (e) {
    // Unsupported platform, or the OS refused. The in-app timer still runs,
    // so this degrades to the old behaviour rather than breaking the app.
    debugPrint('[bg] could not register background polling: $e');
  }
}

/// Stop waking up. Called on sign-out: polling for an account that is no
/// longer signed in would be a background job that can only ever 401.
Future<void> stopBackgroundAlerts() async {
  try {
    await Workmanager().cancelByUniqueName(_uniqueName);
  } catch (_) {}
}
