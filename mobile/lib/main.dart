/// ThusIldy — a phone-shaped view of the Python stack in this repo.
///
/// It reads. It does not trade, hold keys, or place orders. Every number it
/// shows comes from the ThusIldy server, which in turn reads exactly what
/// `monitor.py` reads.
library;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'api/client.dart';
import 'api/models.dart';
import 'api/applock.dart';
import 'api/settings.dart';
import 'screens/login_screen.dart';
import 'screens/shell.dart';
import 'theme/liquid_obsidian.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  SystemChrome.setSystemUIOverlayStyle(const SystemUiOverlayStyle(
    statusBarColor: Colors.transparent,
    statusBarIconBrightness: Brightness.light,
    statusBarBrightness: Brightness.dark,
  ));
  runApp(const ThusIldyApp());
}

class ThusIldyApp extends StatefulWidget {
  const ThusIldyApp({super.key});

  @override
  State<ThusIldyApp> createState() => _ThusIldyAppState();
}

class _ThusIldyAppState extends State<ThusIldyApp> {
  final _client = ApiClient();
  Account? _account;
  bool _restored = false;

  @override
  void initState() {
    super.initState();
    _restore();
  }

  /// Saved host and token first, then ask the server who that token belongs to.
  ///
  /// THREE OUTCOMES, NOT TWO. Collapsing them is what made the app ask for a
  /// password every time it was opened:
  ///
  ///   confirmed      the server answered; use and cache what it said.
  ///   rejected (401) the session is genuinely dead. Clear it, sign in again.
  ///   unreachable    the phone has no route yet — which on a phone is most
  ///                  cold starts, before wifi or data settles. The session is
  ///                  probably fine, so fall back to the last confirmed
  ///                  account and let the screens show their own retry.
  ///
  /// The previous version caught every failure identically and dropped to the
  /// sign-in screen, so a one-second network delay was indistinguishable from
  /// being logged out.
  ///
  /// Falling back to a cached account is safe because it is a cache and not an
  /// authority: it decides which tabs are drawn, never what they return. Every
  /// gated endpoint is still checked server-side on each request.
  /// True while the biometric gate has not been satisfied.
  ///
  /// Starts false and is only ever set by `_unlock` below, so a device that
  /// cannot do biometrics — or a plugin that throws — never leaves anyone
  /// staring at a locked screen they cannot pass. See `applock.dart`.
  bool _locked = false;

  Future<void> _unlock() async {
    if (!await AppLock.instance.enabled()) return;
    if (mounted) setState(() => _locked = true);
    final ok = await AppLock.instance.unlock();
    if (mounted) setState(() => _locked = !ok);
  }

  Future<void> _restore() async {
    await Settings.instance.restore(_client);
    if (_client.token.isNotEmpty) {
      try {
        final me = await _client.me();
        if (me.signedIn) {
          _account = me;
          await Settings.instance.saveAccount(me);
        }
      } on UnauthorizedException {
        // genuinely signed out — do not keep a token the server refuses
        _client.token = '';
        await Settings.instance.clearSession();
      } catch (_) {
        // unreachable, not unauthorised. Keep the session and open the app.
        _account = await Settings.instance.cachedAccount();
      }
    }
    if (mounted) setState(() => _restored = true);
    await _unlock();
  }

  @override
  Widget build(BuildContext context) {
    if (!_restored) {
      return const MaterialApp(
        debugShowCheckedModeBanner: false,
        home: Scaffold(
          backgroundColor: Obsidian.background,
          body: Center(
              child: CircularProgressIndicator(color: Obsidian.primary)),
        ),
      );
    }
    return MaterialApp(
      title: 'ThusIldy',
      debugShowCheckedModeBanner: false,
      theme: Obsidian.theme(),
      home: _locked
          ? const _LockedGate()
          : _account == null
          ? LoginScreen(
              client: _client,
              onEnter: (a) => setState(() => _account = a),
            )
          : Shell(
              client: _client,
              account: _account!,
              onAccountChanged: (a) => setState(() => _account = a),
              onSignOut: () => setState(() => _account = null),
            ),
    );
  }
}


/// What is on screen while the biometric prompt is up, and after it fails.
///
/// Deliberately blank of any account detail: the point of the lock is that
/// somebody holding the phone cannot read the positions behind it, and a
/// "locked" screen listing your P&L would defeat itself.
class _LockedGate extends StatelessWidget {
  const _LockedGate();

  @override
  Widget build(BuildContext context) => Scaffold(
        backgroundColor: Obsidian.background,
        body: Center(
          child: Padding(
            padding: const EdgeInsets.all(32),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                const Icon(Icons.fingerprint_rounded,
                    size: 54, color: Obsidian.primary),
                const SizedBox(height: 18),
                Text('ThusIldy is locked',
                    style: Obsidian.headlineMd()),
                const SizedBox(height: 8),
                Text(
                    'Unlock with your fingerprint, face or device passcode to '
                    'see your positions.',
                    textAlign: TextAlign.center,
                    style: Obsidian.body(color: Obsidian.outline, size: 12.5)),
              ],
            ),
          ),
        ),
      );
}
