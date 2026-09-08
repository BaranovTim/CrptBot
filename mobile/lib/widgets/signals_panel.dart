/// What is calling right now, across every pair the server watches.
///
/// A CALL IS A PAIR *AND* A TIMEFRAME
///     SUI can be SELL on 1h and FLAT on 1d in the same second, and both are
///     right — they answer different questions over different horizons. So
///     every row names its interval and opening one lands on THAT interval.
///     Dropping you on whichever timeframe you last looked at would show a
///     different call from the one you tapped, which is the worst outcome a
///     list like this can produce.
library;

import 'package:flutter/material.dart';

import '../api/models.dart';
import '../theme/liquid_obsidian.dart';
import 'glass.dart';

class SignalsPanel extends StatelessWidget {
  const SignalsPanel({
    super.key,
    required this.data,
    required this.onOpen,
    this.equities = false,
  });

  final LiveSignals? data;

  /// Called with the pair and the timeframe the call belongs to.
  final void Function(LiveSignal) onOpen;

  /// Show the equity rows instead of the crypto ones.
  final bool equities;

  @override
  Widget build(BuildContext context) {
    final d = data;
    final rows = (d?.signals ?? const <LiveSignal>[])
        .where((s) => s.isEquity == equities)
        .toList();

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Container(
                width: 7,
                height: 7,
                decoration: BoxDecoration(
                    color: rows.isEmpty ? Obsidian.outline : Obsidian.primary,
                    shape: BoxShape.circle)),
            const SizedBox(width: 8),
            Expanded(
              child: Text('CALLS RIGHT NOW', style: Obsidian.labelSm(size: 10.5)),
            ),
            if (rows.isNotEmpty)
              Container(
                padding:
                    const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                decoration: BoxDecoration(
                  color: Obsidian.primary.withValues(alpha: 0.13),
                  borderRadius: BorderRadius.circular(20),
                ),
                child: Text('${rows.length}',
                    style: Obsidian.dataTable(
                        size: 10.5,
                        color: Obsidian.primary,
                        w: FontWeight.w700)),
              ),
          ],
        ),
        const SizedBox(height: 10),
        if (d == null)
          _note('Reading the latest calls…')
        else if (rows.isEmpty)
          // NOT "no signals". The distinction matters: the models are
          // answering, and their answer is FLAT.
          _note(equities
              ? 'Every stock the server watches is FLAT right now. That is an '
                  'answer, not a gap — a call has to beat its own costs before '
                  'it is worth taking.'
              : 'Every pair the server watches is FLAT right now — '
                  '${d.watched} of them, across ${d.intervals.join(", ")}. '
                  'That is an answer, not a gap.')
        else
          for (final s in rows) ...[
            _row(s),
            const SizedBox(height: 8),
          ],
      ],
    );
  }

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

  Widget _row(LiveSignal s) {
    final tone = s.isSell ? Obsidian.red : Obsidian.green;
    final grade = _gradeTone(s.strength);
    return GlassPanel(
      padding: const EdgeInsets.symmetric(horizontal: 13, vertical: 12),
      onTap: () => onOpen(s),
      child: Row(
        children: [
          Container(
            width: 38,
            height: 38,
            decoration: BoxDecoration(
              color: tone.withValues(alpha: 0.11),
              borderRadius: BorderRadius.circular(10),
              border: Border.all(color: tone.withValues(alpha: 0.32)),
            ),
            alignment: Alignment.center,
            child: Icon(
                s.isSell
                    ? Icons.south_east_rounded
                    : Icons.north_east_rounded,
                size: 17,
                color: tone),
          ),
          const SizedBox(width: 11),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Flexible(
                      child: Text(s.isEquity ? s.short : '${s.short}/USDT',
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: Obsidian.bodyLg().copyWith(
                              fontSize: 14, fontWeight: FontWeight.w600)),
                    ),
                    const SizedBox(width: 7),
                    // THE TIMEFRAME, beside the name rather than buried:
                    // it is half of what the call actually says.
                    Container(
                      padding: const EdgeInsets.symmetric(
                          horizontal: 6, vertical: 2),
                      decoration: BoxDecoration(
                        color: Colors.white.withValues(alpha: 0.07),
                        borderRadius: BorderRadius.circular(4),
                      ),
                      child: Text(s.interval,
                          style: Obsidian.dataTable(
                              size: 10, color: Obsidian.onSurfaceVariant)),
                    ),
                  ],
                ),
                const SizedBox(height: 5),
                Row(
                  children: [
                    Text(s.action,
                        style: Obsidian.labelSm(color: tone, size: 10.5)),
                    const SizedBox(width: 7),
                    Container(
                      padding: const EdgeInsets.symmetric(
                          horizontal: 6, vertical: 2),
                      decoration: BoxDecoration(
                        color: grade.withValues(alpha: 0.13),
                        borderRadius: BorderRadius.circular(4),
                        border:
                            Border.all(color: grade.withValues(alpha: 0.3)),
                      ),
                      child: Text(s.strengthLabel,
                          style: Obsidian.labelSm(color: grade, size: 9)),
                    ),
                    if (s.ev != null) ...[
                      const SizedBox(width: 7),
                      Flexible(
                        child: Text('EV ${s.ev! >= 0 ? '+' : ''}'
                            '${s.ev!.toStringAsFixed(2)}%',
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: Obsidian.dataTable(
                                size: 10.5, color: Obsidian.outline)),
                      ),
                    ],
                  ],
                ),
              ],
            ),
          ),
          const SizedBox(width: 6),
          const Icon(Icons.chevron_right_rounded,
              size: 18, color: Obsidian.outline),
        ],
      ),
    );
  }

  /// STRONG green, MEDIUM amber, LOW grey.
  ///
  /// Amber is this app's "we cannot tell" colour, which is close enough to
  /// what a medium signal is: real margin over costs, not much of it.
  static Color _gradeTone(String strength) => switch (strength) {
        'strong' => Obsidian.green,
        'medium' => Obsidian.amber,
        'small' => Obsidian.outline,
        _ => Obsidian.outline,
      };
}
