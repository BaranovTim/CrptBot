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
  /// The token is asked ABOUT rather than trusted. A session can be revoked,
  /// expire, or have been issued by a server the app is no longer pointed at;
  /// in every one of those cases `me()` fails and the app shows the sign-in
  /// screen, which is the honest outcome. Deciding locally that a stored token
  /// means "signed in" produces an app that looks logged in and 401s on every
  /// screen.
  Future<void> _restore() async {
    await Settings.instance.restore(_client);
    if (_client.token.isNotEmpty) {
      try {
        final me = await _client.me();
        if (me.signedIn) _account = me;
      } catch (_) {
        // unreachable server or a dead session: either way, sign-in screen.
        // Not clearing the token here on purpose — the server may simply be
        // down, and wiping a good session because of a flaky network would
        // make the user re-enter a password for no reason.
      }
    }
    if (mounted) setState(() => _restored = true);
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
      home: _account == null
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
