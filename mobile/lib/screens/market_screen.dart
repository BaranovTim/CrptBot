/// The market page: your coins, in the order you keep them.
///
/// The mockup's NOT TRAINED badge turns out to describe reality exactly:
/// models are fitted for BTCUSDT and nothing else. So the badge is driven by
/// whether a `.joblib` actually exists, and tapping an untrained pair routes
/// to the training screen rather than to a dashboard that would have no
/// honest probability to show.
library;

import 'dart:async';

import 'package:flutter/material.dart';

import '../api/client.dart';
import '../api/format.dart';
import '../api/market_ticker.dart';
import '../api/models.dart';
import '../api/widgets.dart';
import '../api/trades.dart';
import '../api/muted.dart';
import '../api/settings.dart';
import '../api/watchlist.dart';
import 'add_coin_sheet.dart';
import '../theme/liquid_obsidian.dart';
import '../widgets/glass.dart';
import '../widgets/patient_loader.dart';
import '../widgets/momentum_panel.dart';
import '../widgets/record_panel.dart';
import '../widgets/signals_panel.dart';
import '../widgets/status_dot.dart';

class MarketScreen extends StatefulWidget {
  const MarketScreen({
    super.key,
    required this.client,
    required this.onPick,
    this.onOpenSignal,
    required this.onOrderChanged,
  });

  final ApiClient client;
  final void Function(Coin coin) onPick;

  /// Open a live call on the pair AND timeframe it belongs to. A call is a
  /// property of both, so landing on the wrong timeframe would show a
  /// different answer from the one that was tapped.
  final void Function(LiveSignal)? onOpenSignal;

  /// The dashboard swipes through this list, so the shell has to be told
  /// when the order changes — otherwise a drag here would silently disagree
  /// with what a swipe there does.
  final VoidCallback onOrderChanged;

  @override
  State<MarketScreen> createState() => _MarketScreenState();
}

class _MarketScreenState extends State<MarketScreen> {
  List<Coin> _coins = const [];
  List<String> _watch = const [];
  String? _error;

  /// See `patient_loader.dart` — a failure is only a failure after five
  /// minutes of trying, not after one timeout.
  DateTime _waitingSince = DateTime.now();
  String? _lastFailure;
  Timer? _retry;

  LiveSignals? _signals;

  /// This week's momentum rotation; null until it loads, and left null on
  /// a plan that does not include it (the panel then says it is loading
  /// only until the first failure hides it -- see `_momentumFailed`).
  Momentum? _momentum;
  bool _momentumFailed = false;

  /// The live record of the calls (api/ledger.py), shown for the level the
  /// person follows. Hidden, like the rotation, if the server has none.
  LiveRecord? _record;
  bool _recordFailed = false;
  String _sensitivity = 'strong';

  Future<void> _loadRecord() async {
    try {
      final level = await Settings.instance.sensitivity();
      final r = await widget.client.record();
      if (mounted) setState(() { _record = r; _sensitivity = level; });
    } catch (_) {
      // an older server without the ledger: the panel goes away
      if (mounted) setState(() => _recordFailed = true);
    }
  }

  /// Open trades as `SYMBOL:interval` keys (see `SignalsPanel.held`), so
  /// the calls list tags the row you are actually in, not every row of the
  /// coin.
  Set<String> _held = const {};

  Future<void> _loadHeld() async {
    final all = await Trades.instance.load();
    final open = all
        .where((t) => t.isOpen)
        .map((t) => SignalsPanel.heldKey(t.symbol, t.interval))
        .toSet();
    if (mounted) setState(() => _held = open);
  }

  /// Live prices, straight from the exchange. One socket for the whole list.
  final MarketTicker _ticker = MarketTicker();
  Map<String, double> _live = const {};
  StreamSubscription<Map<String, double>>? _liveSub;

  Future<void> _loadMomentum() async {
    try {
      final m = await widget.client.momentum();
      if (mounted) setState(() => _momentum = m);
    } catch (_) {
      // Not on this plan, or the server is older than the rotation: the
      // panel goes away rather than spinning for ever.
      if (mounted) setState(() => _momentumFailed = true);
    }
  }

  Future<void> _loadSignals() async {
    unawaited(_loadMomentum());
    unawaited(_loadRecord());
    try {
      final s = await widget.client.signals();
      if (mounted) setState(() => _signals = s);
      unawaited(Widgets.instance
          .pushMarket(_coins, calls: s, live: _ticker.prices));
    } catch (_) {
      // The list above is the screen's job; a missing calls panel says so
      // itself rather than taking the page down.
    }
  }

