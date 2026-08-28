/// The pairs you follow, stored on the device.
///
/// WHY IT IS NOT ON THE SERVER
/// ---------------------------
/// The Python API is read-only: no writes, no auth, nothing worth attacking,
/// and if the process were compromised the worst outcome is that someone
/// learns what your terminal already prints. Holding the watchlist there
/// would have meant adding a write endpoint — trading that property away for
/// a list of a few strings.
///
/// It is also just a preference, and preferences belong to the device. The
/// app sends its list to `/api/coins?symbols=...` and the server answers for
/// whatever it is asked about, remembering nothing.
library;

import 'package:shared_preferences/shared_preferences.dart';

class Watchlist {
  Watchlist._();
  static final Watchlist instance = Watchlist._();

  static const _key = 'watchlist.symbols.v1';

  /// What a fresh install follows. BTCUSDT first because it is the pair with
  /// fitted models — a new user opening a dashboard with no model would see
  /// the training screen and wonder what they did wrong.
  static const defaults = <String>[
    'BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'ADAUSDT',
  ];

  List<String>? _cache;

  Future<List<String>> load() async {
    if (_cache != null) return List.unmodifiable(_cache!);
    try {
      final prefs = await SharedPreferences.getInstance();
      final saved = prefs.getStringList(_key);
      _cache = (saved == null || saved.isEmpty)
          ? List<String>.from(defaults)
          : saved;
    } catch (_) {
      // a device with no usable storage still gets a working app
      _cache = List<String>.from(defaults);
    }
    return List.unmodifiable(_cache!);
  }

  Future<void> _persist() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setStringList(_key, _cache ?? []);
    } catch (_) {
      // in-memory list still works for this session
    }
  }

  Future<List<String>> add(String symbol) async {
    final list = List<String>.from(await load());
    final s = symbol.trim().toUpperCase();
    if (s.isEmpty || list.contains(s)) return List.unmodifiable(list);
    list.add(s);
    _cache = list;
    await _persist();
    return List.unmodifiable(list);
  }

  Future<List<String>> remove(String symbol) async {
    final list = List<String>.from(await load());
    list.remove(symbol.trim().toUpperCase());
    // never leave it empty: an empty market screen looks broken, and there
    // would be no way back to a coin without the picker
    _cache = list.isEmpty ? List<String>.from(defaults) : list;
    await _persist();
    return List.unmodifiable(_cache!);
  }

  Future<bool> contains(String symbol) async =>
      (await load()).contains(symbol.trim().toUpperCase());
}
