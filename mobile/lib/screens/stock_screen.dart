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
import '../api/settings.dart';
import '../theme/liquid_obsidian.dart';
import '../widgets/glass.dart';
import '../widgets/patient_loader.dart';
import '../widgets/train_button.dart';
import '../widgets/sparkline.dart';
import '../widgets/timeframe_bar.dart';

class StockScreen extends StatefulWidget {
  const StockScreen({super.key, required this.client, required this.symbol,
    this.catalogue, this.onClose, this.neighbours = const [],
    this.onSwipe});

  final ApiClient client;
  final String symbol;

  /// Field labels and formatting. Passed in when the caller already has it,
  /// so opening a result from the screener costs no extra round trip.
  final ScreenerCatalogue? catalogue;

  /// Non-null when this is a pushed page rather than a tab.
  final VoidCallback? onClose;

  /// The other followed stocks, in order, so a flick moves between them the
  /// way it moves between coins on the crypto dashboard.
  final List<String> neighbours;
  final void Function(int direction)? onSwipe;

  @override
  State<StockScreen> createState() => _StockScreenState();
}

class _StockScreenState extends State<StockScreen> {
  StockDetail? _data;
  ScreenerCatalogue? _cat;
  String? _error;
  String? _lastFailure;
  DateTime _waitingSince = DateTime.now();

  /// Which timeframe the chart is on. Daily by default: it is the only one
  /// with a decade of history behind it, and the only one where a single bar
  /// is not mostly market microstructure.
  String _interval = '1d';

