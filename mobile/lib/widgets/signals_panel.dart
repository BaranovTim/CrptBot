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

import '../api/format.dart';
import '../api/models.dart';
import '../theme/liquid_obsidian.dart';
import 'glass.dart';

class SignalsPanel extends StatelessWidget {
  const SignalsPanel({
    super.key,
    required this.data,
    required this.onOpen,
    this.equities = false,
    this.held = const {},
  });

  final LiveSignals? data;

  /// Called with the pair and the timeframe the call belongs to.
  final void Function(LiveSignal) onOpen;

  /// Show the equity rows instead of the crypto ones.
  final bool equities;

  /// What you currently have an OPEN trade logged against, as
  /// `SYMBOL:interval` keys -- or a bare `SYMBOL` for an entry logged before
  /// entries carried a timeframe, which then marks every row of that coin.
  ///
  /// PER TIMEFRAME, not per coin. An entry taken on the 4h call is a 4h
  /// trade; the 1h row of the same coin is a call you have NOT acted on, and
  /// tagging it IN TRADE told you the opposite. A call on a pair you are
  /// already in is about whether to stay; one you are not in is about
  /// whether to enter. The list should not make you remember which is which.
  final Set<String> held;

  static String heldKey(String symbol, String? interval) =>
      interval == null || interval.isEmpty ? symbol : '$symbol:$interval';

  bool _inTrade(LiveSignal s) =>
      held.contains(heldKey(s.symbol, s.interval)) || held.contains(s.symbol);

  /// "TP +1.18% · SL −7.05%" from the call's own levels, signed from the
  /// price (a short's target is below it), or null when a level is missing.
  ///
  /// A call that enters with a RESTING ORDER leads with its price, and the
  /// distances are from there: that is where the trade the levels describe
  /// begins, not the price on the screen.
  static String? _payoff(LiveSignal s) {
    final limit = s.entryLimit;
    final px = limit ?? s.price, tp = s.takeProfit, sl = s.stopLoss;
    if (px == null || px <= 0 || tp == null || sl == null) return null;
    String pct(double v) {
      final d = (v / px - 1) * 100;
      return '${d >= 0 ? '+' : '−'}${d.abs().toStringAsFixed(2)}%';
    }

    final head = limit == null ? '' : 'Limit ${priceText(limit)} · ';
    final o = s.order;
    final scale = o == null || o.scalePrice == null || o.taken
        ? ''
        : ' · ${o.partShort} at ${pct(o.scalePrice!)}';
    return '${head}TP ${pct(tp)}$scale · SL ${pct(sl)}';
  }

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
                    // THE PAYOFF, NOT AN EV. A 4h call's EV was built on a
                    // probability that does not know how near its target
                    // is, and printed a red "EV -1.3%" beside the calls that
                    // paid; the server no longer sends one for them. The
                    // distances to target and stop are facts.
                    if (_payoff(s) != null) ...[
                      const SizedBox(width: 7),
                      Flexible(
                        child: Text(_payoff(s)!,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: Obsidian.dataTable(
                                size: 10.5, color: Obsidian.outline)),
                      ),
                    ] else if (s.ev != null) ...[
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
          if (_inTrade(s)) ...[
            const SizedBox(width: 6),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 3),
              decoration: BoxDecoration(
                color: Obsidian.primary.withValues(alpha: 0.13),
                borderRadius: BorderRadius.circular(5),
                border:
                    Border.all(color: Obsidian.primary.withValues(alpha: 0.35)),
              ),
              child: Row(mainAxisSize: MainAxisSize.min, children: [
                const Icon(Icons.bookmark_added_rounded,
                    size: 11, color: Obsidian.primary),
                const SizedBox(width: 4),
                Text('IN TRADE',
                    style: Obsidian.labelSm(color: Obsidian.primary, size: 8.5)),
              ]),
            ),
          ],
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
