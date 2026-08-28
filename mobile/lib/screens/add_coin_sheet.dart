/// The picker behind the "+" on the market screen.
///
/// Lists every USD-M perpetual Binance is currently trading — 524 of them at
/// the time of writing — ordered by 24h volume rather than alphabetically. A
/// search for "b" should offer BTC before BAKE; alphabetical order buries
/// every pair anyone actually wants behind three-letter tokens nobody has
/// heard of.
///
/// Only real symbols can be picked, which is deliberate: typing a pair by
/// hand is how you end up watching PEPEUSDT, a contract that does not exist
/// because the listed one is 1000PEPEUSDT.
library;

import 'dart:async';

import 'package:flutter/material.dart';

import '../api/client.dart';
import '../api/models.dart';
import '../api/watchlist.dart';
import '../theme/liquid_obsidian.dart';
import '../widgets/glass.dart';

/// The caller reloads unconditionally when this returns.
///
/// A bottom sheet can be dismissed by dragging, which never reaches a
/// `Navigator.pop` with a result — so a "did anything change" flag would be
/// silently false exactly when the user swiped the sheet away after adding a
/// coin. Reloading a four-row list is cheaper than getting that wrong.
Future<void> showAddCoinSheet(
  BuildContext context, {
  required ApiClient client,
  required List<String> current,
}) =>
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (_) => _AddCoinSheet(client: client, current: current),
    );

class _AddCoinSheet extends StatefulWidget {
  const _AddCoinSheet({required this.client, required this.current});

  final ApiClient client;
  final List<String> current;

  @override
  State<_AddCoinSheet> createState() => _AddCoinSheetState();
}

class _AddCoinSheetState extends State<_AddCoinSheet> {
  final _search = TextEditingController();
  Timer? _debounce;
  List<SymbolInfo> _rows = const [];
  late Set<String> _picked = widget.current.toSet();
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load('');
  }

  @override
  void dispose() {
    _debounce?.cancel();
    _search.dispose();
    super.dispose();
  }

  void _onQuery(String q) {
    // debounced: 524 symbols are cached server-side for six hours, but a
    // request per keystroke is still a request per keystroke
    _debounce?.cancel();
    _debounce = Timer(const Duration(milliseconds: 250), () => _load(q));
  }

  Future<void> _load(String q) async {
    setState(() => _loading = true);
    try {
      final rows = await widget.client.symbols(q: q, limit: 80);
      if (!mounted) return;
      setState(() {
        _rows = rows;
        _error = null;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _loading = false;
      });
    }
  }

  Future<void> _toggle(SymbolInfo s) async {
    final on = _picked.contains(s.symbol);
    final next = on
        ? await Watchlist.instance.remove(s.symbol)
        : await Watchlist.instance.add(s.symbol);
    if (!mounted) return;
    setState(() => _picked = next.toSet());
  }

  static String _vol(double? v) {
    if (v == null || v <= 0) return '';
    if (v >= 1e9) return '\$${(v / 1e9).toStringAsFixed(2)}B';
    if (v >= 1e6) return '\$${(v / 1e6).toStringAsFixed(0)}M';
    return '\$${(v / 1e3).toStringAsFixed(0)}K';
  }

  @override
  Widget build(BuildContext context) {
    final inset = MediaQuery.of(context).viewInsets.bottom;
    return Padding(
      padding: EdgeInsets.only(bottom: inset),
      child: FractionallySizedBox(
        heightFactor: 0.86,
        child: Container(
          decoration: BoxDecoration(
            color: Obsidian.surfaceContainer,
            borderRadius: const BorderRadius.vertical(
                top: Radius.circular(Obsidian.rXl)),
            border: Border.all(color: Colors.white.withValues(alpha: 0.12)),
          ),
          padding: const EdgeInsets.fromLTRB(
              Obsidian.containerPadding, 10, Obsidian.containerPadding, 0),
          child: Column(
            children: [
              Container(
                width: 40,
                height: 4,
                margin: const EdgeInsets.only(bottom: 16),
                decoration: BoxDecoration(
                  color: Obsidian.outline.withValues(alpha: 0.5),
                  borderRadius: BorderRadius.circular(2),
                ),
              ),
              Row(
                children: [
                  Text('Add a pair', style: Obsidian.headlineMd()),
                  const Spacer(),
                  Text('${_picked.length} followed',
                      style: Obsidian.labelSm(size: 11)),
                ],
              ),
              const SizedBox(height: 14),
              GlassField(
                controller: _search,
                hint: 'Search BTC, SOL, DOGE…',
                onChanged: _onQuery,
              ),
              const SizedBox(height: 8),
              Text(
                'USD-M perpetuals listed on Binance, most traded first.',
                style: Obsidian.body(color: Obsidian.outline, size: 11.5),
              ),
              const SizedBox(height: 10),
              Expanded(child: _body()),
            ],
          ),
        ),
      ),
    );
  }

  Widget _body() {
    if (_error != null) {
      return Center(
        child: Text(_error!,
            textAlign: TextAlign.center,
            style: Obsidian.body(color: Obsidian.error, size: 12.5)),
      );
    }
    if (_loading && _rows.isEmpty) {
      return const Center(
          child: CircularProgressIndicator(color: Obsidian.primary));
    }
    if (_rows.isEmpty) {
      return Center(
          child: Text('Nothing matches that.', style: Obsidian.body()));
    }
    return ListView.separated(
      padding: EdgeInsets.only(
          bottom: MediaQuery.of(context).padding.bottom + 16),
      itemCount: _rows.length,
      separatorBuilder: (_, _) => Divider(
          height: 1, color: Colors.white.withValues(alpha: 0.05)),
      itemBuilder: (_, i) {
        final s = _rows[i];
        final on = _picked.contains(s.symbol);
        return InkWell(
          onTap: () => _toggle(s),
          child: Padding(
            padding: const EdgeInsets.symmetric(vertical: 13),
            child: Row(
              children: [
                Container(
                  width: 38,
                  height: 38,
                  alignment: Alignment.center,
                  decoration: BoxDecoration(
                    color: Obsidian.surfaceLowest,
                    shape: BoxShape.circle,
                    border: Border.all(
                        color: Colors.white.withValues(alpha: 0.08)),
                  ),
                  child: Text(
                    s.base.length > 4 ? s.base.substring(0, 4) : s.base,
                    style: Obsidian.labelSm(
                        color: Obsidian.onSurface, size: 9.5),
                  ),
                ),
                const SizedBox(width: 14),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text('${s.base} / ${s.quote}',
                          style: Obsidian.bodyLg().copyWith(
                              fontWeight: FontWeight.w600)),
                      const SizedBox(height: 2),
                      Text(
                          [s.symbol, _vol(s.volume24h)]
                              .where((x) => x.isNotEmpty)
                              .join('  ·  '),
                          style: Obsidian.labelSm(
                              color: Obsidian.outline, size: 10)),
                    ],
                  ),
                ),
                Icon(
                  on ? Icons.check_circle_rounded : Icons.add_circle_outline,
                  color: on ? Obsidian.green : Obsidian.outline,
                  size: 24,
                ),
              ],
            ),
          ),
        );
      },
    );
  }
}
