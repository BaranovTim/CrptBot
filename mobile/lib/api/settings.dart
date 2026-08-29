/// Where the backend is, and how to authenticate to it.
///
/// Both live on the device. The host changes when you move the backend off
/// your laptop onto a server; the token exists because `serve.py` refuses to
/// bind anywhere reachable without one.
library;

import 'package:shared_preferences/shared_preferences.dart';

import 'client.dart';

class Settings {
  Settings._();
  static final Settings instance = Settings._();

  static const _baseKey = 'api.base.v1';
  static const _tokenKey = 'api.token.v1';

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
