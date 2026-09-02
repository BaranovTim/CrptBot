/// One stock, in detail. The equities answer to the crypto dashboard.
///
/// WHAT IT DELIBERATELY DOES NOT HAVE
///     A recommendation. The crypto dashboard is built around a fitted model
///     and there is none for equities, so rather than leave a blank where the
///     signal sits — which reads as a missing feature — this says why, once,
///     and gets on with what it does have.
///
/// WHY THE NUMBERS MATCH THE SCREENER EXACTLY
///     They are the same row. The page reads the screener's table rather than
///     recomputing anything, so a stock cannot show a P/E of 18 in the results
///     and 21 when you open it.
library;

import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import '../api/client.dart';
import '../api/models.dart';
import '../theme/liquid_obsidian.dart';
import '../widgets/glass.dart';
import '../widgets/patient_loader.dart';
import '../widgets/sparkline.dart';

class StockScreen extends StatefulWidget {
  const StockScreen({super.key, required this.client, required this.symbol,
    this.catalogue, this.onClose});

  final ApiClient client;
  final String symbol;

  /// Field labels and formatting. Passed in when the caller already has it,
  /// so opening a result from the screener costs no extra round trip.
  final ScreenerCatalogue? catalogue;

  /// Non-null when this is a pushed page rather than a tab.
  final VoidCallback? onClose;

  @override
  State<StockScreen> createState() => _StockScreenState();
}

class _StockScreenState extends State<StockScreen> {
  StockDetail? _data;
  ScreenerCatalogue? _cat;
  String? _error;
  String? _lastFailure;
  DateTime _waitingSince = DateTime.now();

  /// The metric groups, in the order they earn their place on a stock page.
  static const _groups = <String, List<String>>{
    'VALUATION': ['market_cap', 'pe', 'eps_ttm', 'dividend_yield',
                  'payout_ratio'],
    'QUALITY': ['roe', 'current_ratio', 'debt_equity'],
    'GROWTH': ['eps_growth_this_year', 'eps_growth_qtr', 'sales_growth_qtr'],
    'TREND': ['rsi14', 'sma20', 'sma50', 'sma200', 'beta'],
    'VOLUME': ['current_volume', 'avg_volume', 'rel_volume'],
    'SHORT INTEREST': ['float_short', 'days_to_cover'],
  };

  @override
  void initState() {
    super.initState();
    _cat = widget.catalogue;
    _load();
  }

  @override
  void didUpdateWidget(covariant StockScreen old) {
    super.didUpdateWidget(old);
    if (old.symbol != widget.symbol) {
      setState(() {
        _data = null;
        _waitingSince = DateTime.now();
      });
      _load();
    }
  }

