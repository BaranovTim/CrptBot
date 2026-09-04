/// Where the backend is, and how to authenticate to it.
///
/// Both live on the device. The host changes when you move the backend off
/// your laptop onto a server; the token exists because `serve.py` refuses to
/// bind anywhere reachable without one.
library;

import 'dart:convert';

import 'package:shared_preferences/shared_preferences.dart';

import 'client.dart';
import 'models.dart';

class Settings {
  Settings._();
  static final Settings instance = Settings._();

  static const _baseKey = 'api.base.v1';
  static const _tokenKey = 'api.token.v1';
  static const _accountKey = 'api.account.v1';
  static const _signalKey = 'signal.sensitivity.v1';
  static const _newsKey = 'news.alerts.v1';
  static const _intervalKey = 'timeframe.last.v1';

  /// Applies whatever was saved to `client`, falling back to the compile-time
  /// defaults when nothing has been stored.
  Future<void> restore(ApiClient client) async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final base = prefs.getString(_baseKey);
      final token = prefs.getString(_tokenKey);
      if (base != null && base.isNotEmpty) client.base = base;
      if (token != null) client.token = token;
    } catch (_) {
      // no storage: the compile-time defaults still work
    }
  }

  /// The last account the server confirmed, kept so the app can open while
  /// offline instead of demanding a password it cannot check.
  ///
  /// This is a CACHE, never an authority. The tier stored here decides which
  /// tabs are drawn when the server is unreachable; the server decides what
  /// any of them actually return. Editing this file would change the menu and
  /// nothing behind it — every gated endpoint still answers 402.
  Future<void> saveAccount(Account a) =>
      _put(_accountKey, json.encode({
            'identifier': a.identifier,
            'tier': a.tier,
            'entitled': a.entitled,
            'operator': a.operator,
            'subscription_ends':
                a.subscriptionEnds == null
                    ? null
                    : a.subscriptionEnds!.millisecondsSinceEpoch / 1000.0,
          }));

  Future<Account?> cachedAccount() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final raw = prefs.getString(_accountKey);
      if (raw == null || raw.isEmpty) return null;
      return Account.fromJson(json.decode(raw) as Map<String, dynamic>);
    } catch (_) {
      return null;              // a corrupt cache just means signing in again
    }
  }

  /// How strong a signal has to be before the app calls it a BUY or SELL.
  ///
  /// strong  — only entries with a real margin over costs (the original rule)
  /// medium  — a thinner margin, more signals
  /// small   — anything profitable after fees at all
  ///
  /// There is no looser option on purpose: below breakeven the model does
  /// produce far more signals, and every one of them loses money on average.
  Future<String> sensitivity() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final v = prefs.getString(_signalKey);
      return (v == 'medium' || v == 'small') ? v! : 'strong';
    } catch (_) {
      return 'strong';          // the conservative default, if storage fails
    }
  }

  Future<void> saveSensitivity(String v) => _put(_signalKey, v);

  /// How much of the news is allowed to buzz.
  ///
  /// Four levels rather than a switch, because "news" is not one thing. These
  /// feeds carry about seventy items a day and the scorer can read maybe half
  /// of them, so the useful question is not whether you want news but which
  /// half — and an on/off switch forces the answer "off".
  ///
  ///   all          every headline that arrives.
  ///   directional  only BULL or BEAR — nothing the scorer could not read,
  ///                and nothing it read as MIXED.
  ///   strong       only STRONG IMPACT, whichever way it points.
  ///   none         silent. Still all there in the News tab.
  ///
  /// Defaults to `all`, which is the behaviour that already existed. Changing
  /// someone's notifications underneath them is not an upgrade.
  Future<String> newsAlerts() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final v = prefs.getString(_newsKey);
      return const {'all', 'directional', 'strong', 'none'}.contains(v)
          ? v!
          : 'all';
    } catch (_) {
      return 'all';            // fail audible, as everywhere else here
    }
  }

  Future<void> saveNewsAlerts(String v) => _put(_newsKey, v);

  /// The last timeframe looked at, remembered across launches AND across
  /// markets.
  ///
  /// ONE SETTING FOR BOTH, deliberately. Opening a stock always landed on 1d
  /// regardless of what you had been reading a moment earlier on a coin —
  /// the timeframe is a question you are asking ("how far ahead am I
  /// looking"), not a property of the instrument, and it should not reset
  /// when you cross markets.
  Future<String> lastInterval() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final v = prefs.getString(_intervalKey);
      return const {'1m', '5m', '15m', '1h', '4h', '1d'}.contains(v)
          ? v!
          : '1h';
    } catch (_) {
      return '1h';
    }
  }

  Future<void> saveInterval(String v) => _put(_intervalKey, v);

  Future<void> clearSession() async {
    await _put(_tokenKey, '');
    await _put(_accountKey, '');
  }

  Future<void> saveBase(String base) => _put(_baseKey, base);

  Future<void> saveToken(String token) => _put(_tokenKey, token);

  Future<void> _put(String key, String value) async {
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString(key, value.trim());
    } catch (_) {
      // in-memory value still applies for this session
    }
  }
}
