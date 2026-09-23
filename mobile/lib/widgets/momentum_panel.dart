/// The weekly momentum rotation, under the calls list on the Market screen.
///
/// A DIFFERENT KIND OF RECOMMENDATION from a 4h call, and it says so: not
/// one coin now, but a basket held for a week -- long the three coins with
/// the best 30-day return, short the three with the worst, rebalanced every
/// Monday 00:00 UTC. Measured (research/new_strategies.py): rebalanced at
/// every one of the week's 42 four-hour slots from 2021 to 2026 it made
/// money at all of them, Sharpe 0.62-1.31, median 1.00. The panel carries
/// that number and its caveats in a sheet, because a list of six coins with
/// no evidence behind it reads as a tip.
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
          _note('Not enough coins with thirty days of prices to rank this '
              'week. The rotation needs at least eight.')
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
                    'Best and worst ${d.lookbackDays}-day returns of the '
                    '${d.universe} coins, equal size, held for the week. '
                    'On spot, the longs alone.',
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
                    onTap: onOpen == null ? null : () => onOpen!(p.symbol),
                    child: Container(
                      padding: const EdgeInsets.symmetric(
                          horizontal: 9, vertical: 5),
                      decoration: BoxDecoration(
                        color: tone.withValues(alpha: 0.10),
                        borderRadius: BorderRadius.circular(8),
                        border: Border.all(color: tone.withValues(alpha: 0.28)),
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
    'Coins that rose most over the last month tend to keep outperforming '
        'the ones that fell most, for a while. It is the one crypto factor '
        'the research literature rates strong, and it held here.',
    'Every Monday 00:00 UTC the fifteen coins are ranked by their 30-day '
        'return. Long the top three, short the bottom three, equal size, '
        'held until the next Monday. The short side needs a futures account; '
        'on spot, the longs alone still beat holding every coin, by less.',
    'It is a basket, not a single call: some weeks lose. It made money over '
        'years, not every week.',
    'The fifteen coins are today\'s list, which flatters any strategy a '
        'little -- so it was also tested on the ten coins already big in 2021.',
  ];
}
