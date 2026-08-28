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
    if (r.statusCode != 200) {
      throw ApiException('$path returned ${r.statusCode}: ${r.body}');
    }
    return json.decode(r.body) as Map<String, dynamic>;
  }

  Future<List<Coin>> coins() async {
    final j = await _get('/api/coins');
    return (j['coins'] as List)
        .map((e) => Coin.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  Future<Dashboard> dashboard() async =>
      Dashboard.fromJson(await _get('/api/dashboard'));

  Future<List<double>> chart({int n = 96}) async {
    final j = await _get('/api/chart?n=$n');
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

  Future<TrainingInfo> training(String symbol) async =>
      TrainingInfo.fromJson(await _get('/api/training?symbol=$symbol'));
}
