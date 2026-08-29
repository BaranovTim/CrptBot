/// Which pairs are allowed to buzz your phone.
///
/// WHY THIS IS A MUTE LIST AND NOT A SUBSCRIBE LIST
/// -----------------------------------------------
/// The two are not symmetric when the stored value is missing. A subscribe
/// list that fails to load leaves every coin silent — the app looks broken in
/// the one way you would never think to check, because nothing appearing is
/// indistinguishable from nothing happening. A mute list that fails to load
/// leaves every coin audible: noisier, but visibly wrong, and you can fix what
/// you can see.
///
/// So the default is "notify", a coin is silenced only by an explicit tap, and
/// every storage failure below falls back to the empty set.
library;

import 'package:shared_preferences/shared_preferences.dart';

class Muted {
  Muted._();
  static final Muted instance = Muted._();

  static const _key = 'notifications.muted.v1';

  Set<String>? _cache;

  Future<Set<String>> load() async {
    if (_cache != null) return _cache!;
    try {
      final prefs = await SharedPreferences.getInstance();
      _cache = (prefs.getStringList(_key) ?? const <String>[]).toSet();
    } catch (_) {
      _cache = <String>{};                 // audible, not silent — see above
    }
    return _cache!;
  }

  /// Synchronous read for the notification path.
  ///
  /// `_pollAlerts` runs every 20s and must decide instantly whether to show
  /// an alert; awaiting shared_preferences there would mean an alert that
  /// arrives before the first `load()` completes slips through unfiltered.
  /// Callers prime this with `load()` at startup, and until they have, this
  /// reports nothing muted — which is the same fail-audible default.
  bool isMuted(String symbol) =>
      _cache?.contains(symbol.trim().toUpperCase()) ?? false;

  Future<bool> toggle(String symbol) async {
    final s = symbol.trim().toUpperCase();
    final set = Set<String>.from(await load());
    final nowMuted = !set.contains(s);
    if (nowMuted) {
      set.add(s);
    } else {
      set.remove(s);
    }
    _cache = set;
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setStringList(_key, set.toList());
    } catch (_) {
      // the in-memory set still governs this session
    }
    return nowMuted;
  }

  /// Dropping a coin from the watchlist drops its mute with it.
  ///
  /// Otherwise re-adding a coin you had silenced months ago brings back a
  /// setting you have no memory of making, and the bell is off for reasons
  /// that look like a bug.
  Future<void> forget(String symbol) async {
    final set = Set<String>.from(await load());
    if (set.remove(symbol.trim().toUpperCase())) {
      _cache = set;
      try {
        final prefs = await SharedPreferences.getInstance();
        await prefs.setStringList(_key, set.toList());
      } catch (_) {}
    }
  }
}
