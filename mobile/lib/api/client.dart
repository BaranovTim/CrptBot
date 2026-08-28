/// HTTP client for the read-only Python service.
///
/// The base URL differs per target and there is no way around it: an iOS
/// simulator shares the Mac's loopback, an Android emulator reaches the host
/// through 10.0.2.2, and a physical phone needs the Mac's LAN address. The
/// default is picked per platform and can be overridden at build time with
///   flutter run --dart-define=API_BASE=http://192.168.1.20:8787
/// or edited inside the app on the Profile screen.
library;

import 'dart:convert';
import 'dart:io' show Platform;

import 'package:flutter/foundation.dart' show kIsWeb;
import 'package:http/http.dart' as http;

import 'models.dart';

const _envBase = String.fromEnvironment('API_BASE');

String defaultApiBase() {
  if (_envBase.isNotEmpty) return _envBase;
  if (!kIsWeb && Platform.isAndroid) return 'http://10.0.2.2:8787';
  return 'http://localhost:8787';
}

class ApiException implements Exception {
  ApiException(this.message);
  final String message;
  @override
  String toString() => message;
}

/// This timeframe has no fitted model yet.
///
/// Distinct from ApiException on purpose: it is a normal state, not a fault,
/// and the app routes it to the training screen instead of an error panel.
class UntrainedException implements Exception {
  UntrainedException(this.message);
  final String message;
  @override
  String toString() => message;
}

class ApiClient {
  ApiClient({String? base}) : base = base ?? defaultApiBase();

  String base;

  Future<Map<String, dynamic>> _get(String path,
      {Duration timeout = const Duration(seconds: 20)}) async {
    final uri = Uri.parse('$base$path');
    late http.Response r;
    try {
      r = await http.get(uri).timeout(timeout);
    } catch (e) {
      // the overwhelmingly likely cause is the server not running or the
      // phone being on a different network, so say that rather than
      // surfacing a SocketException the user has to decode
      throw ApiException(
          'Cannot reach $base\n\nStart it on your Mac:\n  python3 serve.py\n\n($e)');
    }
    if (r.statusCode == 409) {
      throw UntrainedException(
          (json.decode(r.body) as Map<String, dynamic>)['error'] as String? ??
              'not trained');
    }
    if (r.statusCode != 200) {
      throw ApiException('$path returned ${r.statusCode}: ${r.body}');
    }
    return json.decode(r.body) as Map<String, dynamic>;
  }

  Future<List<SymbolInfo>> symbols({String? q, int limit = 60}) async {
    final query = [
      if (q != null && q.isNotEmpty) 'q=${Uri.encodeQueryComponent(q)}',
      'limit=$limit',
    ].join('&');
    final j = await _get('/api/symbols?$query');
    return (j['symbols'] as List)
        .map((e) => SymbolInfo.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  Future<List<Coin>> coins({List<String>? symbols}) async {
    final q = (symbols == null || symbols.isEmpty)
        ? ''
        : '?symbols=${symbols.join(',')}';
    final j = await _get('/api/coins$q');
    return (j['coins'] as List)
        .map((e) => Coin.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  Future<Dashboard> dashboard({String? symbol, String? interval}) async =>
      Dashboard.fromJson(await _get('/api/dashboard${_q(symbol, interval)}'));

  Future<Consensus> consensus({String? symbol}) async =>
      Consensus.fromJson(await _get('/api/consensus${_q(symbol, null)}'));

  Future<List<TimeframeInfo>> timeframes({String? symbol}) async {
    final j = await _get('/api/timeframes${_q(symbol, null)}');
    return (j['timeframes'] as List)
        .map((e) => TimeframeInfo.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  static String _q(String? symbol, String? interval, {String extra = ''}) {
    final parts = [
      if (symbol != null) 'symbol=$symbol',
      if (interval != null) 'interval=$interval',
      if (extra.isNotEmpty) extra,
    ];
    return parts.isEmpty ? '' : '?${parts.join('&')}';
  }

  Future<List<double>> chart(
      {String? symbol, String? interval, int n = 96}) async {
    final j = await _get('/api/chart${_q(symbol, interval, extra: 'n=$n')}');
    return (j['points'] as List)
        .map((e) => ((e as Map)['c'] as num).toDouble())
        .toList();
  }

  Future<List<WhaleEvent>> whales({int limit = 20}) async {
    final j = await _get('/api/whales?limit=$limit');
    return (j['events'] as List)
        .map((e) => WhaleEvent.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  Future<TrainingInfo> training(String symbol, {String? interval}) async =>
      TrainingInfo.fromJson(
          await _get('/api/training${_q(symbol, interval)}'));

  /// Alerts newer than `cursor`, plus the cursor to use next time.
  ///
  /// The cursor is epoch milliseconds, not an ISO timestamp, and that is
  /// deliberate on the server side: a `+00:00` offset in a query string has
  /// its `+` decoded as a space, which would make every poll look like a
  /// first poll and re-deliver the whole backlog.
  Future<({List<Alert> alerts, int cursor})> alerts({int? cursor}) async {
    final j = await _get('/api/alerts${cursor == null ? '' : '?after=$cursor'}');
    return (
      alerts: (j['alerts'] as List)
          .map((e) => Alert.fromJson(e as Map<String, dynamic>))
          .toList(),
      cursor: (j['cursor'] as num).toInt(),
    );
  }

  Future<List<ScheduledEvent>> calendar({int days = 21}) async {
    final j = await _get('/api/calendar?days=$days');
    return (j['events'] as List)
        .map((e) => ScheduledEvent.fromJson(e as Map<String, dynamic>))
        .toList();
  }
}
