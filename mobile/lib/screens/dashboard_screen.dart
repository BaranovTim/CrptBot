/// The bot dashboard.
///
/// Laid out to match `bot_dashboard_updated/screen.png`, with three
/// substitutions where the mockup's sample state and the real backend
/// disagree — and in each case the backend wins:
///
///   RECOMMENDED ACTION   the mockup shows a confident BUY. The card renders
///                        whatever `evaluate()` actually decided, which today
///                        is usually FLAT, because EV after costs does not
///                        clear the threshold. A dashboard that always says
///                        BUY is a screenshot, not an instrument.
///
///   BOT ACTIVE           becomes WATCHING. Nothing here places an order, and
///                        a badge implying autonomy would be the single most
///                        misleading pixel in the app.
///
///   Data by TradingView  becomes Binance, which is where the bars are
///                        actually from.
library;

import 'dart:async';

import 'package:flutter/material.dart';

import 'package:url_launcher/url_launcher.dart';

import '../api/client.dart';
import '../api/live_price.dart';
import '../api/models.dart';
import '../theme/liquid_obsidian.dart';
import '../widgets/glass.dart';
import '../widgets/sparkline.dart';
import '../widgets/timeframe_bar.dart';
import '../widgets/status_dot.dart';

class DashboardScreen extends StatefulWidget {
  const DashboardScreen({
    super.key,
    required this.client,
    required this.live,
    required this.interval,
    required this.onPickInterval,
    required this.onNeedsTraining,
  });

  final ApiClient client;

  /// The selected timeframe. Each one is a separately fitted model, so this
  /// changes which model answers, not just which candles are drawn.
  final String interval;
  final ValueChanged<String> onPickInterval;

  /// Called when the chosen timeframe has no model — the training screen is
  /// the honest destination, not an error panel.
  final ValueChanged<String> onNeedsTraining;

  /// Price arrives here from Binance directly, not through the server. The
  /// server's copy is up to 15s stale by the time it reaches the phone, which
  /// is the whole reason this exists.
  final LivePriceService live;

  @override
  State<DashboardScreen> createState() => _DashboardScreenState();
}

class _DashboardScreenState extends State<DashboardScreen> {
  Dashboard? _data;
  List<double> _series = const [];
  List<WhaleEvent> _whales = const [];
  Consensus? _consensus;
  String? _error;
  String? _untrained;
  Timer? _timer;
  StreamSubscription<LiveTick>? _tick;
  LiveTick? _live;

  @override
  void initState() {
    super.initState();
    // repaint on every trade. setState on a tick is cheap here because the
    // only things that move are the price, the change badge and the two
    // re-anchored barrier rows
    _tick = widget.live.stream.listen((t) {
      if (mounted) setState(() => _live = t);
    });
    _load();
    // the service caches per closing bar, so this costs a JSON round trip,
    // not a feature recompute
    _timer = Timer.periodic(const Duration(seconds: 10), (_) => _load(quiet: true));
  }

  Future<void> _loadConsensus() async {
    try {
      final c = await widget.client.consensus();
      if (mounted) setState(() => _consensus = c);
    } catch (_) {
      // supporting context; its absence is not worth an error state
    }
  }

  @override
  void didUpdateWidget(covariant DashboardScreen old) {
    super.didUpdateWidget(old);
    if (old.interval != widget.interval) {
      setState(() {
        _data = null;
        _series = const [];
        _untrained = null;
      });
      _load();
    }
  }

  @override
  void dispose() {
    _timer?.cancel();
    _tick?.cancel();
    super.dispose();
  }

  /// Hand off to the Binance app, falling back to the web trade page.
  Future<void> _openBinance(String symbol) async {
    final app = Uri.parse('bnc://app.binance.com/futures/$symbol');
    final web = Uri.parse('https://www.binance.com/en/futures/$symbol');
    try {
      if (await canLaunchUrl(app)) {
        await launchUrl(app);
        return;
      }
    } catch (_) {
      // canLaunchUrl throws if the scheme is not declared; fall through
    }
    await launchUrl(web, mode: LaunchMode.externalApplication);
  }

