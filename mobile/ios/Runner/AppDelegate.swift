import Flutter
import UIKit
import workmanager_apple

@main
@objc class AppDelegate: FlutterAppDelegate, FlutterImplicitEngineDelegate {
  /// Must match `Background.iosTaskId` in Dart AND the entry in
  /// `BGTaskSchedulerPermittedIdentifiers` in Info.plist. All three, exactly.
  ///
  /// iOS does not warn when they disagree. It simply never launches the app,
  /// which is indistinguishable from "background refresh did not happen to
  /// run yet" — the failure mode this whole feature is trying to escape.
  private static let alertPollTaskId = "com.tradingbot.tradingbotApp.alertPoll"

  override func application(
    _ application: UIApplication,
    didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?
  ) -> Bool {
    // REGISTERED HERE, AND NOWHERE ELSE.
    //
    // BGTaskScheduler requires every launch handler to be registered before
    // `didFinishLaunchingWithOptions` returns — Apple raises an exception for
    // a handler registered any later. It cannot be moved into the Dart side's
    // `startBackgroundAlerts()`, which runs long after launch: that call only
    // SCHEDULES the task, and it can only schedule an identifier whose
    // handler was already installed right here.
    //
    // Needs no paid developer account. Background modes are Info.plist
    // declarations rather than entitlements tied to an App ID, unlike Push
    // Notifications — which is why this is the best iOS can do for free.
    // ONE IDENTIFIER, ONE KIND: BGAppRefreshTask.
    //
    // Registering the same identifier as a BGProcessingTask as well looks like
    // belt and braces and is not: the plugin stores one kind per identifier,
    // so the second registration wins and the periodic re-scheduling would
    // then submit the wrong request type. A processing task is also the wrong
    // tool — those are for long work on an idle, charging device, typically
    // overnight. This is a two-second HTTP call.
    //
    // `earliestBeginInSeconds` is what the plugin re-submits with after each
    // run, which is what makes a one-shot BGAppRefreshTask into a repeating
    // one.
    WorkmanagerPlugin.registerPeriodicTask(
      withIdentifier: AppDelegate.alertPollTaskId,
      earliestBeginInSeconds: 900
    )

    return super.application(application, didFinishLaunchingWithOptions: launchOptions)
  }

  func didInitializeImplicitFlutterEngine(_ engineBridge: FlutterImplicitEngineBridge) {
    GeneratedPluginRegistrant.register(with: engineBridge.pluginRegistry)
  }
}
