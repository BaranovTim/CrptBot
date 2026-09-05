/// HTTP client for the read-only Python service.
///
/// WHERE THE SERVER IS
///     Hosted, on the droplet, behind TLS. Nothing has to be started on
///     anyone's laptop for the app to work, which is why the default below is
///     the deployed host rather than loopback — an install that has never
///     been to the Profile screen must still find the backend.
///
///     A local backend is a development case, not the normal one, so it is
///     the case that has to say so:
///       flutter run --dart-define=API_BASE=http://192.168.1.20:8787
///     or Profile -> API host inside the app. Both override this default.
library;

import 'dart:convert';

import 'package:http/http.dart' as http;

import 'models.dart';

const _envBase = String.fromEnvironment('API_BASE');
const _envToken = String.fromEnvironment('API_TOKEN');

String defaultApiToken() => _envToken;

/// The deployed backend, used when nothing else has been chosen.
///
/// Hard-coded on purpose. It was `localhost:8787`, which is correct only on
/// the machine running `serve.py` — on a phone it resolves to the phone, so a
/// fresh install failed to connect and the app blamed the network for it.
const _deployedBase = 'https://165.232.127.165.sslip.io';

String defaultApiBase() {
  if (_envBase.isNotEmpty) return _envBase;
  return _deployedBase;
}

/// This account is signed in but has no subscription.
///
/// Separate from ApiException because the app draws a different screen for
/// it: not an error, a price list. 401 means "sign in", 402 means "this is
/// what you would be buying", and conflating them sends people to the wrong
/// place.
class PaywallException implements Exception {
  PaywallException(this.tier);
  final String tier;
  @override
  String toString() => 'subscription required';
}

/// The server rejected the credential — 401.
///
/// Separate from a network failure ON PURPOSE. They are opposite situations:
/// this one means the session is genuinely dead and you must sign in again;
/// an unreachable server means the session is probably fine and the phone
/// just has no route yet. Treating both as "signed out" is what made the app
/// demand a password every time it was opened before wifi settled.
class UnauthorizedException implements Exception {
  UnauthorizedException(this.message);
  final String message;
  @override
  String toString() => message;
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

/// What to say when nothing answered.
///
/// Deliberately does NOT say "start it on your Mac" any more. The backend is
/// hosted and running continuously; telling someone to start a Python process
/// they do not have sends them to fix a machine that is not the problem.
String _unreachable(Object e) => 'No answer from the server.\n\n'
    'It is hosted, so there is nothing to start — this is either your '
    'connection or the server being down.\n\n($e)';

class ApiClient {
  ApiClient({String? base, String? token})
      : base = base ?? defaultApiBase(),
        token = token ?? defaultApiToken();

  String base;

  /// Bearer credential for a deployed server.
  ///
  /// Empty when the backend runs on your own machine bound to loopback —
  /// `serve.py` only demands one when it binds somewhere reachable, and it
  /// refuses to start without one in that case rather than quietly serving
  /// the internet.
  String token;

  Map<String, String> get _headers =>
      token.isEmpty ? const {} : {'Authorization': 'Bearer $token'};