  Future<void> _load({bool quiet = false}) async {
    try {
      final iv = widget.interval;
      final results = await Future.wait([
        widget.client.dashboard(interval: iv),
        widget.client.chart(interval: iv, n: 96),
        widget.client.whales(limit: 6),
      ]);
      if (!mounted) return;
      setState(() {
        _data = results[0] as Dashboard;
        _series = results[1] as List<double>;
        _whales = results[2] as List<WhaleEvent>;
        _error = null;
        _untrained = null;
      });
      // Deliberately NOT awaited with the rest.
      //
      // Consensus builds a dashboard per trained timeframe, and each one is a
      // full feature computation the first time it is asked for. Putting it in
      // the same Future.wait held the whole screen on a spinner for twelve
      // seconds. It is supporting context, so it arrives when it arrives.
      unawaited(_loadConsensus());
    } on UntrainedException catch (e) {
      // a normal state, not a fault: this timeframe simply has no model yet
      if (!mounted) return;
      setState(() {
        _untrained = e.message;
        _error = null;
      });
    } catch (e) {
      if (!mounted || quiet) return;
      setState(() => _error = e.toString());
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_untrained != null) return _untrainedPanel();
    final d = _data;
    if (d == null) return _placeholder();

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
          _header(d),
          const SizedBox(height: 14),
          TimeframeBar(
            timeframes: d.timeframes,
            selected: d.interval,
            onSelect: (tf) => tf.trained
                ? widget.onPickInterval(tf.interval)
                : widget.onNeedsTraining(tf.interval),
          ),
          const SizedBox(height: Obsidian.gutter),
          ..._costWarning(d),
          ..._whaleAlerts(),
          _chartCard(d),
          const SizedBox(height: Obsidian.panelGap),
          _indicatorGrid(d),
          const SizedBox(height: Obsidian.gutter),
          _recommendation(d),
          const SizedBox(height: Obsidian.gutter),
          _levels(d),
          const SizedBox(height: Obsidian.gutter),
          ..._consensusPanel(),
          _note(d),
        ],
      ),
    );
  }

  // ---------------------------------------------------------------- header
  Widget _header(Dashboard d) {
    final s = d.status;
    final c = s.active ? Obsidian.green : Obsidian.outline;
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(d.pair.replaceAll(' / ', '/'), style: Obsidian.displayLg()),
              const SizedBox(height: 4),
              Text('${d.interval} windows · ${d.htf} context · ${s.detail}',
                  style: Obsidian.body(size: 13)),
            ],
          ),
        ),
        Padding(
          padding: const EdgeInsets.only(top: 6),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              StatusDot(live: s.active),
              const SizedBox(width: 8),
              Text(s.label, style: Obsidian.labelSm(color: c, size: 12)),
            ],
          ),
        ),
      ],
    );
  }

  /// Shown when fees eat too much of the barrier span to trade this
  /// timeframe, whatever the probability says.
  ///
  /// Placed ABOVE the odds on purpose. A confident percentage sitting over a
  /// trade that cannot clear its own costs is the most expensive thing this
  /// screen could show.
  List<Widget> _costWarning(Dashboard d) {
    final c = d.timeframes
        .where((t) => t.interval == d.interval)
        .map((t) => t.cost)
        .firstOrNull;
    if (c == null || (!c.untradeable && !c.marginal)) return const [];
    final colour = c.untradeable ? Obsidian.red : Obsidian.tone('warn');
    return [
      GlassPanel(
        padding: const EdgeInsets.all(16),
        glow: c.untradeable ? colour : null,
        glowOpacity: 0.2,
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Icon(
                c.untradeable
                    ? Icons.block_rounded
                    : Icons.warning_amber_rounded,
                color: colour,
                size: 22),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                      c.untradeable
                          ? 'Costs exceed the whole range at ${d.interval}'
                          : 'Costs take a large bite at ${d.interval}',
                      style: Obsidian.bodyLg(color: colour)
                          .copyWith(fontWeight: FontWeight.w600)),
                  const SizedBox(height: 6),
                  Text(c.note, style: Obsidian.body(size: 12.5)),
                ],
              ),
            ),
          ],
        ),
      ),
      const SizedBox(height: Obsidian.gutter),
    ];
  }

  // ----------------------------------------------------------- whale alert
  List<Widget> _whaleAlerts() {
    // mechanical transactions are filtered out by the same rule the terminal
    // uses: code F is tax withholding on vesting, nobody decided anything
    final real = _whales.where((w) => !w.mechanical).take(1).toList();
    if (real.isEmpty) return const [];
    final w = real.first;
    return [
      GlassPanel(
        padding: const EdgeInsets.all(18),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Container(
              width: 44,
              height: 44,
              decoration: BoxDecoration(
                color: Obsidian.red.withValues(alpha: 0.15),
                shape: BoxShape.circle,
                border: Border.all(
                    color: Obsidian.red.withValues(alpha: 0.45)),
              ),
              child: const Icon(Icons.warning_amber_rounded,
                  color: Obsidian.redSoft, size: 22),
            ),
            const SizedBox(width: 14),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('Whale Activity Detected',
                      style: Obsidian.bodyLg().copyWith(
                          fontWeight: FontWeight.w600)),
                  const SizedBox(height: 6),
                  Text(w.describe, style: Obsidian.body(size: 14)),
                  const SizedBox(height: 6),
                  Text('${w.impact} · ${w.note}',
                      style: Obsidian.labelSm(
                          color: Obsidian.outline, size: 10.5)),
                ],
              ),
            ),
          ],
        ),
      ),
      const SizedBox(height: Obsidian.gutter),
    ];
  }

  // ------------------------------------------------------------ chart card
  double? get _livePrice => _live?.price;

  Widget _chartCard(Dashboard d) {
    final change = _live?.changePct ?? d.changePct ?? 0;
    final up = change >= 0;
    final tone = up ? Obsidian.green : Obsidian.red;
    final live = d.live;
    final shown = _livePrice ?? d.price;
    return GlassPanel(
      padding: const EdgeInsets.fromLTRB(18, 18, 18, 12),
      onTap: () => _openBinance(d.symbol),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('24H', style: Obsidian.labelSm(size: 12)),
                    const SizedBox(height: 4),
                    Text('TIMEFRAME', style: Obsidian.labelSm(size: 12)),
                  ],
                ),
              ),
              Column(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  Text(_money(shown), style: Obsidian.displayLg()),
                  const SizedBox(height: 2),
                  Text('${up ? '+' : ''}${change.toStringAsFixed(2)}%',
                      style: Obsidian.dataTable(
                          color: tone, size: 15, w: FontWeight.w700)),
                ],
              ),
            ],
          ),
          const SizedBox(height: 8),
          Sparkline(values: _series, color: tone),
          const SizedBox(height: 6),
          // two lines, not one: sharing a row with the credit was ellipsising
          // the volume reading into "vol…", which reads as a rendering fault
          if (live != null) _liveStrip(live),
          const SizedBox(height: 6),
          Row(
            mainAxisAlignment: MainAxisAlignment.end,
            children: [
              StatusDot(live: widget.live.connected, size: 6),
              const SizedBox(width: 6),
              Text(
                  widget.live.connected
                      ? 'LIVE from Binance · tap to open'
                      : 'Binance · tap to open',
                  style: Obsidian.labelSm(
                      color: Obsidian.outline.withValues(alpha: 0.75),
                      size: 9.5)),
            ],
          ),
        ],
      ),
    );
  }

  /// The forming bar, from the intra-bar watch. Everything here changes
  /// second to second, unlike the indicators below which are last-close.
  Widget _liveStrip(LiveReading l) {
    // three items, not five: this shares a row with the data credit and the
    // fourth was being ellipsised into "v…", which reads as a glitch
    final parts = <String>[
      if (l.movePct != null)
        '${l.movePct! >= 0 ? '+' : ''}${l.movePct!.toStringAsFixed(2)}%',
      if (l.moveAtr != null) '${l.moveAtr!.toStringAsFixed(2)}atr',
      if (l.volumePace != null) 'vol ${l.volumePace!.toStringAsFixed(1)}x',
    ];
    return Text(parts.join('  ·  '),
        overflow: TextOverflow.ellipsis,
        style: Obsidian.labelSm(
            color: l.beyondSpike ? Obsidian.green : Obsidian.outline,
            size: 10));
  }

  // ------------------------------------------------------------ indicators
  Widget _indicatorGrid(Dashboard d) {
    final items = d.indicators.take(4).toList();
    if (items.isEmpty) return const SizedBox.shrink();
    return Column(
      children: [
        for (var i = 0; i < items.length; i += 2)
          Padding(
            padding: const EdgeInsets.only(bottom: Obsidian.panelGap),
            // IntrinsicHeight so the pair matches height when one note wraps
            // to two lines and the other does not. CrossAxisAlignment.stretch
            // ALONE cannot do this inside a ListView: stretch asks children to
            // fill the cross axis, the Row's height there is unbounded, and
            // the layout fails with "BoxConstraints forces an infinite height"
            // — which takes the whole screen down, not just this row.
            child: IntrinsicHeight(
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Expanded(child: _statCard(items[i])),
                  const SizedBox(width: Obsidian.panelGap),
                  Expanded(
                    child: i + 1 < items.length
                        ? _statCard(items[i + 1])
                        : const SizedBox.shrink(),
                  ),
                ],
              ),
            ),
          ),
      ],
    );
  }

  Widget _statCard(Indicator ind) {
    final c = Obsidian.tone(ind.tone);
    return GlassPanel(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          Row(
            children: [
              Container(
                width: 26,
                height: 26,
                decoration: BoxDecoration(
                  color: c.withValues(alpha: 0.14),
                  borderRadius: BorderRadius.circular(Obsidian.rSm + 2),
                ),
                child: Icon(_iconFor(ind.key), size: 15, color: c),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: Text(ind.label,
                    style: Obsidian.labelSm(color: c, size: 10)),
              ),
            ],
          ),
          const SizedBox(height: 12),
          Text(ind.value, style: Obsidian.displayLg().copyWith(fontSize: 30)),
          const SizedBox(height: 6),
          Text(ind.note, style: Obsidian.body(size: 12.5)),
        ],
      ),
    );
  }

  IconData _iconFor(String key) => switch (key) {
        'rsi' => Icons.bar_chart_rounded,
        'htf' => Icons.show_chart_rounded,
        'vol' => Icons.waves_rounded,
        'volume' => Icons.equalizer_rounded,
        _ => Icons.insights_rounded,
      };

  // -------------------------------------------------------- recommendation
  Widget _recommendation(Dashboard d) {
    final r = d.recommendation;
    final c = Obsidian.tone(r.tone);
    final glowing = r.tone == 'up' || r.tone == 'down';
    return GlassPanel(
      active: true,
      radius: Obsidian.rLg,
      padding: const EdgeInsets.symmetric(horizontal: 22, vertical: 26),
      glow: glowing ? c : null,
      glowOpacity: 0.35,
      child: Column(
        children: [
          Text('RECOMMENDED ACTION',
              style: Obsidian.labelSm(size: 11.5)),
          const SizedBox(height: 12),
          Text(
            r.action,
            style: Obsidian.displayLg(color: c).copyWith(
              fontSize: 52,
              shadows: glowing
                  ? [BoxShadow(color: c.withValues(alpha: 0.55), blurRadius: 28)]
                  : null,
            ),
          ),
          const SizedBox(height: 12),
          Text(r.detail,
              textAlign: TextAlign.center, style: Obsidian.body(size: 14.5)),
          if (r.sizePct != null && r.sizePct! > 0) ...[
            const SizedBox(height: 10),
            Text('quarter-Kelly size ${r.sizePct!.toStringAsFixed(2)}% of equity',
                style: Obsidian.labelSm(color: c, size: 10.5)),
          ],
        ],
      ),
    );
  }

  // -------------------------------------------------------------- levels
  Widget _levels(Dashboard d) {
    final px = _livePrice ?? d.price;
    // The model fixed the barrier DISTANCE at the last close, not the price
    // it is measured from. So TP/SL follow the live price: these are the
    // levels for an entry right now. The probability below is still the one
    // read at the close — that is what the footnote says.
    final tp = d.liveTakeProfit(_livePrice);
    final sl = d.liveStopLoss(_livePrice);
    return GlassPanel(
      padding: EdgeInsets.zero,
      child: Column(
        children: [
          _row('Current Price', _money(px), Obsidian.onSurface),
          _divider(),
          _row('Take Profit (TP1)', _money(tp), Obsidian.green),
          _divider(),
          _row('Stop Loss (SL)', _money(sl), Obsidian.red),
          if (d.windowPUp != null) ...[
            _divider(),
            // the recommendation's own window, so the two never disagree
            _row('Chance up (${d.windowBars ?? '?'} × ${d.interval})',
                '${(d.windowPUp! * 100).toStringAsFixed(1)}%',
                Obsidian.primary),
          ],
        ],
      ),
    );
  }

  Widget _row(String label, String value, Color c) => Padding(
        padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 16),
        child: Row(
          children: [
            Expanded(child: Text(label, style: Obsidian.bodyLg())),
            Text(value,
                style: Obsidian.dataTable(
                    color: c, size: 15, w: FontWeight.w700)),
          ],
        ),
      );

  // "Row separators should be 1px lines at 5% white opacity."
  Widget _divider() =>
      Divider(height: 1, thickness: 1, color: Colors.white.withValues(alpha: 0.05));

  /// Every fitted timeframe, side by side.
  ///
  /// Worth showing because disagreement is the useful part: rows that all
  /// say the same thing are one piece of evidence repeated, rows that
  /// conflict are a reason to wait. It is a view, not a model — nothing here
  /// feeds back into a prediction.
  List<Widget> _consensusPanel() {
    final c = _consensus;
    if (c == null || c.rows.length < 2) return const [];
    return [
      GlassPanel(
        padding: const EdgeInsets.fromLTRB(18, 16, 18, 14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Text('ACROSS TIMEFRAMES', style: Obsidian.labelSm(size: 11)),
                const Spacer(),
                if (c.agreement != null)
                  Text('${(c.agreement! * 100).round()}% agree',
                      style: Obsidian.labelSm(
                          color: c.agreement! >= 0.8
                              ? Obsidian.green
                              : Obsidian.onSurfaceVariant,
                          size: 10.5)),
              ],
            ),
            const SizedBox(height: 12),
            for (final r in c.rows)
              Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: Row(
                  children: [
                    SizedBox(
                      width: 42,
                      child: Text(r.interval,
                          style: Obsidian.dataTable(
                              size: 13,
                              color: r.interval == widget.interval
                                  ? Obsidian.green
                                  : Obsidian.onSurfaceVariant)),
                    ),
                    Expanded(child: _consensusBar(r)),
                    const SizedBox(width: 10),
                    SizedBox(
                      width: 52,
                      child: Text(
                          r.pUp == null
                              ? '—'
                              : '${(r.pUp! * 100).toStringAsFixed(1)}%',
                          textAlign: TextAlign.right,
                          style: Obsidian.dataTable(
                              size: 13,
                              color: r.stale
                                  ? Obsidian.outline
                                  : Obsidian.onSurface)),
                    ),
                  ],
                ),
              ),
            const SizedBox(height: 2),
            Text(c.note,
                style: Obsidian.body(color: Obsidian.outline, size: 10.5)),
          ],
        ),
      ),
      const SizedBox(height: Obsidian.gutter),
    ];
  }

  /// A bar centred on 50%: right of centre leans up, left leans down.
  /// Anchoring at zero instead would make every reading look enormous.
  Widget _consensusBar(ConsensusRow r) {
    final p = r.pUp;
    if (p == null) return const SizedBox(height: 6);
    final lean = (p - 0.5).clamp(-0.5, 0.5);
    final up = lean >= 0;
    final colour =
        r.stale ? Obsidian.outline : (up ? Obsidian.green : Obsidian.red);
    return LayoutBuilder(builder: (_, box) {
      final half = box.maxWidth / 2;
      final w = (lean.abs() / 0.5) * half;
      return SizedBox(
        height: 8,
        child: Stack(
          children: [
            Positioned.fill(
              child: Container(
                margin: const EdgeInsets.symmetric(vertical: 3),
                color: Colors.white.withValues(alpha: 0.05),
              ),
            ),
            Positioned(
              left: up ? half : half - w,
              width: w.clamp(1.0, half),
              top: 0,
              bottom: 0,
              child: DecoratedBox(
                decoration: BoxDecoration(
                  color: colour.withValues(alpha: 0.75),
                  borderRadius: BorderRadius.circular(2),
                ),
              ),
            ),
            Positioned(
              left: half - 0.5,
              top: 0,
              bottom: 0,
              child: Container(
                  width: 1, color: Colors.white.withValues(alpha: 0.25)),
            ),
          ],
        ),
      );
    });
  }

  Widget _note(Dashboard d) => Padding(
        padding: const EdgeInsets.symmetric(horizontal: 4),
        child: Text(
            'TP and SL track the live price — they are the levels for an entry '
            'now, using the barrier distance the model fixed at the '
            '${d.interval} close (${_money(d.anchor)}). '
            '${d.calibrationNote}',
            style: Obsidian.body(color: Obsidian.outline, size: 11.5)),
      );

  // ------------------------------------------------------------- fallbacks
  Widget _untrainedPanel() {
    final tfs = _data?.timeframes ?? const <TimeframeInfo>[];
    return ListView(
      padding: const EdgeInsets.fromLTRB(Obsidian.containerPadding, 8,
          Obsidian.containerPadding, Obsidian.navClearance + 24),
      children: [
        if (tfs.isNotEmpty) ...[
          TimeframeBar(
            timeframes: tfs,
            selected: widget.interval,
            onSelect: (tf) => tf.trained
                ? widget.onPickInterval(tf.interval)
                : widget.onNeedsTraining(tf.interval),
          ),
          const SizedBox(height: Obsidian.gutter),
        ],
        GlassPanel(
          padding: const EdgeInsets.all(22),
          child: Column(
            children: [
              const Icon(Icons.model_training_rounded,
                  color: Obsidian.outline, size: 34),
              const SizedBox(height: 14),
              Text('No model for ${widget.interval}',
                  style: Obsidian.headlineMd()),
              const SizedBox(height: 10),
              Text(
                'Each timeframe is its own fitted model — a pattern on a 1m '
                'chart is not the same object as the one on a 1d chart, so a '
                'single fit cannot serve both. There is no probability to '
                'show here until this one is trained.',
                textAlign: TextAlign.center,
                style: Obsidian.body(size: 13.5),
              ),
              const SizedBox(height: 18),
              FilledButton(
                style: FilledButton.styleFrom(
                    backgroundColor: Obsidian.primary,
                    foregroundColor: Obsidian.onPrimary),
                onPressed: () => widget.onNeedsTraining(widget.interval),
                child: const Text('How to train it'),
              ),
            ],
          ),
        ),
      ],
    );
  }

  Widget _placeholder() {
    if (_error != null) {
      return Center(
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
                Text('No link to the service',
                    style: Obsidian.headlineMd()),
                const SizedBox(height: 10),
                Text(_error!, style: Obsidian.body(size: 12.5)),
                const SizedBox(height: 18),
                FilledButton(
                  style: FilledButton.styleFrom(
                      backgroundColor: Obsidian.primary,
                      foregroundColor: Obsidian.onPrimary),
                  onPressed: () {
                    setState(() => _error = null);
                    _load();
                  },
                  child: const Text('Retry'),
                ),
              ],
            ),
          ),
        ),
      );
    }
    return const Center(
        child: CircularProgressIndicator(color: Obsidian.primary));
  }

  static String _money(double? v) {
    if (v == null) return '—';
    final s = v.toStringAsFixed(v.abs() >= 100 ? 2 : 4);
    final parts = s.split('.');
    final whole = parts[0].replaceAllMapped(
        RegExp(r'(\d)(?=(\d{3})+$)'), (m) => '${m[1]},');
    return '\$$whole.${parts[1]}';
  }
}
