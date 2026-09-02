/// Your stocks, priced. The equities half of the Market tab.
///
/// WHY THE PRICES COME FROM THE SCREENER TABLE
///     Every symbol in it already has a price and a day's change, computed
///     nightly. Fetching thirty of them live would be thirty round trips to
///     show what is already on disk, and the table is the same source the
///     screener matched on — so a stock cannot be one price in the list and
///     another in the results.
///
///     The cost is that these are yesterday's closes, not live ticks. Said on
///     screen rather than implied, because a stale price presented as live is
///     the kind of number someone acts on.
library;

import 'package:flutter/material.dart';

import '../api/client.dart';
import '../api/models.dart';
import '../api/watchlist.dart';
import '../theme/liquid_obsidian.dart';
import '../widgets/glass.dart';
import '../widgets/patient_loader.dart';

class StockMarketScreen extends StatefulWidget {
  const StockMarketScreen({super.key, required this.client,
    required this.onPick});

  final ApiClient client;
  final ValueChanged<String> onPick;

  @override
  State<StockMarketScreen> createState() => _StockMarketScreenState();
}

class _StockMarketScreenState extends State<StockMarketScreen> {
  List<StockQuote> _quotes = const [];
  List<String> _watch = const [];
  final DateTime _waitingSince = DateTime.now();
  String? _error;
  String? _lastFailure;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final watch = await StockWatchlist.instance.load();
      final q = await widget.client.stockQuotes(watch);
      if (!mounted) return;
      setState(() {
        _watch = watch;
        _quotes = q;
        _error = null;
      });
    } catch (e) {
      if (!mounted) return;
      _lastFailure = '$e';
    }
  }

  void _giveUp() {
    if (!mounted || _error != null) return;
    setState(() => _error = _lastFailure ?? 'No answer from the server.');
  }

  Future<void> _add() async {
    final picked = await showModalBottomSheet<String>(
      context: context,
      backgroundColor: Colors.transparent,
      isScrollControlled: true,
      builder: (_) => _StockPicker(client: widget.client, current: _watch),
    );
    if (picked == null) return;
    await StockWatchlist.instance.add(picked);
    await _load();
  }

  Future<void> _remove(String symbol) async {
    await StockWatchlist.instance.remove(symbol);
    await _load();
    if (!mounted) return;
    ScaffoldMessenger.of(context)
      ..removeCurrentSnackBar()
      ..showSnackBar(SnackBar(
        backgroundColor: Obsidian.surfaceHigh,
        content: Text('$symbol removed', style: Obsidian.body()),
        action: SnackBarAction(
          label: 'Undo',
          textColor: Obsidian.primary,
          onPressed: () async {
            await StockWatchlist.instance.add(symbol);
            await _load();
          },
        ),
      ));
  }

  @override
  Widget build(BuildContext context) {
    if (_error != null) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(Obsidian.containerPadding),
          child: Text(_error!,
              textAlign: TextAlign.center,
              style: Obsidian.body(color: Obsidian.redSoft)),
        ),
      );
    }

    return RefreshIndicator(
      onRefresh: _load,
      backgroundColor: Obsidian.surfaceContainer,
      color: Obsidian.primary,
      child: ListView(
        addRepaintBoundaries: false,
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.fromLTRB(Obsidian.containerPadding, 8,
            Obsidian.containerPadding, Obsidian.navClearance + 24),
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('Stocks', style: Obsidian.displayLg()),
                    const SizedBox(height: 4),
                    Text('Last close, from the nightly table — not live ticks',
                        style: Obsidian.body(
                            color: Obsidian.outline, size: 11.5)),
                  ],
                ),
              ),
              InkWell(
                onTap: _add,
                customBorder: const CircleBorder(),
                child: Container(
                  width: 42,
                  height: 42,
                  decoration: BoxDecoration(
                    color: Obsidian.surfaceHigh.withValues(alpha: 0.6),
                    shape: BoxShape.circle,
                    border: Border.all(
                        color: Colors.white.withValues(alpha: 0.15)),
                  ),
                  child: const Icon(Icons.add_rounded,
                      color: Obsidian.primary, size: 26),
                ),
              ),
            ],
          ),
          const SizedBox(height: 20),
          if (_watch.isEmpty)
            Padding(
              padding: const EdgeInsets.only(top: 50),
              child: Column(
                children: [
                  Text('No stocks followed yet',
                      style: Obsidian.body(color: Obsidian.outline)),
                  const SizedBox(height: 8),
                  Text('Tap + to add one, or open a screener result.',
                      textAlign: TextAlign.center,
                      style: Obsidian.body(
                          color: Obsidian.outlineVariant, size: 11.5)),
                ],
              ),
            )
          else if (_quotes.isEmpty)
            WaitingPanel(
                since: _waitingSince,
                what: 'Loading prices',
                compact: true,
                onPatienceExhausted: _giveUp)
          else
            for (final q in _quotes) _card(q),
        ],
      ),
    );
  }

  Widget _card(StockQuote q) {
    final up = (q.changePct ?? 0) >= 0;
    return Padding(
      padding: const EdgeInsets.only(bottom: Obsidian.gutter),
      child: GlassPanel(
        padding: const EdgeInsets.all(16),
        onTap: () => widget.onPick(q.symbol),
        child: Row(
          children: [
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(q.symbol,
                      style:
                          Obsidian.dataTable(size: 16, w: FontWeight.w700)),
                  const SizedBox(height: 4),
                  Text(
                      q.known
                          ? _sub(q)
                          // A symbol the nightly table has never seen. Kept in
                          // the list rather than dropped, and labelled.
                          : 'Not in the table yet',
                      style: Obsidian.body(
                          color: Obsidian.outline, size: 11)),
                ],
              ),
            ),
            Column(
              crossAxisAlignment: CrossAxisAlignment.end,
              children: [
                Text(q.price == null
                        ? '—'
                        : '\$${q.price!.toStringAsFixed(2)}',
                    style: Obsidian.dataTable(size: 15)),
                if (q.changePct != null)
                  Text(
                      '${up ? '+' : ''}${q.changePct!.toStringAsFixed(2)}%',
                      style: Obsidian.dataTable(
                          size: 12,
                          color: up ? Obsidian.green : Obsidian.red)),
              ],
            ),
            IconButton(
              icon: const Icon(Icons.close_rounded, size: 16),
              color: Obsidian.outline,
              visualDensity: VisualDensity.compact,
              onPressed: () => _remove(q.symbol),
            ),
          ],
        ),
      ),
    );
  }

  static String _sub(StockQuote q) {
    final bits = <String>[];
    if (q.marketCap != null) {
      bits.add(ScreenerField.fromJson(
              {'id': 'c', 'label': 'c', 'group': '', 'kind': 'currency'})
          .format(q.marketCap));
    }
    if (q.rsi14 != null) bits.add('RSI ${q.rsi14!.toStringAsFixed(0)}');
    return bits.isEmpty ? 'US equity' : bits.join(' · ');
  }
}

