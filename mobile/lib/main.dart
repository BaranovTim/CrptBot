/// TradingBot — a phone-shaped view of the Python stack in this repo.
///
/// It reads. It does not trade, hold keys, or place orders. Every number it
/// shows comes from `serve.py` on your machine, which in turn reads exactly
/// what `monitor.py` reads.
library;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'api/client.dart';
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
  runApp(const TradingBotApp());
}

class TradingBotApp extends StatefulWidget {
  const TradingBotApp({super.key});

  @override
  State<TradingBotApp> createState() => _TradingBotAppState();
}

class _TradingBotAppState extends State<TradingBotApp> {
  final _client = ApiClient();
  bool _entered = false;

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'TradingBot',
      debugShowCheckedModeBanner: false,
      theme: Obsidian.theme(),
      home: _entered
          ? Shell(client: _client)
          : LoginScreen(
              client: _client,
              onEnter: () => setState(() => _entered = true),
            ),
    );
  }
}
