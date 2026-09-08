/// Live prices for every pair on the Market screen, from one socket.
///
/// WHY A SECOND SERVICE AND NOT `LivePriceService`
///     That one follows the pair the dashboard is showing, on
///     `<symbol>@bookTicker` — one socket per symbol. Market lists a dozen
///     coins at once, and a dozen sockets is a dozen handshakes, a dozen
///     reconnect timers and a dozen things to leak. Binance publishes
///     `!miniTicker@arr`, which pushes EVERY symbol that traded in the last
///     second down a single connection, so the cost here is one socket
///     regardless of how long the watchlist gets.
///
/// WHY IT DOES NOT GO THROUGH THE SERVER
///     The droplet has one core and 967MB, and it already carries the models,
///     the collector and the alert loop. Relaying a price stream through it
///     would put a permanent load there to save the phone a connection it can
///     make perfectly well itself — and the dashboard has been talking to
///     Binance directly for exactly this reason since the beginning.
///
///     The consequence, stated: prices on this screen come from the exchange
///     rather than from the server's cache, so they can be a second or two
///     ahead of what a dashboard payload says. That is the right way round —
///     the fresher number is the true one.
library;

import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart' show debugPrint;
import 'package:web_socket_channel/web_socket_channel.dart';

class MarketTicker {
  MarketTicker({this.emitEvery = const Duration(milliseconds: 400)});

  /// How often listeners hear about changes.
  ///
  /// The stream itself fires every second with hundreds of symbols; rebuilding
  /// a list on each frame would spend more time in layout than in the socket.
  /// Prices are coalesced and emitted on this cadence instead.
  final Duration emitEvery;

  final _out = StreamController<Map<String, double>>.broadcast();
  final Map<String, double> _prices = {};

  WebSocketChannel? _ch;
  StreamSubscription? _sub;
  Timer? _retry, _pump, _watchdog;
  int _attempt = 0;
  bool _closed = false, _connecting = false, _dirty = false;
  DateTime _lastFrame = DateTime.fromMillisecondsSinceEpoch(0);

  Stream<Map<String, double>> get stream => _out.stream;
  Map<String, double> get prices => Map.unmodifiable(_prices);

  /// Whether a frame has arrived recently enough to call this live.
  ///
  /// Derived from when data last ARRIVED, never from socket state: a socket
  /// can sit open and silent, which looks connected and tells you nothing.
  bool get live =>
      DateTime.now().difference(_lastFrame) < const Duration(seconds: 12);

  void start() {
    if (_closed || _ch != null || _connecting) return;
    _connect();
    _pump ??= Timer.periodic(emitEvery, (_) {
      if (!_dirty) return;
      _dirty = false;
      _out.add(Map.unmodifiable(_prices));
    });
    _watchdog ??= Timer.periodic(const Duration(seconds: 15), (_) {
      if (_closed || live) return;
      debugPrint('[ticker] silent socket, reconnecting');
      _scheduleReconnect();
    });
  }

  void _connect() {
    _connecting = true;
    _retry?.cancel();
    // Every symbol, one connection. The payload is an array of mini tickers,
    // each `{s: symbol, c: close, ...}`.
    final uri = Uri.parse('wss://fstream.binance.com/ws/!miniTicker@arr');
    try {
      final ch = WebSocketChannel.connect(uri);
      _ch = ch;
      _lastFrame = DateTime.now();
      _sub = ch.stream.listen(
        _onMessage,
        onError: (e) {
          debugPrint('[ticker] socket error: $e');
          _scheduleReconnect();
        },
        onDone: _scheduleReconnect,
        cancelOnError: true,
      );
      _attempt = 0;
    } catch (e) {
      debugPrint('[ticker] connect failed: $e');
      _scheduleReconnect();
    } finally {
      _connecting = false;
    }
  }

  void _onMessage(dynamic raw) {
    _lastFrame = DateTime.now();
    try {
      final decoded = json.decode(raw as String);
      if (decoded is! List) return;
      for (final e in decoded) {
        if (e is! Map) continue;
        final sym = e['s'] as String?;
        final close = double.tryParse('${e['c']}');
        if (sym == null || close == null) continue;
        _prices[sym] = close;
      }
      _dirty = true;
    } catch (e) {
      debugPrint('[ticker] bad frame: $e');
    }
  }

  void _scheduleReconnect() {
    if (_closed) return;
    _sub?.cancel();
    _sub = null;
    _ch = null;
    _connecting = false;
    // Backed off and capped. A phone that has lost wifi must not hammer a
    // handshake it cannot complete, and must recover promptly when it returns.
    final delay = Duration(seconds: [1, 2, 4, 8, 15][
        _attempt.clamp(0, 4)]);
    _attempt++;
    _retry?.cancel();
    _retry = Timer(delay, _connect);
  }

  void dispose() {
    _closed = true;
    _retry?.cancel();
    _pump?.cancel();
    _watchdog?.cancel();
    _sub?.cancel();
    _ch?.sink.close();
    _out.close();
  }
}
