/// The live record of the 4h calls, under the calls list on the Market screen.
///
/// WHY: every number the app quoted about its calls was from the
/// walk-forward -- honest, out of time, and still a backtest. This is the
/// other half: the orders the server actually placed since the model went
/// live, which filled, and how each trade ended, net of the 0.10% fee
/// (api/ledger.py). It shows the level the person follows, says what the
/// walk-forward expects next to it, and says when there are too few trades
/// to read anything into -- twenty trades at 60% can show 40% or 80%.
library;

import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../api/models.dart';
import '../theme/liquid_obsidian.dart';
import 'glass.dart';

class RecordPanel extends StatelessWidget {
  const RecordPanel({super.key, required this.data, this.level = 'strong'});

  /// Null while loading.
  final LiveRecord? data;

  /// The sensitivity the person follows: strong, medium or small.
  final String level;

  @override
  Widget build(BuildContext context) {
    final d = data;
    final lv = d?.levels[level];
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Container(
                width: 7,
                height: 7,
                decoration: BoxDecoration(
                    color: (lv?.closed ?? 0) > 0 ? Obsidian.primary : Obsidian.outline,
                    shape: BoxShape.circle)),
            const SizedBox(width: 8),
            Expanded(
              child: Text('LIVE RECORD · 4H CALLS',
                  style: Obsidian.labelSm(size: 10.5)),
            ),
            GestureDetector(
              onTap: d == null ? null : () => _explain(context, d),
              child: const Padding(
                padding: EdgeInsets.all(4),
                child: Icon(Icons.info_outline_rounded,
                    size: 16, color: Obsidian.outline),
              ),
            ),
          ],
        ),
        const SizedBox(height: 10),
        if (d == null)
          _note('Reading the record…')
        else
          GestureDetector(
            onTap: () => _explain(context, d),
            child: GlassPanel(
              padding: const EdgeInsets.fromLTRB(15, 14, 15, 12),
              child: _body(d, lv),
            ),
          ),
      ],
    );
  }

  Widget _body(LiveRecord d, RecordLevel? lv) {
    final exp = d.expected[level];
    final since = d.since == null ? 'the model went live' : _date(d.since!);
    final head = Text(
        'Since $since · your setting: $level',
        style: Obsidian.body(color: Obsidian.onSurfaceVariant, size: 11.5));
    if (lv == null || lv.closed == 0) {
      final open = lv == null
          ? ''
          : ' ${lv.orders} order${lv.orders == 1 ? '' : 's'} placed, '
              '${lv.inTrade} in a trade, ${lv.openOrders} waiting to fill.';
      return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        head,
        const SizedBox(height: 10),
        Text('No trade has closed yet.$open',
            style: Obsidian.body(size: 12.5)),
        if (exp != null) ...[
          const SizedBox(height: 8),
          Text(_expectedLine(exp),
              style: Obsidian.body(color: Obsidian.outline, size: 11)),
        ],
      ]);
    }
    final win = lv.winRate ?? 0;
    final avg = lv.avgNetPct ?? 0;
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      head,
      const SizedBox(height: 12),
      Row(children: [
        _stat('WON', '${(100 * win).toStringAsFixed(0)}%',
            '${lv.wins} of ${lv.closed}', win >= 0.5 ? Obsidian.green : Obsidian.red),
        _stat('PER TRADE', _signed(avg), 'after fees',
            avg >= 0 ? Obsidian.green : Obsidian.red),
        _stat('TOTAL', _signed(lv.sumNetPct), 'equal size',
            lv.sumNetPct >= 0 ? Obsidian.green : Obsidian.red),
      ]),
      const SizedBox(height: 10),
      Text(
          '${lv.targets} target${lv.targets == 1 ? '' : 's'}'
          '${lv.backToEntry > 0 ? ' · ${lv.backToEntry} back to entry after ⅓ off' : ''}'
          ' · ${lv.stops} stop${lv.stops == 1 ? '' : 's'}'
          ' · ${lv.timeouts} on time · ${lv.inTrade} open · ${lv.expired} never filled',
          style: Obsidian.body(color: Obsidian.onSurfaceVariant, size: 11.5)),
      const SizedBox(height: 8),
      if (lv.closed < 30)
        Text(_smallSample(win, lv.closed),
            style: Obsidian.body(color: Obsidian.outline, size: 11)),
      if (exp != null)
        Text(_expectedLine(exp),
            style: Obsidian.body(color: Obsidian.outline, size: 11)),
      for (final t in (d.recent[level] ?? const <RecordTrade>[]).take(4))
        Padding(padding: const EdgeInsets.only(top: 8), child: _trade(t)),
    ]);
  }

  Widget _stat(String label, String value, String sub, Color tone) => Expanded(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(label, style: Obsidian.labelSm(size: 9.5)),
          const SizedBox(height: 3),
          Text(value,
              style: Obsidian.dataTable(size: 17, color: tone, w: FontWeight.w600)),
          Text(sub, style: Obsidian.body(color: Obsidian.outline, size: 10.5)),
        ]),
      );

  Widget _trade(RecordTrade t) {
    final tone = (t.netPct ?? 0) >= 0 ? Obsidian.green : Obsidian.red;
    final how = t.taken
        ? {'target': '⅓ off, then target', 'stop': '⅓ off, back to entry',
            'timeout': '⅓ off, on time'}[t.state] ?? t.state
        : {'target': 'target', 'stop': 'stopped', 'timeout': 'on time'}[t.state] ?? t.state;
    return Row(children: [
      SizedBox(
          width: 64,
          child: Text(t.short,
              style: Obsidian.dataTable(size: 11.5, color: Obsidian.onSurface, w: FontWeight.w600))),
      SizedBox(
          width: 52,
          child: Text(t.side.toUpperCase(),
              style: Obsidian.labelSm(
                  size: 9.5, color: t.side == 'long' ? Obsidian.green : Obsidian.red))),
      Expanded(
          child: Text(how, style: Obsidian.body(color: Obsidian.onSurfaceVariant, size: 11.5))),
      Text(t.netPct == null ? '—' : _signed(t.netPct!),
          style: Obsidian.dataTable(size: 11.5, color: tone, w: FontWeight.w600)),
    ]);
  }

  Widget _note(String text) => GlassPanel(
        padding: const EdgeInsets.all(15),
        child: Text(text, style: Obsidian.body(color: Obsidian.outline, size: 11.5)),
      );

  static String _signed(double v) =>
      '${v >= 0 ? '+' : '−'}${v.abs().toStringAsFixed(2)}%';

  static String _expectedLine(RecordExpected e) {
    final w = e.winRate, hw = e.holdWinRate, a = e.avgNetPct, ha = e.holdAvgNetPct;
    if (w == null || a == null) return '';
    final lo = math.min(w, hw ?? w), hi = math.max(w, hw ?? w);
    final alo = math.min(a, ha ?? a), ahi = math.max(a, ha ?? a);
    return 'Expected from the walk-forward: ${(100 * lo).toStringAsFixed(0)}–'
        '${(100 * hi).toStringAsFixed(0)}% won, ${_signed(alo)} to ${_signed(ahi)} a trade.';
  }

  /// A 95% band for a win rate measured on `n` trades.
  static String _smallSample(double p, int n) {
    final half = 1.96 * math.sqrt(math.max(p * (1 - p), 0.05) / math.max(n, 1));
    return 'Only $n trade${n == 1 ? '' : 's'}: a real ${(100 * p).toStringAsFixed(0)}% '
        'could read anywhere in ±${(100 * half).toStringAsFixed(0)} points. Too few to judge yet.';
  }

  static String _date(DateTime t) {
    const m = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep',
      'Oct', 'Nov', 'Dec'];
    return '${t.day} ${m[t.month - 1]}';
  }

  void _explain(BuildContext context, LiveRecord d) {
    showModalBottomSheet<void>(
      context: context,
      backgroundColor: Colors.transparent,
      isScrollControlled: true,
      builder: (_) => SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(12),
          child: GlassPanel(
            padding: const EdgeInsets.fromLTRB(20, 18, 20, 20),
            child: SingleChildScrollView(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('THE LIVE RECORD', style: Obsidian.labelSm(size: 11)),
                  const SizedBox(height: 12),
                  for (final line in _why)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 10),
                      child: Text(line, style: Obsidian.body(size: 13)),
                    ),
                  const SizedBox(height: 4),
                  Text('BY SETTING', style: Obsidian.labelSm(size: 10)),
                  const SizedBox(height: 6),
                  for (final k in const ['strong', 'medium', 'small'])
                    Padding(
                      padding: const EdgeInsets.only(bottom: 6),
                      child: Text(_levelLine(k, d), style: Obsidian.body(size: 12)),
                    ),
                  if (d.expectedNote.isNotEmpty) ...[
                    const SizedBox(height: 6),
                    Text(d.expectedNote,
                        style: Obsidian.body(color: Obsidian.outline, size: 11.5)),
                  ],
                  if (d.benchmarks.isNotEmpty) ...[
                    const SizedBox(height: 14),
                    Text('WHAT OTHERS MEASURED', style: Obsidian.labelSm(size: 10)),
                    const SizedBox(height: 6),
                    for (final b in d.benchmarks)
                      Padding(
                        padding: const EdgeInsets.only(bottom: 8),
                        child: Text('${b.what}: ${b.figure}  (${b.source})',
                            style: Obsidian.body(color: Obsidian.onSurfaceVariant, size: 11.5)),
                      ),
                  ],
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }

  static String _levelLine(String k, LiveRecord d) {
    final lv = d.levels[k];
    final e = d.expected[k];
    final live = lv == null || lv.closed == 0
        ? 'no closed trades yet'
        : '${lv.closed} trades, ${(100 * (lv.winRate ?? 0)).toStringAsFixed(0)}% won, '
            '${_signed(lv.avgNetPct ?? 0)} a trade';
    final exp = e == null || e.winRate == null
        ? ''
        : ' · expected ${(100 * e.winRate!).toStringAsFixed(0)}%, ${_signed(e.avgNetPct ?? 0)}'
            '${e.tradesPerMonth == null ? '' : ', ~${e.tradesPerMonth} trades a month'}';
    return '${k[0].toUpperCase()}${k.substring(1)}: $live$exp';
  }

  static const _why = [
    'Every 4h call the server made since the model went live, traded the way '
        'the app says: a limit order 0.5 ATR better than the close, good for a '
        'day, one at a time per coin. Nothing here is a backtest.',
    'A trade is an order that filled. It is counted when it ends -- at the '
        'target, at the stop, or when its window runs out -- from the fill '
        'price, less 0.10% for fees. Orders that never filled are listed, not '
        'counted as wins or losses.',
    'Halfway to the target a third comes off and the stop on the rest moves '
        'to the entry, so a trade that gets that far is counted with both '
        'parts: the third at the halfway price, the rest wherever it ended. '
        'One that then comes back to the entry is a small win, not a stop.',
    'Win rate alone says little: this system wins more often than it loses, '
        'but a win and a loss are not the same size. Per-trade and total '
        'returns are the numbers that pay.',
  ];
}
