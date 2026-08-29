/// "Select Crypto Pair".
///
/// The mockup's NOT TRAINED badge turns out to describe reality exactly:
/// models are fitted for BTCUSDT and nothing else. So the badge is driven by
/// whether a `.joblib` actually exists, and tapping an untrained pair routes
/// to the training screen rather than to a dashboard that would have no
/// honest probability to show.
library;

import 'package:flutter/material.dart';

import '../api/client.dart';
import '../api/models.dart';
import '../api/muted.dart';
import '../api/watchlist.dart';
import 'add_coin_sheet.dart';
import '../theme/liquid_obsidian.dart';
import '../widgets/glass.dart';
import '../widgets/status_dot.dart';

class MarketScreen extends StatefulWidget {
  const MarketScreen({
    super.key,
    required this.client,
    required this.onPick,
  });

  final ApiClient client;
  final void Function(Coin coin) onPick;

  @override
  State<MarketScreen> createState() => _MarketScreenState();
}

class _MarketScreenState extends State<MarketScreen> {
  List<Coin> _coins = const [];
  List<String> _watch = const [];
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final watch = await Watchlist.instance.load();
      final c = await widget.client.coins(symbols: watch);
      if (!mounted) return;
      setState(() {
        _watch = watch;
        _coins = c;
        _error = null;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _error = e.toString());
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
      child: ListView(
        // addRepaintBoundaries: a BackdropFilter samples what is painted
        // BEHIND it, and ListView puts every child in its own RepaintBoundary
        // by default. Inside that layer the backdrop is empty, so the glass
        // panels blur nothing and paint nothing — the screen comes up blank
        // with no error anywhere. Opting out gives the filter a real backdrop.
        addRepaintBoundaries: false,
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.fromLTRB(Obsidian.containerPadding, 8,
            Obsidian.containerPadding, Obsidian.navClearance + 24),
        children: [
          Row(
            children: [
              Expanded(
                child: Text('Select Crypto Pair', style: Obsidian.displayLg()),
              ),
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
            Text(_error!, style: Obsidian.body(color: Obsidian.error)),
          for (final c in _coins) ...[
            Dismissible(
              key: ValueKey(c.symbol),
              direction: DismissDirection.endToStart,
              background: Container(
                alignment: Alignment.centerRight,
                padding: const EdgeInsets.only(right: 24),
                decoration: BoxDecoration(
                  color: Obsidian.red.withValues(alpha: 0.18),
                  borderRadius: BorderRadius.circular(Obsidian.rLg),
                ),
                child: const Icon(Icons.delete_outline_rounded,
                    color: Obsidian.redSoft),
              ),
              onDismissed: (_) => _remove(c),
              child: _coinCard(c),
            ),
            const SizedBox(height: Obsidian.gutter),
          ],
          if (_coins.isNotEmpty)
            Text(
              'Tap the bell to silence a pair\'s alerts, × to stop '
              'following it, or swipe a row. Only pairs with a fitted model '
              'produce a probability — the rest are shown so you can see '
              'what has not been trained.',
              style: Obsidian.body(color: Obsidian.outline, size: 11.5),
            ),
        ],
      ),
    );
  }

  Widget _coinCard(Coin c) {
    final up = (c.changePct ?? 0) >= 0;
    return GlassPanel(
      padding: const EdgeInsets.all(16),
      onTap: () => widget.onPick(c),
      child: Row(
        children: [
          Container(
            width: 52,
            height: 52,
            decoration: BoxDecoration(
              color: Obsidian.surfaceLowest,
              shape: BoxShape.circle,
              border: Border.all(color: Colors.white.withValues(alpha: 0.10)),
            ),
            alignment: Alignment.center,
            child: Text(c.short,
                style: Obsidian.labelSm(color: Obsidian.onSurface, size: 12)),
          ),
          const SizedBox(width: 14),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('${c.name} / USDT',
                    style: Obsidian.bodyLg()
                        .copyWith(fontWeight: FontWeight.w600)),
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
          Column(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              Text(_price(c.price),
                  style: Obsidian.dataTable(size: 15, w: FontWeight.w700)),
              const SizedBox(height: 4),
              Text(
                  '${up ? '+' : ''}${(c.changePct ?? 0).toStringAsFixed(2)}%',
                  style: Obsidian.dataTable(
                      size: 12.5,
                      color: up ? Obsidian.green : Obsidian.red,
                      w: FontWeight.w700)),
            ],
          ),
          const SizedBox(width: 6),
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
              width: 40,
              height: 40,
              child: Icon(icon, size: 19, color: color)),
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

  static String _price(double? v) {
    if (v == null) return '—';
    final s = v.toStringAsFixed(v.abs() >= 100 ? 2 : 4);
    final parts = s.split('.');
    final whole = parts[0].replaceAllMapped(
        RegExp(r'(\d)(?=(\d{3})+$)'), (m) => '${m[1]},');
    return '\$$whole.${parts[1]}';
  }
}