  @override
  void initState() {
    super.initState();
    _load();
    _loadSignals();
    _loadHeld();
    _ticker.start();
    _liveSub = _ticker.stream.listen((p) {
      if (!mounted) return;
      setState(() => _live = p);
      // Whichever screen is open is the one that has to notice a level
      // being hit; this one sees every listed coin.
      unawaited(Trades.instance.checkLive(p, client: widget.client));
      // Cheap: writes a small blob and asks the launcher to redraw. Android
      // coalesces these, and it is the only path that keeps a widget current
      // while the app is open.
      unawaited(Widgets.instance
          .pushMarket(_coins, calls: _signals, live: p));
    });
  }

  @override
  void dispose() {
    _liveSub?.cancel();
    _ticker.dispose();
    _retry?.cancel();
    super.dispose();
  }

  void _giveUp() {
    if (!mounted || _error != null) return;
    setState(() => _error = _lastFailure ?? 'No answer from the server.');
  }

  Future<void> _load() async {
    _retry?.cancel();
    try {
      final watch = await Watchlist.instance.load();
      final c = await widget.client.coins(symbols: watch);
      if (!mounted) return;
      setState(() {
        _watch = watch;
        _coins = c;
        _error = null;
        _lastFailure = null;
        _waitingSince = DateTime.now();
      });
      // Point the socket at the list actually on screen. `watch` reconnects
      // only when the set changes, so calling it on every reload is free.
      _ticker.watch(c.map((x) => x.symbol));
    } catch (e) {
      if (!mounted) return;
      _lastFailure = e.toString();
      _retry = Timer(const Duration(seconds: 15), _load);
    }
  }

  Future<void> _openPicker() async {
    await showAddCoinSheet(context, client: widget.client, current: _watch);
    await _load();
  }

  Future<void> _remove(Coin c) async {
    await Watchlist.instance.remove(c.symbol);
    await Muted.instance.forget(c.symbol);
    await _load();
    if (!mounted) return;
    ScaffoldMessenger.of(context)
      ..removeCurrentSnackBar()
      ..showSnackBar(SnackBar(
        backgroundColor: Obsidian.surfaceHigh,
        behavior: SnackBarBehavior.floating,
        margin: const EdgeInsets.fromLTRB(16, 0, 16, Obsidian.navClearance + 8),
        content: Text('${c.short} removed', style: Obsidian.body()),
        action: SnackBarAction(
          label: 'Undo',
          textColor: Obsidian.primary,
          onPressed: () async {
            await Watchlist.instance.add(c.symbol);
            await _load();
          },
        ),
      ));
  }

