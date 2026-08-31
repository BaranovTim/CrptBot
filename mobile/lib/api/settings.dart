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
