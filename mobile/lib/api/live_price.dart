/// Live price, straight from Binance to the phone.
///
/// WHY NOT THROUGH THE PYTHON SERVER
/// ---------------------------------
/// The dashboard used to read price from `/api/dashboard`, which fetches the
/// forming bar over REST and caches it for 5 seconds, behind a 10-second app
/// poll. Worst case that is a 15-second-old price next to a Binance app
/// showing it live.
///
/// The fix is not to poll harder. Price is public and unauthenticated and
/// Binance pushes it, so the app subscribes directly and the server never sees
/// this traffic. The server keeps doing the thing only it can do — the
/// probability, the barriers, the indicators — all of which change once per
/// closed bar and do not need to be fast.
///
/// WHY bookTicker AND NOT aggTrade
/// -------------------------------
/// This was measured, not chosen. Against `fstream.binance.com` (USD-M
/// futures), over 8 seconds each:
///
///     /ws/btcusdt@aggTrade      0 messages
///     /ws/btcusdt@ticker        0 messages
///     /ws/btcusdt@bookTicker    6994 messages
///     /stream?streams=a/b       handshake 101, then silence
///
/// The combined `/stream?streams=` form and the trade streams complete their
/// handshake and then deliver nothing at all — no error, no close frame, just
/// a socket that looks healthy and says nothing. That is the worst possible
/// failure mode to debug, and it is why the connection state below is derived
/// from when a FRAME last arrived rather than from whether the socket is open.
///
/// `bookTicker` is best bid/ask, pushed on every book change. The mid sits
/// inside the spread and tracks the last trade within a tick on BTC perp, so
/// it is if anything more responsive than the number Binance shows.
///
/// The 24h change is NOT taken from here — that needs the `@ticker` stream,
/// which is one of the silent ones. It comes from the server's REST copy,
/// where being a few seconds stale does not matter.
///
/// THROTTLING IS NOT OPTIONAL
/// --------------------------
/// 6994 messages in 8 seconds is ~875/second. Calling setState on each would
/// render the app unusable and empty the battery, so frames are coalesced and
/// emitted at a fixed rate. The newest quote always wins; nothing queues.
library;

import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart' show debugPrint;
import 'package:web_socket_channel/web_socket_channel.dart';

class LiveTick {
  const LiveTick({this.price, this.changePct, required this.at});

  final double? price;

  /// Always null from the socket — see the note above. The dashboard falls
  /// back to the server's value.
  final double? changePct;
  final DateTime at;
}

class LivePriceService {
  LivePriceService({this.symbol = 'BTCUSDT', this.emitEvery = const Duration(milliseconds: 120)});

  final String symbol;

  /// How often the UI is allowed to hear about a new quote.
  final Duration emitEvery;

  final _out = StreamController<LiveTick>.broadcast();
  WebSocketChannel? _ch;
  StreamSubscription? _sub;
  Timer? _retry;
  Timer? _pump;
  Timer? _watchdog;
  int _attempt = 0;
  bool _closed = false;
  bool _connecting = false;

  double? _pending;
  DateTime _lastFrame = DateTime.fromMillisecondsSinceEpoch(0);
  LiveTick _last = LiveTick(at: DateTime.fromMillisecondsSinceEpoch(0));

  Stream<LiveTick> get stream => _out.stream;
  LiveTick get last => _last;

  /// Derived from when a FRAME last arrived, never from socket state.
  ///
  /// A Binance futures socket can sit open and completely silent — that is the
  /// documented-by-experiment behaviour of the trade streams above — so "is
  /// the socket open" is not the same question as "is the price live", and
  /// only the second one is worth showing a user.
  bool get connected =>
      _last.price != null &&
      DateTime.now().difference(_lastFrame) < const Duration(seconds: 10);

  void start() {
    _closed = false;
    _pump ??= Timer.periodic(emitEvery, (_) => _emit());
    // A WATCHDOG, not belt-and-braces.
    //
    // The failure that makes this necessary was observed directly: the socket
    // stayed ESTABLISHED, onError never fired, onDone never fired, and frames
    // simply stopped. The price froze on screen with no indication anything
    // was wrong. Reconnect logic hung off onDone/onError cannot help there,
    // because neither ever arrives — so silence itself has to be the trigger.
    _watchdog ??= Timer.periodic(const Duration(seconds: 5), (_) {
      if (_closed || _ch == null) return;
      if (DateTime.now().difference(_lastFrame) > const Duration(seconds: 15)) {
        debugPrint('[live] silent for 15s — forcing reconnect');
        _scheduleReconnect();
      }
    });
    if (_ch == null && !_connecting) _connect();
  }

  void _connect() {
    _connecting = true;
    _retry?.cancel();
    final uri = Uri.parse(
        'wss://fstream.binance.com/ws/${symbol.toLowerCase()}@bookTicker');
    try {
      final ch = WebSocketChannel.connect(uri);
      _ch = ch;
      // treat the connection as alive from this instant, so the watchdog
      // gives the handshake a full window before declaring it silent
      _lastFrame = DateTime.now();
      _sub = ch.stream.listen(
        _onMessage,
        onError: (e) {
          debugPrint('[live] socket error: $e');
          _scheduleReconnect();
        },
        onDone: () {
          debugPrint('[live] socket closed (${ch.closeCode})');
          _scheduleReconnect();
        },
        cancelOnError: true,
      );
    } catch (e) {
      debugPrint('[live] connect threw: $e');
      _scheduleReconnect();
    }
  }

  void _onMessage(dynamic raw) {
    _attempt = 0; // a frame proves the link is healthy
    _lastFrame = DateTime.now();
    try {
      final d = json.decode(raw as String);
      if (d is! Map) return;
      // single-stream endpoint delivers the event unwrapped; the combined one
      // wraps it in {stream, data} — accept either so the URL can change
      final e = (d['data'] is Map ? d['data'] : d) as Map;
      final bid = double.tryParse('${e['b']}');
      final ask = double.tryParse('${e['a']}');
      if (bid == null && ask == null) return;
      // mid, so a one-sided book update does not make the price jump
      _pending = (bid != null && ask != null) ? (bid + ask) / 2 : (bid ?? ask);
    } catch (_) {
      // one malformed frame is not worth tearing the socket down
    }
  }

  void _emit() {
    final p = _pending;
    if (p == null) return;
    _pending = null;
    _last = LiveTick(price: p, changePct: null, at: DateTime.now());
    if (!_out.isClosed) _out.add(_last);
  }

  void _scheduleReconnect() {
    _sub?.cancel();
    _sub = null;
    _ch = null;
    _connecting = false;
    if (_closed) return;
    final delay = Duration(seconds: (1 << _attempt).clamp(1, 30));
    _attempt = (_attempt + 1).clamp(0, 5);
    _retry = Timer(delay, _connect);
  }

  Future<void> dispose() async {
    _closed = true;
    _retry?.cancel();
    _pump?.cancel();
    _watchdog?.cancel();
    await _sub?.cancel();
    await _ch?.sink.close();
    await _out.close();
  }
}