  Future<void> _load() async {
    try {
      final d = await widget.client.stock(widget.symbol);
      final c = _cat ?? await widget.client.screenerCatalogue();
      if (!mounted) return;
      setState(() {
        _data = d;
        _cat = c;
        _error = null;
        _lastFailure = null;
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

  @override
  Widget build(BuildContext context) {
    if (_error != null) return _errorPanel();
    final d = _data;
    final cat = _cat;
    if (d == null || cat == null) {
      return WaitingPanel(
          since: _waitingSince,
          what: 'Loading ${widget.symbol}',
          onPatienceExhausted: _giveUp);
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
          _header(d, cat),
          const SizedBox(height: 14),
          _chartCard(d),
          const SizedBox(height: Obsidian.gutter),
          _noSignalCard(d),
          const SizedBox(height: Obsidian.gutter),
          ..._metricGroups(d, cat),
          _footer(d),
        ],
      ),
    );
  }

  Widget _header(StockDetail d, ScreenerCatalogue cat) {
    final change = d.metric('change_pct');
    final up = (change ?? 0) >= 0;
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (widget.onClose != null)
          Padding(
            padding: const EdgeInsets.only(right: 6, top: 4),
            child: InkWell(
              onTap: widget.onClose,
              customBorder: const CircleBorder(),
              child: const Icon(Icons.arrow_back_rounded,
                  size: 22, color: Obsidian.outline),
            ),
          ),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(d.symbol, style: Obsidian.displayLg()),
              const SizedBox(height: 2),
              Text('US equity · daily bars',
                  style: Obsidian.body(color: Obsidian.outline, size: 12)),
            ],
          ),
        ),
        Column(
          crossAxisAlignment: CrossAxisAlignment.end,
          children: [
            Text(cat.byId['price']?.format(d.metric('price')) ?? '—',
                style: Obsidian.dataTable(size: 22, w: FontWeight.w700)),
            if (change != null)
              Text('${up ? '+' : ''}${change.toStringAsFixed(2)}%',
                  style: Obsidian.dataTable(
                      size: 13,
                      color: up ? Obsidian.green : Obsidian.red)),
          ],
        ),
      ],
    );
  }

  Widget _chartCard(StockDetail d) => GlassPanel(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(
              children: [
                Text('PRICE · 12 MONTHS',
                    style: Obsidian.labelSm(size: 10.5)),
                const Spacer(),
                if (d.series.isNotEmpty)
                  Text('${d.series.length} sessions',
                      style: Obsidian.labelSm(
                          size: 9.5, color: Obsidian.outline)),
              ],
            ),
            const SizedBox(height: 14),
            if (d.series.isEmpty)
              SizedBox(
                height: 110,
                child: Center(
                  child: Text('No price history available',
                      style:
                          Obsidian.body(color: Obsidian.outline, size: 12)),
                ),
              )
            else
              Sparkline(
                values: d.series,
                color: (d.series.last >= d.series.first)
                    ? Obsidian.green
                    : Obsidian.red,
              ),
          ],
        ),
      );

  /// The absence, stated. See the library doc.
  Widget _noSignalCard(StockDetail d) => Container(
        padding: const EdgeInsets.all(14),
        decoration: BoxDecoration(
          color: Obsidian.amber.withValues(alpha: 0.07),
          borderRadius: BorderRadius.circular(Obsidian.rMd),
          border:
              Border.all(color: Obsidian.amber.withValues(alpha: 0.22)),
        ),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Icon(Icons.info_outline_rounded,
                size: 16, color: Obsidian.amber),
            const SizedBox(width: 10),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('No buy or sell call for stocks',
                      style: Obsidian.body(size: 12.5, color: Obsidian.amber)
                          .copyWith(fontWeight: FontWeight.w600)),
                  const SizedBox(height: 4),
                  Text(d.signalNote,
                      style: Obsidian.body(
                          color: Obsidian.outline, size: 11.5)),
                ],
              ),
            ),
          ],
        ),
      );

  List<Widget> _metricGroups(StockDetail d, ScreenerCatalogue cat) {
    final byId = cat.byId;
    final out = <Widget>[];
    for (final entry in _groups.entries) {
      final present = entry.value
          .where((id) => byId.containsKey(id) && d.metric(id) != null)
          .toList();
      final missing = entry.value
          .where((id) => byId.containsKey(id) && d.metric(id) == null)
          .toList();
      if (present.isEmpty && missing.isEmpty) continue;

      out.add(GlassPanel(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(entry.key, style: Obsidian.labelSm(size: 10.5)),
            const SizedBox(height: 12),
            Wrap(
              spacing: 22,
              runSpacing: 14,
              children: [
                for (final id in present)
                  _cell(byId[id]!, d.metric(id), false),
                for (final id in missing) _cell(byId[id]!, null, true),
              ],
            ),
          ],
        ),
      ));
      out.add(const SizedBox(height: Obsidian.gutter));
    }
    return out;
  }

  Widget _cell(ScreenerField f, double? v, bool missing) => SizedBox(
        width: 96,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(f.label.toUpperCase(),
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
                style: Obsidian.labelSm(size: 8.5)),
            const SizedBox(height: 3),
            Text(missing ? '—' : f.format(v),
                style: Obsidian.dataTable(
                    size: 14,
                    color: missing ? Obsidian.outlineVariant : null)),
          ],
        ),
      );

  Widget _footer(StockDetail d) => Column(
        children: [
          const SizedBox(height: 4),
          GlassPanel(
            padding: const EdgeInsets.all(14),
            onTap: () => _open(d),
            child: Row(
              children: [
                const Icon(Icons.open_in_new_rounded,
                    size: 16, color: Obsidian.primary),
                const SizedBox(width: 10),
                Expanded(
                  child: Text('Open ${d.symbol} on Yahoo Finance',
                      style:
                          Obsidian.body(size: 12.5, color: Obsidian.primary)),
                ),
              ],
            ),
          ),
          const SizedBox(height: 12),
          Text(
              d.builtAt == null
                  ? 'Company figures from SEC filings.'
                  : 'Company figures from SEC filings · table built '
                      '${_ago(d.builtAt!)}',
              textAlign: TextAlign.center,
              style: Obsidian.body(color: Obsidian.outline, size: 10.5)),
        ],
      );

  Future<void> _open(StockDetail d) async {
    try {
      await launchUrl(Uri.parse(d.yahooUrl),
          mode: LaunchMode.externalApplication);
    } catch (_) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
        backgroundColor: Obsidian.surfaceHigh,
        content: Text('Could not open ${d.yahooUrl}', style: Obsidian.body()),
      ));
    }
  }

  Widget _errorPanel() => Center(
        child: Padding(
          padding: const EdgeInsets.all(Obsidian.containerPadding),
          child: GlassPanel(
            padding: const EdgeInsets.all(22),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                const Icon(Icons.cloud_off_rounded,
                    color: Obsidian.outline, size: 34),
                const SizedBox(height: 14),
                Text(widget.symbol, style: Obsidian.headlineMd()),
                const SizedBox(height: 10),
                Text(_error!, style: Obsidian.body(size: 12.5)),
                const SizedBox(height: 18),
                FilledButton(
                  style: FilledButton.styleFrom(
                      backgroundColor: Obsidian.primary,
                      foregroundColor: Obsidian.onPrimary),
                  onPressed: () {
                    setState(() {
                      _error = null;
                      _waitingSince = DateTime.now();
                    });
                    _load();
                  },
                  child: const Text('Retry'),
                ),
              ],
            ),
          ),
        ),
      );

  static String _ago(DateTime t) {
    final d = DateTime.now().difference(t.toLocal());
    if (d.inHours < 24) return '${d.inHours}h ago';
    return '${d.inDays}d ago';
  }
}