  @override
  Widget build(BuildContext context) {
    return RefreshIndicator(
      onRefresh: _load,
      backgroundColor: Obsidian.surfaceContainer,
      color: Obsidian.primary,
      // ReorderableListView, because the order is now functional: the
      // dashboard swipes through this list, so dragging a coin up decides
      // which pair is one flick away.
      //
      // buildDefaultDragHandles is false and each row carries its own handle.
      // The default on mobile is long-press-anywhere, which collides with the
      // card's own tap target and is invisible until you discover it — the
      // same mistake swipe-to-delete made on this screen.
      child: ReorderableListView(
        buildDefaultDragHandles: false,
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.fromLTRB(Obsidian.containerPadding, 8,
            Obsidian.containerPadding, Obsidian.navClearance + 24),
        onReorderItem: _onReorder,
        header: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
          // No title. The list is the page; the one control it needs is
          // the add button, kept where it was so the hand still finds it.
          Row(
            mainAxisAlignment: MainAxisAlignment.end,
            children: [
              InkWell(
                onTap: _openPicker,
                customBorder: const CircleBorder(),
                child: Container(
                  width: 52,
                  height: 52,
                  decoration: BoxDecoration(
                    color: Obsidian.surfaceHigh.withValues(alpha: 0.6),
                    shape: BoxShape.circle,
                    border:
                        Border.all(color: Colors.white.withValues(alpha: 0.15)),
                  ),
                  child: const Icon(Icons.add_rounded,
                      color: Obsidian.primary, size: 26),
                ),
              ),
            ],
          ),
            const SizedBox(height: 22),
            if (_error != null)
              Text(_error!, style: Obsidian.body(color: Obsidian.error))
            else if (_coins.isEmpty)
              WaitingPanel(
                  since: _waitingSince,
                  what: 'Loading your coins',
                  compact: true,
                  onPatienceExhausted: _giveUp),
          ],
        ),
        footer: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            if (_coins.isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(top: 6),
                child: Text(
                  'Drag ⠿ to reorder — the dashboard swipes through this list '
                  'in this order. Tap the bell to silence a pair, × to stop '
                  'following it. Only pairs with a fitted model produce a '
                  'probability.',
                  style: Obsidian.body(color: Obsidian.outline, size: 11.5),
                ),
              ),
            const SizedBox(height: 26),
            // BELOW THE LIST, deliberately. The list above is what you
            // follow; this is what is actually calling, which is a different
            // question and can name a pair you do not follow at all.
            SignalsPanel(
              data: _signals,
              held: _held,
              onOpen: (sig) => widget.onOpenSignal?.call(sig),
            ),
            // How those calls have actually done since the model went live:
            // right under them, because it is the answer to "should I trust
            // the list above".
            if (!_recordFailed) ...[
              const SizedBox(height: 26),
              RecordPanel(data: _record, level: _sensitivity),
            ],
            // The weekly rotation: a basket, not a call, so it sits under
            // the calls rather than among them. A pick opens on the daily
            // chart -- a 30-day return is a daily question.
            if (!_momentumFailed) ...[
              const SizedBox(height: 26),
              MomentumPanel(
                data: _momentum,
                onOpen: (sym) => widget.onOpenSignal?.call(LiveSignal.fromJson(
                    {'symbol': sym, 'interval': '1d', 'action': 'FLAT'})),
              ),
            ],
          ],
        ),
        children: [
          for (var i = 0; i < _coins.length; i++)
            Padding(
              key: ValueKey(_coins[i].symbol),
              padding: const EdgeInsets.only(bottom: Obsidian.gutter),
              child: _coinCard(_coins[i], i),
            ),
        ],
      ),
    );
  }

  /// `newIndex` is post-removal — `onReorderItem` has already adjusted it.
  Future<void> _onReorder(int oldIndex, int newIndex) async {
    // Move the visible list first so the row lands where the finger left it.
    // The store is the source of truth; a later _load() reconciles.
    setState(() => _coins.insert(newIndex, _coins.removeAt(oldIndex)));
    await Watchlist.instance.reorder(oldIndex, newIndex);
    widget.onOrderChanged();
  }

  Widget _coinCard(Coin c, int index) {
    final up = (c.changePct ?? 0) >= 0;
    // The socket's price when it has one, the payload's otherwise. The
    // percentage stays with the payload: it is a 24h figure the exchange
    // computes, not something to re-derive from a single tick.
    final price = _live[c.symbol] ?? c.price;
    return GlassPanel(
      padding: const EdgeInsets.all(16),
      onTap: () => widget.onPick(c),
      child: Row(
        children: [
          Container(
            width: 44,
            height: 44,
            decoration: BoxDecoration(
              color: Obsidian.surfaceLowest,
              shape: BoxShape.circle,
              border: Border.all(color: Colors.white.withValues(alpha: 0.10)),
            ),
            alignment: Alignment.center,
            child: Text(c.short,
                style: Obsidian.labelSm(color: Obsidian.onSurface, size: 12)),
          ),
          const SizedBox(width: 11),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                // Explicit size, not the bodyLg default. Once the row gained
                // a drag handle, a bell and a close button the name column is
                // ~93dp wide, and bodyLg needs more than that for eight
                // characters — so the longest-priced pair, BTC, was the one
                // rendering as "BTC/US…".
                Text('${c.short}/USDT',
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: Obsidian.bodyLg().copyWith(
                        fontWeight: FontWeight.w600, fontSize: 14.5)),
                const SizedBox(height: 6),
                Row(
                  children: [
                    StatusDot(
                        live: c.trained,
                        size: 8,
                        color: c.listed ? null : Obsidian.red),
                    const SizedBox(width: 6),
                    // Flexible + a COUNT rather than the full list: six
                    // timeframes spelled out ("1m 5m 15m 1h 4h 1d") ran 17px
                    // past the price column. The count says the same thing
                    // and cannot grow.
                    Flexible(
                      child: Text(
                          !c.listed
                              ? 'NOT LISTED ON BINANCE'
                              : (c.trained
                                  ? 'TRAINED · ${c.trainedIntervals.length} '
                                      'TIMEFRAME'
                                      '${c.trainedIntervals.length == 1 ? "" : "S"}'
                                  : 'NOT TRAINED'),
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: Obsidian.labelSm(
                              color: !c.listed
                                  ? Obsidian.redSoft
                                  : (c.trained
                                      ? Obsidian.greenDim
                                      : Obsidian.outline),
                              size: 9.5)),
                    ),
                  ],
                ),
              ],
            ),
          ),
          // Bounded, and scaled down rather than allowed to grow.
          //
          // BTC's price is several characters wider than SOL's, and an
          // unbounded price column took that width out of the pair name —
          // so the row that mattered most read "BTC/US…". The identity of a
          // row must never be the thing that gets clipped to make room for
          // a number that can shrink instead.
          ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 104),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.end,
              children: [
                FittedBox(
                  fit: BoxFit.scaleDown,
                  alignment: Alignment.centerRight,
                  child: Text(_price(price),
                      maxLines: 1,
                      style:
                          Obsidian.dataTable(size: 15, w: FontWeight.w700)),
                ),
                const SizedBox(height: 4),
                Text(
                    '${up ? '+' : ''}${(c.changePct ?? 0).toStringAsFixed(2)}%',
                    maxLines: 1,
                    style: Obsidian.dataTable(
                        size: 12.5,
                        color: up ? Obsidian.green : Obsidian.red,
                        w: FontWeight.w700)),
              ],
            ),
          ),
          const SizedBox(width: 2),
          ReorderableDragStartListener(
            index: index,
            child: const SizedBox(
              width: 34,
              height: 40,
              child: Icon(Icons.drag_handle_rounded,
                  size: 19, color: Obsidian.outline),
            ),
          ),
          // Swipe-to-delete already worked, and was invisible: the only hint
          // sat BELOW every row, so on a list longer than a screen you would
          // never meet it. A gesture nobody discovers is not a feature.
          // These two buttons say out loud what the row can do.
          _rowButton(
            icon: Muted.instance.isMuted(c.symbol)
                ? Icons.notifications_off_rounded
                : Icons.notifications_active_rounded,
            color: Muted.instance.isMuted(c.symbol)
                ? Obsidian.outline
                : Obsidian.green,
            tooltip: 'Alerts for ${c.short}',
            onTap: () async {
              // coin-level from here: the market row is about the coin, and
              // per-timeframe control lives behind the dashboard's bell
              await Muted.instance.toggle(c.symbol);
              if (mounted) setState(() {});
            },
          ),
          _rowButton(
            icon: Icons.close_rounded,
            color: Obsidian.outline,
            tooltip: 'Stop following ${c.short}',
            onTap: () => _confirmRemove(c),
          ),
        ],
      ),
    );
  }

  Widget _rowButton({
    required IconData icon,
    required Color color,
    required String tooltip,
    required VoidCallback onTap,
  }) =>
      Semantics(
        button: true,
        label: tooltip,
        child: InkWell(
          onTap: onTap,
          customBorder: const CircleBorder(),
          // 40px: below ~44 a target next to a tappable card gets hit by
          // accident, and this one removes a coin
          child: SizedBox(
              width: 36,
              height: 40,
              child: Icon(icon, size: 18, color: color)),
        ),
      );

  /// Confirm before dropping a pair.
  ///
  /// The swipe has an Undo snackbar and needs no dialog; a button sitting one
  /// finger-width from "open this coin" does, because a mis-tap there is
  /// silent and you would not know which coin vanished.
  Future<void> _confirmRemove(Coin c) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: Obsidian.surfaceContainer,
        title: Text('Stop following ${c.short}?', style: Obsidian.headlineMd()),
        content: Text(
            'It disappears from this list. Nothing is deleted on the server, '
            'and you can add it back from the + button.',
            style: Obsidian.body(color: Obsidian.outline)),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(ctx, false),
              child: Text('Keep', style: Obsidian.body())),
          TextButton(
              onPressed: () => Navigator.pop(ctx, true),
              child: Text('Remove',
                  style: Obsidian.body(color: Obsidian.redSoft))),
        ],
      ),
    );
    if (ok == true) await _remove(c);
  }

  static String _price(double? v) => priceText(v);
}