class _StockPicker extends StatefulWidget {
  const _StockPicker({required this.client, required this.current});

  final ApiClient client;
  final List<String> current;

  @override
  State<_StockPicker> createState() => _StockPickerState();
}

class _StockPickerState extends State<_StockPicker> {
  List<StockQuote> _results = const [];
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    _search('');
  }

  Future<void> _search(String q) async {
    setState(() => _busy = true);
    try {
      final r = await widget.client.stockSearch(q);
      if (mounted) setState(() => _results = r);
    } catch (_) {
      // an empty list is the honest result of a failed search
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) => SafeArea(
        top: false,
        child: Padding(
          padding: EdgeInsets.only(
              left: Obsidian.containerPadding,
              right: Obsidian.containerPadding,
              bottom: MediaQuery.of(context).viewInsets.bottom + 16,
              top: 16),
          child: GlassPanel(
            active: true,
            padding: const EdgeInsets.fromLTRB(20, 20, 20, 10),
            child: ConstrainedBox(
              constraints: BoxConstraints(
                  maxHeight: MediaQuery.of(context).size.height * 0.6),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('ADD A STOCK', style: Obsidian.labelSm(size: 11)),
                  const SizedBox(height: 10),
                  TextField(
                    autofocus: true,
                    textCapitalization: TextCapitalization.characters,
                    style: Obsidian.dataTable(size: 15),
                    decoration: InputDecoration(
                      hintText: 'Ticker, e.g. AAPL',
                      hintStyle:
                          Obsidian.body(color: Obsidian.outlineVariant),
                      isDense: true,
                      suffixIcon: _busy
                          ? const Padding(
                              padding: EdgeInsets.all(12),
                              child: SizedBox(
                                  width: 12,
                                  height: 12,
                                  child: CircularProgressIndicator(
                                      strokeWidth: 1.5,
                                      color: Obsidian.primary)),
                            )
                          : null,
                    ),
                    onChanged: _search,
                  ),
                  const SizedBox(height: 10),
                  Flexible(
                    child: ListView.builder(
                      shrinkWrap: true,
                      itemCount: _results.length,
                      itemBuilder: (_, i) {
                        final r = _results[i];
                        final already = widget.current.contains(r.symbol);
                        return ListTile(
                          dense: true,
                          contentPadding: EdgeInsets.zero,
                          title: Text(r.symbol,
                              style: Obsidian.dataTable(
                                  size: 14,
                                  color: already
                                      ? Obsidian.outlineVariant
                                      : null)),
                          trailing: Text(
                              r.price == null
                                  ? ''
                                  : '\$${r.price!.toStringAsFixed(2)}',
                              style: Obsidian.dataTable(
                                  size: 12, color: Obsidian.outline)),
                          onTap: already
                              ? null
                              : () => Navigator.of(context).pop(r.symbol),
                        );
                      },
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
      );
}