  Future<Map<String, dynamic>> _get(String path,
      {Duration timeout = const Duration(seconds: 20)}) async {
    final uri = Uri.parse('$base$path');
    late http.Response r;
    try {
      r = await http.get(uri, headers: _headers).timeout(timeout);
    } catch (e) {
      // Said plainly, because by the time anyone reads this the app has
      // already retried quietly for five minutes — see `patient_loader`. This
      // is no longer a transient, so the message should help diagnose rather
      // than surface a SocketException to be decoded.
      throw ApiException(_unreachable(e));
    }
    if (r.statusCode == 401) {
      throw UnauthorizedException(
          'The server rejected the token.\n\nProfile → Bearer token, and '
          'paste the value from the server\'s .env file.');
    }
    if (r.statusCode == 402) {
      final j = json.decode(r.body) as Map<String, dynamic>;
      throw PaywallException(j['tier'] as String? ?? 'free');
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

  Future<Map<String, dynamic>> _post(String path,
      Map<String, dynamic> body) async {
    final uri = Uri.parse('$base$path');
    late http.Response r;
    try {
      r = await http
          .post(uri,
              headers: {..._headers, 'Content-Type': 'application/json'},
              body: json.encode(body))
          .timeout(const Duration(seconds: 30));
    } catch (e) {
      throw ApiException(_unreachable(e));
    }
    final j = r.body.isEmpty
        ? <String, dynamic>{}
        : json.decode(r.body) as Map<String, dynamic>;
    if (r.statusCode >= 400) {
      // the server writes these to be read by a person — 'that identifier is
      // already taken', not 'constraint violation'
      throw ApiException(j['error'] as String? ?? 'failed (${r.statusCode})');
    }
    return j;
  }

  /// Sign in. On success the session token replaces whatever `token` held,
  /// so every later call is made as this account.
  Future<Account> login(String identifier, String password) async {
    final j = await _post('/api/auth/login',
        {'identifier': identifier, 'password': password});
    token = j['token'] as String? ?? '';
    return Account.fromJson(j['user'] as Map<String, dynamic>);
  }

  Future<Account> register(String identifier, String password) async {
    final j = await _post('/api/auth/register',
        {'identifier': identifier, 'password': password});
    token = j['token'] as String? ?? '';
    return Account.fromJson(j['user'] as Map<String, dynamic>);
  }

  Future<void> logout() async {
    try {
      await _post('/api/auth/logout', const {});
    } catch (_) {
      // the session is being abandoned either way; a failed call must not
      // leave the user stuck on a screen they asked to leave
    }
    token = '';
  }

  /// Who the current token belongs to, and what it may see.
  Future<Account> me() async =>
      Account.fromJson(await _get('/api/me'));

  /// Reachability, with no credential involved.
  ///
  /// The sign-in screen probes this rather than a gated route, so a bad
  /// password cannot be reported as a broken network.
  /// One indicator's recent history.
  Future<IndicatorSeries> indicator(String key,
      {String? symbol, String? interval, int n = 96}) async {
    final q = [
      'key=$key',
      if (symbol != null) 'symbol=$symbol',
      if (interval != null) 'interval=$interval',
      'n=$n',
    ].join('&');
    return IndicatorSeries.fromJson(await _get('/api/indicator?$q'));
  }

  Future<List<NewsItem>> news({int limit = 20, String? symbol,
      String market = 'crypto'}) async {
    final q = symbol == null ? '' : '&symbol=$symbol';
    final j = await _get('/api/news?limit=$limit$q&market=$market');
    return ((j['items'] as List?) ?? const [])
        .map((e) => NewsItem.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  /// The screener's controls, presets and table age.
  ///
  /// Fetched rather than hardcoded so a filter added on the server appears
  /// without shipping an APK, and an older build simply does not show it
  /// instead of rendering a control that does nothing.
  Future<ScreenerCatalogue> screenerCatalogue(
          {String market = 'stocks'}) async =>
      ScreenerCatalogue.fromJson(
          await _get('/api/screener/catalogue?market=$market'));

  /// Run a screen.
  ///
  /// `preset` and `filters` are exclusive: naming a preset runs the server's
  /// own definition, which is what the very first tap does. As soon as a value
  /// is edited the app sends the filters instead, so what runs is always what
  /// is on screen.
  Future<ScreenerResult> screen({
    String market = 'stocks',
    String? preset,
    List<ScreenerFilter>? filters,
    String? sortBy,
    bool descending = true,
    bool includeUnknown = false,
    int limit = 200,
  }) async {
    final q = StringBuffer('/api/screener?limit=$limit&market=$market');
    if (includeUnknown) q.write('&unknown=1');
    if (sortBy != null) q.write('&sort=$sortBy&dir=${descending ? 'desc' : 'asc'}');
    if (filters != null) {
      final encoded = json.encode(filters.map((f) => f.toJson()).toList());
      q.write('&filters=${Uri.encodeQueryComponent(encoded)}');
    } else if (preset != null) {
      q.write('&preset=$preset');
    }
    return ScreenerResult.fromJson(await _get(q.toString(),
        // A screen touches thousands of rows on a single core; the default
        // 20s is tight for the first uncached call after a rebuild.
        timeout: const Duration(seconds: 45)));
  }

  Future<StockDetail> stock(String symbol, {String interval = '1d'}) async =>
      StockDetail.fromJson(await _get(
          '/api/stock?symbol=$symbol&interval=$interval',
          timeout: const Duration(seconds: 40)));

  Future<List<StockQuote>> stockQuotes(List<String> symbols) async {
    if (symbols.isEmpty) return const [];
    final j = await _get('/api/stock/quotes?symbols=${symbols.join(',')}');
    return ((j['quotes'] as List?) ?? const [])
        .map((e) => StockQuote.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  Future<List<StockQuote>> stockSearch(String q, {int limit = 40}) async {
    final j = await _get(
        '/api/stock/search?q=${Uri.encodeQueryComponent(q)}&limit=$limit');
    return ((j['results'] as List?) ?? const [])
        .map((e) => StockQuote.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  Future<HorizonReport> horizon(String symbol,
          {String market = 'crypto'}) async =>
      HorizonReport.fromJson(
          await _get('/api/horizon?symbol=$symbol&market=$market'));

  /// Queue a fit. Returns immediately — a fit takes about forty minutes, so
  /// the app polls `trainStatus` rather than waiting on the socket.
  Future<Map<String, dynamic>> train(String symbol, String interval,
          {String market = 'crypto'}) =>
      _post('/api/train',
          {'symbol': symbol, 'interval': interval, 'market': market});

  Future<TrainStatus> trainStatus({String? symbol}) async =>
      TrainStatus.fromJson(await _get(
          '/api/train/status${symbol == null ? '' : '?symbol=$symbol'}'));

  Future<Map<String, dynamic>> cancelTraining(String id) =>
      _post('/api/train/cancel', {'id': id});

  Future<List<ScreenerSetup>> screenerSetups(
      {String market = 'stocks', int top = 3}) async {
    final j = await _get('/api/screener/setups?market=$market&top=$top',
        timeout: const Duration(seconds: 45));
    return ((j['setups'] as List?) ?? const [])
        .map((e) => ScreenerSetup.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  Future<Map<String, dynamic>> health() async => _get('/api/health');

  Future<Map<String, dynamic>> plans() async => _get('/api/billing/plans');

  /// Ask for a Stripe checkout URL. Throws with a readable reason while
  /// payments are not configured.
  Future<String> checkout(String plan) async {
    final j = await _post('/api/billing/checkout', {'plan': plan});
    final url = j['url'] as String?;
    if (url == null || url.isEmpty) {
      throw ApiException('The server did not return a checkout link.');
    }
    return url;
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

  /// The next event big enough to reposition the market, which may be well
  /// down the chronological list. Null when there is none in range.
  Future<ScheduledEvent?> nextMajor({int days = 45}) async {
    final j = await _get('/api/calendar?days=$days');
    final m = j['next_major'];
    return m == null
        ? null
        : ScheduledEvent.fromJson(m as Map<String, dynamic>);
  }
}
