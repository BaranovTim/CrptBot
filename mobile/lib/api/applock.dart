/// The fingerprint on the way in.
///
/// WHAT THIS PROTECTS AND WHAT IT DOES NOT
///     It protects the SCREEN, not the data. Anyone holding an unlocked phone
///     can otherwise read your positions, your P&L and which coins you watch.
///     A biometric prompt in front of that is worth having and takes a second.
///
///     It is NOT encryption. The trade log and the session token live in
///     shared_preferences, which the OS protects from other apps but which a
///     rooted device can read regardless of this. Saying otherwise would be
///     the kind of security claim that is worse than no claim at all.
///
/// OFF BY DEFAULT, AND FAILS OPEN
///     A lock nobody asked for is a lock that traps people out of their own
///     app — and a phone with no enrolled fingerprint, or a plugin that
///     throws on some manufacturer's build, must not brick the app. Every
///     failure path here lets you in; the only thing that keeps you out is a
///     prompt you were actually shown and actively failed.
library;

import 'package:flutter/foundation.dart' show debugPrint, kIsWeb;
import 'package:local_auth/local_auth.dart';
import 'package:shared_preferences/shared_preferences.dart';

class AppLock {
  AppLock._();
  static final AppLock instance = AppLock._();

  static const _key = 'security.applock.v1';
  final _auth = LocalAuthentication();

  /// Whether this device can do it at all: hardware present AND something
  /// enrolled. Both matter — a phone with a sensor and no registered finger
  /// can never satisfy the prompt.
  Future<bool> available() async {
    if (kIsWeb) return false;
    try {
      if (!await _auth.isDeviceSupported()) return false;
      if (!await _auth.canCheckBiometrics) return false;
      return (await _auth.getAvailableBiometrics()).isNotEmpty;
    } catch (e) {
      debugPrint('[lock] availability check failed: $e');
      return false;
    }
  }

  Future<bool> enabled() async {
    try {
      return (await SharedPreferences.getInstance()).getBool(_key) ?? false;
    } catch (_) {
      return false;
    }
  }

  Future<void> setEnabled(bool v) async {
    try {
      await (await SharedPreferences.getInstance()).setBool(_key, v);
    } catch (_) {}
  }

  /// Prompt, if the lock is on. True means "let them in".
  ///
  /// Returns true when the lock is off, when the device cannot do it, and
  /// when the plugin throws — see the fail-open note above. It returns false
  /// only for a prompt that was shown and not satisfied.
  Future<bool> unlock({String reason = 'Unlock ThusIldy'}) async {
    if (!await enabled()) return true;
    if (!await available()) return true;
    try {
      return await _auth.authenticate(
        localizedReason: reason,
        options: const AuthenticationOptions(
          // The device PIN or pattern is accepted as well. Biometrics fail
          // for ordinary reasons — a wet thumb, a mask — and a lock with no
          // second route is a lock that strands people.
          biometricOnly: false,
          stickyAuth: true,
          useErrorDialogs: true,
        ),
      );
    } catch (e) {
      debugPrint('[lock] prompt failed: $e');
      return true;
    }
  }
}
