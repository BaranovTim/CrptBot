package com.tradingbot.tradingbot_app

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.PowerManager
import android.provider.Settings
import io.flutter.embedding.android.FlutterFragmentActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

/**
 * BATTERY OPTIMISATION IS THE ANDROID VERSION OF THE PROBLEM.
 *
 * The background alert job is an ordinary periodic WorkManager request and it
 * is registered correctly — the merged manifest carries WorkManager's boot
 * receiver, its job service and every permission it needs. What defers it is
 * App Standby: Android sorts apps into buckets by how recently they were
 * used, and a job in the "rare" or "restricted" bucket can wait most of a day
 * for its fifteen-minute window. An app opened a few times a day lives in a
 * low bucket almost permanently, so the job runs the moment you open the app
 * — which promotes it — and then goes quiet again. That is exactly the
 * symptom: late alerts, in a clump, on reopen.
 *
 * Exempting the app from battery optimisation takes it out of those buckets.
 * It is the one lever that exists on the device, and it needs the user's own
 * consent through a system dialog — which is why this is a request, not a
 * setting the app can flip.
 *
 * WHAT IT STILL DOES NOT BUY. Fifteen minutes remains WorkManager's floor,
 * and some manufacturers (Xiaomi, Huawei, OnePlus, some Samsungs) run their
 * own killers on top of Android's that this does not touch. The relay in
 * `api/push.py` is the path that does not depend on any of it.
 */
// FlutterFragmentActivity, NOT FlutterActivity.
//
// `local_auth` shows the biometric prompt through AndroidX BiometricPrompt,
// which needs a FragmentActivity to attach to. On a plain FlutterActivity the
// call fails at runtime with `no_fragment_activity` — it compiles, installs
// and only breaks the first time somebody turns the lock on.
class MainActivity : FlutterFragmentActivity() {
    private val channel = "vanth/power"

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, channel)
            .setMethodCallHandler { call, result ->
                when (call.method) {
                    "isExempt" -> result.success(isExempt())
                    "requestExemption" -> {
                        requestExemption()
                        result.success(null)
                    }
                    "openBatterySettings" -> {
                        openBatterySettings()
                        result.success(null)
                    }
                    else -> result.notImplemented()
                }
            }
    }

    private fun isExempt(): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.M) return true
        val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
        return pm.isIgnoringBatteryOptimizations(packageName)
    }

    /**
     * The direct dialog. Needs REQUEST_IGNORE_BATTERY_OPTIMIZATIONS declared,
     * and some builds refuse to show it — hence the fallback to the settings
     * list, which always exists and needs no permission at all. A dead button
     * would be worse than a longer path.
     */
    private fun requestExemption() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.M) return
        try {
            startActivity(
                Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS)
                    .setData(Uri.parse("package:$packageName"))
            )
        } catch (e: Exception) {
            openBatterySettings()
        }
    }

    private fun openBatterySettings() {
        try {
            startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS))
        } catch (e: Exception) {
            try {
                startActivity(
                    Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS)
                        .setData(Uri.parse("package:$packageName"))
                )
            } catch (_: Exception) {
                // nothing left to try; the Flutter side shows the manual path
            }
        }
    }
}