  /// The user's signal-strength setting, applied to stocks exactly as it is
  /// to crypto — one setting, both markets.
  String _sensitivity = 'strong';

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
    Settings.instance.sensitivity().then(
        (v) => mounted ? setState(() => _sensitivity = v) : null);
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
      final d = await widget.client.stock(widget.symbol,
          interval: _interval);
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
      return _swipeable(WaitingPanel(
          since: _waitingSince,
          what: 'Loading ${widget.symbol}',
          onPatienceExhausted: _giveUp));
    }

    return _swipeable(RefreshIndicator(
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
          TimeframeBar(
            timeframes: [
              for (final iv in d.intervals)
                TimeframeInfo.fromJson({
                  'interval': iv,
                  'label': iv.toUpperCase(),
                  // `trained` drives the lock icon. Every equity timeframe is
                  // chartable whether or not a model exists for it, and the
                  // model's absence is already stated in its own card — two
                  // places saying it would be nagging.
                  'trained': true,
                }),
            ],
            selected: d.interval,
            onSelect: (tf) {
              setState(() {
                _interval = tf.interval;
                _data = null;
                _waitingSince = DateTime.now();
              });
              _load();
            },
          ),
          const SizedBox(height: Obsidian.gutter),
          _chartCard(d),
          const SizedBox(height: Obsidian.gutter),
          _recommendation(d),
          const SizedBox(height: Obsidian.gutter),
          // WHAT THE MODEL SAYS COMES FIRST, and when there is a model the
          // company figures move BELOW it and collapse.
          //
          // A trained stock's page is about the prediction; P/E and debt are
          // the context you check afterwards. Leading with six panels of
          // fundamentals buried the one number the page exists for.
          ...(d.trained ? _predictions(d) : const <Widget>[]),
          ..._metricGroups(d, cat, collapsed: d.trained),
          _footer(d),
        ],
      ),
    ));
  }

  /// Horizontal flick -> previous/next followed stock.
  ///
  /// `onHorizontalDragEnd` rather than a PageView, and the threshold is on
  /// VELOCITY not distance — the same reasoning as the crypto dashboard: the
  /// body is a ListView inside a RefreshIndicator, and nesting that in a
  /// PageView makes the two gestures fight until pull-to-refresh stops
  /// working. A slow diagonal drag while scrolling must not change stock.
  Widget _swipeable(Widget child) {
    if (widget.onSwipe == null || widget.neighbours.isEmpty) return child;
    return GestureDetector(
      behavior: HitTestBehavior.opaque,
      onHorizontalDragEnd: (d) {
        final v = d.primaryVelocity ?? 0;
        if (v.abs() < 240) return;              // too slow to be deliberate
        widget.onSwipe!(v < 0 ? 1 : -1);        // drag left = next
      },
      child: child,
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
                Text('PRICE · ${d.interval.toUpperCase()}',
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

  /// The call, drawn exactly as the crypto dashboard draws it.
  ///
  /// Same widget shape, same colours, same sensitivity gate — because it is
  /// the same `Recommendation`, produced by the same model code. A stock is
  /// not a different kind of thing to this app; it is a different market.
  Widget _recommendation(StockDetail d) {
    final r = d.recommendation;
    if (r == null) return _untrained(d);

    final gated = r.action != 'FLAT' &&
        r.action != 'STALE' &&
        r.strength.isNotEmpty &&
        !r.clears(_sensitivity);
    final action = gated ? 'FLAT' : r.action;
    final tone = gated ? 'flat' : r.tone;
    final c = Obsidian.tone(tone);
    final glowing = tone == 'up' || tone == 'down';

    return GlassPanel(
      active: true,
      radius: Obsidian.rLg,
      padding: const EdgeInsets.symmetric(horizontal: 22, vertical: 26),
      glow: glowing ? c : null,
      glowOpacity: 0.35,
      child: Column(
        children: [
          Text('RECOMMENDED ACTION', style: Obsidian.labelSm(size: 11.5)),
          const SizedBox(height: 12),
          Text(action,
              style: Obsidian.displayLg(color: c).copyWith(
                fontSize: 52,
                shadows: glowing
                    ? [BoxShadow(
                        color: c.withValues(alpha: 0.55), blurRadius: 28)]
                    : null,
              )),
          const SizedBox(height: 12),
          Text(
              gated
                  ? 'A ${r.strength} signal is available here. Your setting '
                      'is "$_sensitivity", so it is not shown as a call.'
                  : r.detail,
              textAlign: TextAlign.center,
              style: Obsidian.body(size: 14.5)),
          if (r.pNeeded != null || r.ev != null) ...[
            const SizedBox(height: 14),
            Wrap(
              spacing: 20,
              alignment: WrapAlignment.center,
              children: [
                if (r.ev != null)
                  _stat('EV', '${r.ev! >= 0 ? '+' : ''}'
                      '${r.ev!.toStringAsFixed(3)}%'),
                if (r.pNeeded != null)
                  _stat('NEEDS', r.pNeeded!.toStringAsFixed(3)),
                if (r.sizePct != null)
                  _stat('SIZE', '${r.sizePct!.toStringAsFixed(2)}%'),
              ],
            ),
          ],
          if (d.takeProfit != null && d.stopLoss != null) ...[
            const SizedBox(height: 16),
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceEvenly,
              children: [
                _stat('TAKE PROFIT', d.takeProfit!.toStringAsFixed(2),
                    color: Obsidian.green),
                _stat('STOP LOSS', d.stopLoss!.toStringAsFixed(2),
                    color: Obsidian.red),
              ],
            ),
          ],
        ],
      ),
    );
  }

  Widget _stat(String label, String value, {Color? color}) => Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Text(label, style: Obsidian.labelSm(size: 9)),
          const SizedBox(height: 3),
          Text(value, style: Obsidian.dataTable(size: 13.5, color: color)),
        ],
      );

  /// No model for THIS timeframe yet — the same panel the crypto side shows
  /// for an untrained pair, with the command that fixes it.
  Widget _untrained(StockDetail d) => GlassPanel(
        padding: const EdgeInsets.all(20),
        child: Column(
          children: [
            const Icon(Icons.model_training_rounded,
                size: 30, color: Obsidian.outline),
            const SizedBox(height: 12),
            Text('No model for ${d.symbol} ${d.interval}',
                style: Obsidian.headlineMd()),
            const SizedBox(height: 10),
            Text(d.untrainedNote,
                textAlign: TextAlign.center,
                style: Obsidian.body(color: Obsidian.outline, size: 12)),
            const SizedBox(height: 18),
            // The button, not the command. Copying a shell line into a
            // terminal on another machine was never the point — it was just
            // the only thing available before the server could queue its own
            // work.
            TrainButton(
              client: widget.client,
              symbol: d.symbol,
              interval: d.interval,
              market: 'stocks',
              onTrained: _load,
            ),
          ],
        ),
      );

  /// The model's own readings — the indicator grid the crypto dashboard
  /// shows, from the same payload.
  List<Widget> _predictions(StockDetail d) {
    if (d.indicators.isEmpty) return const [];
    return [
      GlassPanel(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('WHAT THE MODEL IS READING',
                style: Obsidian.labelSm(size: 10.5)),
            const SizedBox(height: 12),
            for (final ind in d.indicators)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 6),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Expanded(
                      flex: 4,
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(ind.label,
                              style: Obsidian.body(size: 12.5)),
                          const SizedBox(height: 2),
                          Text(ind.note,
                              style: Obsidian.body(
                                  color: Obsidian.outline, size: 10.5)),
                        ],
                      ),
                    ),
                    const SizedBox(width: 10),
                    Text(ind.value,
                        style: Obsidian.dataTable(
                            size: 13.5, color: Obsidian.tone(ind.tone))),
                  ],
                ),
              ),
          ],
        ),
      ),
      const SizedBox(height: Obsidian.gutter),
    ];
  }

  /// Collapsed once a model exists — see the note at the call site.
  bool _figuresOpen = false;

  List<Widget> _metricGroups(StockDetail d, ScreenerCatalogue cat,
      {bool collapsed = false}) {
    if (collapsed && !_figuresOpen) {
      return [
        GlassPanel(
          padding: const EdgeInsets.all(14),
          onTap: () => setState(() => _figuresOpen = true),
          child: Row(
            children: [
              const Icon(Icons.business_rounded,
                  size: 16, color: Obsidian.outline),
              const SizedBox(width: 10),
              Expanded(
                child: Text('Company figures',
                    style: Obsidian.body(size: 12.5)),
              ),
              const Icon(Icons.expand_more_rounded,
                  size: 18, color: Obsidian.outline),
            ],
          ),
        ),
        const SizedBox(height: Obsidian.gutter),
      ];
    }
    return _metricGroupsBody(d, cat);
  }

  List<Widget> _metricGroupsBody(StockDetail d, ScreenerCatalogue cat) {
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
