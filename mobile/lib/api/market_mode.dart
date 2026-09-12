/// Stocks or crypto — the switch in the top bar.
///
/// WHY THIS IS A STORED PREFERENCE AND NOT SCREEN STATE
///     It decides what the dashboard, the market list, the news feed and the
///     screener are all about. Holding it in one screen's State would mean
///     switching to stocks on the dashboard and finding crypto still on the
///     market tab — two halves of one app disagreeing about what you are
///     looking at.
///
/// WHY IT DEFAULTS TO CRYPTO
///     That is what has fitted models, a running collector, and alerts. The
///     stocks side has a screener and prices; it does not yet have the trained
///     signals. Opening into the half that is less finished would be a worse
///     first impression than opening into the half that works.
library;

import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';

enum MarketMode { crypto, stocks }

/// Which market a symbol belongs to, from its name alone.
///
/// The convention the whole app rests on: every crypto pair this app trades
/// is quoted in USDT, and no stock ticker ends in those four letters. Used
/// wherever a symbol arrives without its market -- a tapped notification, a
/// logged entry -- and the app has to know which dashboard to open.
MarketMode marketFor(String symbol) =>
    symbol.trim().toUpperCase().endsWith('USDT')
        ? MarketMode.crypto
        : MarketMode.stocks;

class MarketModeStore extends ChangeNotifier {
  MarketModeStore._();
  static final MarketModeStore instance = MarketModeStore._();

  static const _key = 'market.mode.v1';

  MarketMode _mode = MarketMode.crypto;
  MarketMode get mode => _mode;

  bool get isStocks => _mode == MarketMode.stocks;

  Future<void> load() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      if (prefs.getString(_key) == 'stocks') {
        _mode = MarketMode.stocks;
        notifyListeners();
      }
    } catch (_) {
      // storage failure keeps the default, which is the working half
    }
  }

  Future<void> set(MarketMode m) async {
    if (m == _mode) return;
    _mode = m;
    notifyListeners();
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString(_key, m == MarketMode.stocks ? 'stocks' : 'crypto');
    } catch (_) {
      // the in-memory value still governs this session
    }
  }

  Future<void> toggle() =>
      set(_mode == MarketMode.crypto ? MarketMode.stocks : MarketMode.crypto);
}
