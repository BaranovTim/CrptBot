/// The weekly momentum rotation, under the calls list on the Market screen.
///
/// A DIFFERENT KIND OF RECOMMENDATION from a 4h call, and it says so: not
/// one coin now, but a basket held for a week -- among the thirty
/// most-traded perpetuals, long the five ranking best on 15-day momentum and
/// a week of net taker buying together, short the five ranking worst,
/// rebalanced every Monday 00:00 UTC. Measured honestly (research/
/// momentum_pit.py, every perpetual as it stood at the time, dead coins
/// included): Sharpe ~1.0, worst weeks -15% to -18%. The first version's
/// 1.0 was mostly survivorship (0.3-0.4 honestly). The panel carries the
/// numbers and caveats, because a list of coins with no evidence behind it
/// reads as a tip.
library;

import 'package:flutter/material.dart';

import '../api/models.dart';
import '../theme/liquid_obsidian.dart';
import 'glass.dart';

class MomentumPanel extends StatelessWidget {
  const MomentumPanel({super.key, required this.data, this.onOpen});

  /// Null while loading.
  final Momentum? data;

  /// Called with a coin's symbol when its row is tapped.
  final void Function(String symbol)? onOpen;

  @override
  Widget build(BuildContext context) {
    final d = data;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Container(
                width: 7,
                height: 7,
                decoration: BoxDecoration(
                    color: d != null && d.available
                        ? Obsidian.primary
                        : Obsidian.outline,
                    shape: BoxShape.circle)),
            const SizedBox(width: 8),
            Expanded(
              child: Text('MOMENTUM ROTATION · THIS WEEK',
                  style: Obsidian.labelSm(size: 10.5)),
            ),
            GestureDetector(
              onTap: () => _explain(context),
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
          _note('Reading this week\'s rotation…')
        else if (!d.available)
          _note('Not enough coins with enough history to rank this week. '
              'The rotation needs at least eight.')
        else
          GlassPanel(
            padding: const EdgeInsets.fromLTRB(15, 14, 15, 12),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Expanded(
                      child: Text(
                          d.weekStart == null
                              ? 'Held Monday to Monday'
                              : 'Week of ${_date(d.weekStart!)} · rebalances '
                                  '${d.nextRebalance == null ? 'Monday' : _date(d.nextRebalance!)} 00:00 UTC',
                          style: Obsidian.body(
                              color: Obsidian.onSurfaceVariant, size: 11.5)),
                    ),
                    if (d.weekPct != null)
                      Text(
                          '${d.weekPct! >= 0 ? '+' : '−'}${d.weekPct!.abs().toStringAsFixed(2)}% so far',
                          style: Obsidian.dataTable(
                              size: 11.5,
                              color: d.weekPct! >= 0
                                  ? Obsidian.green
                                  : Obsidian.red,
                              w: FontWeight.w600)),
                  ],
                ),
                const SizedBox(height: 12),
                _side('LONG', d.longs, Obsidian.green),
                const SizedBox(height: 10),
                _side('SHORT', d.shorts, Obsidian.red),
                const SizedBox(height: 10),
                Text(
                    'Ranked on ${d.signal.isEmpty ? '${d.lookbackDays}-day momentum' : d.signal}, '
                    'across ${d.universeRule.isEmpty ? 'the ${d.universe} coins' : d.universeRule}. '
                    'Equal size, held for the week. Big weeks both ways: size it small.',
                    style: Obsidian.body(color: Obsidian.outline, size: 11)),
              ],
            ),
          ),
      ],
    );
  }

  Widget _side(String label, List<MomentumPick> picks, Color tone) => Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 50,
            child: Padding(
              padding: const EdgeInsets.only(top: 6),
              child: Text(label,
                  style: Obsidian.labelSm(color: tone, size: 10)),
            ),
          ),
          Expanded(
            child: Wrap(
              spacing: 6,
              runSpacing: 6,
              children: [
                for (final p in picks)
                  GestureDetector(
                    // only coins the app follows have a chart to open
                    onTap: onOpen == null || !p.followed
                        ? null
                        : () => onOpen!(p.symbol),
                    child: Container(
                      padding: const EdgeInsets.symmetric(
                          horizontal: 9, vertical: 5),
                      decoration: BoxDecoration(
                        color: tone.withValues(alpha: p.followed ? 0.10 : 0.05),
                        borderRadius: BorderRadius.circular(8),
                        border: Border.all(
                            color: tone.withValues(alpha: p.followed ? 0.28 : 0.14)),
                      ),
                      child: Text(
                          '${p.short} ${p.ret30d >= 0 ? '+' : '−'}${p.ret30d.abs().toStringAsFixed(0)}%',
                          style: Obsidian.dataTable(
                              size: 11.5,
                              color: Obsidian.onSurface,
                              w: FontWeight.w600)),
                    ),
                  ),
              ],
            ),
          ),
        ],
      );

  Widget _note(String text) => GlassPanel(
        padding: const EdgeInsets.all(15),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Icon(Icons.remove_circle_outline_rounded,
                size: 16, color: Obsidian.outline),
            const SizedBox(width: 10),
            Expanded(
              child: Text(text,
                  style: Obsidian.body(color: Obsidian.outline, size: 11.5)),
            ),
          ],
        ),
      );

  static String _date(DateTime t) {
    const m = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep',
      'Oct', 'Nov', 'Dec'];
    return '${t.day} ${m[t.month - 1]}';
  }

  void _explain(BuildContext context) {
    showModalBottomSheet<void>(
      context: context,
      backgroundColor: Colors.transparent,
      isScrollControlled: true,
      builder: (_) => SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(12),
          child: GlassPanel(
            padding: const EdgeInsets.fromLTRB(20, 18, 20, 20),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('THE MOMENTUM ROTATION',
                    style: Obsidian.labelSm(size: 11)),
                const SizedBox(height: 12),
                for (final line in _why)
                  Padding(
                    padding: const EdgeInsets.only(bottom: 10),
                    child: Text(line, style: Obsidian.body(size: 13)),
                  ),
                if (data?.measured.isNotEmpty ?? false)
                  Text(data!.measured,
                      style: Obsidian.body(color: Obsidian.outline, size: 11.5)),
              ],
            ),
          ),
        ),
      ),
    );
  }

  static const _why = [
    'Two things, ranked together. MOMENTUM: coins that rose most over the '
        'last two weeks tend to keep outperforming the ones that fell most, '
        'for a while. FLOW: coins whose buyers were the more aggressive side '
        'all week -- more than their price move explains -- tend to follow '
        'through. The second is the footprint big buyers leave in public '
        'data; the two barely overlap, which is why together they are '
        'steadier than either.',
    'Every Monday 00:00 UTC the thirty most-traded Binance perpetuals are '
        'ranked on both, and the two ranks averaged. Long the top five, '
        'short the bottom five, equal size, held until the next Monday. The '
        'short side needs a futures account. Coins this app does not follow '
        'are shown without a chart.',
    'Tested on every perpetual as it stood at the time, dead coins included, '
        'Jan 2021 - Aug 2026: Sharpe 0.9 on the years that chose the rule and '
        '1.1 on the two after; momentum alone 0.45 and 0.6, flow alone 0.6 '
        "and 0.6. The worst weeks lost 15-18%. The first version -- 30-day "
        "momentum on today's fifteen coins -- looked like 1.0, but a coin is "
        "on today's list partly because it went up; honestly it was 0.3-0.4.",
    'A basket held for years, not a call: it makes money slowly and loses '
        'in lumps. Size it small.',
  ];
}
