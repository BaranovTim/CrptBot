/// The long view: 1 month, 6 months, 1 year.
///
/// WHY THIS LOOKS NOTHING LIKE THE RECOMMENDATION CARD
///     It must not. The recommendation is a fitted model's output; this is
///     arithmetic over history at horizons where no model can be fitted. If
///     the two shared a visual language — a big coloured word, a probability
///     to three decimals — the reader would reasonably assume they carry the
///     same weight, and one of them is built on six observations.
///
///     So: no verdict, no colour-coded call. A count, its confidence
///     interval, and the sample size in the same size type as the number it
///     qualifies.
///
/// WHY DRAWDOWN IS GIVEN EQUAL BILLING
///     "Up after a year, 67% of the time" is the wrong number for someone
///     holding through it. The worst drawdown across those windows was -67%,
///     and a position sized on the first number would not have survived to
///     collect it.
library;

import 'package:flutter/material.dart';

import '../api/models.dart';
import '../theme/liquid_obsidian.dart';
import 'glass.dart';

class HorizonSheet extends StatelessWidget {
  const HorizonSheet({super.key, required this.report});

  final HorizonReport report;

  @override
  Widget build(BuildContext context) => SafeArea(
        top: false,
        child: Padding(
          padding: const EdgeInsets.all(Obsidian.containerPadding),
          child: GlassPanel(
            active: true,
            padding: const EdgeInsets.fromLTRB(20, 20, 20, 10),
            child: ConstrainedBox(
              constraints: BoxConstraints(
                  maxHeight: MediaQuery.of(context).size.height * 0.82),
              child: ListView(
                shrinkWrap: true,
                children: [
                  Text('THE LONG VIEW · ${report.symbol}',
                      style: Obsidian.labelSm(size: 11)),
                  const SizedBox(height: 4),
                  Text('${report.historyYears} years of history',
                      style: Obsidian.body(
                          color: Obsidian.outline, size: 11.5)),
                  const SizedBox(height: 14),
                  // The disclaimer leads. It is not fine print here: it is
                  // the difference between what this panel is and what the
                  // recommendation card is.
                  Container(
                    padding: const EdgeInsets.all(12),
                    decoration: BoxDecoration(
                      color: Obsidian.amber.withValues(alpha: 0.07),
                      borderRadius: BorderRadius.circular(Obsidian.rMd),
                      border: Border.all(
                          color: Obsidian.amber.withValues(alpha: 0.22)),
                    ),
                    child: Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        const Icon(Icons.history_rounded,
                            size: 15, color: Obsidian.amber),
                        const SizedBox(width: 9),
                        Expanded(
                          child: Text(report.disclaimer,
                              style: Obsidian.body(
                                  color: Obsidian.outline, size: 11)),
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(height: 16),
                  for (final r in report.rows) ...[
                    _row(r),
                    const SizedBox(height: 14),
                  ],
                  Center(
                    child: TextButton(
                      onPressed: () => Navigator.of(context).pop(),
                      child: Text('Close',
                          style: Obsidian.body(color: Obsidian.primary)),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
      );

  Widget _row(HorizonRow r) {
    if (r.n == 0) {
      return Text('${r.horizon}: ${r.note}',
          style: Obsidian.body(color: Obsidian.outline, size: 11.5));
    }
    // Thin evidence is drawn thin. A rate from six windows must not look like
    // a rate from eighty-one.
    final strong = r.meaningful && !r.spansEven;
    final c = strong ? Obsidian.onSurface : Obsidian.outline;

    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: Obsidian.surfaceLow,
        borderRadius: BorderRadius.circular(Obsidian.rMd),
        border: Border(
          left: BorderSide(
              color: strong ? Obsidian.primary : Obsidian.outlineVariant,
              width: 3),
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              Text(r.horizon.toUpperCase(),
                  style: Obsidian.labelSm(size: 10.5)),
              const Spacer(),
              // n in the SAME size as the rate. It is not a footnote.
              Text('n = ${r.n}',
                  style: Obsidian.dataTable(
                      size: 13,
                      color: r.meaningful
                          ? Obsidian.outline
                          : Obsidian.amber)),
            ],
          ),
          const SizedBox(height: 10),
          Row(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              Text('${r.upRate!.toStringAsFixed(0)}%',
                  style: Obsidian.displayLg(color: c).copyWith(fontSize: 34)),
              const SizedBox(width: 8),
              Padding(
                padding: const EdgeInsets.only(bottom: 6),
                child: Text('of windows ended higher',
                    style: Obsidian.body(
                        color: Obsidian.outline, size: 11.5)),
              ),
            ],
          ),
          if (r.ciLow != null)
            Text(
                '95% confidence: ${r.ciLow!.toStringAsFixed(0)}–'
                '${r.ciHigh!.toStringAsFixed(0)}%'
                '${r.spansEven ? '  — includes 50%, so this cannot be read '
                    'as a tendency' : ''}',
                style: Obsidian.body(
                    color: r.spansEven ? Obsidian.amber : Obsidian.outline,
                    size: 11)),
          const SizedBox(height: 12),
          Wrap(
            spacing: 18,
            runSpacing: 10,
            children: [
              _stat('MEDIAN', r.medianPct),
              _stat('WORST', r.worstPct),
              _stat('BEST', r.bestPct),
            ],
          ),
          const SizedBox(height: 12),
          Container(
            padding: const EdgeInsets.all(10),
            decoration: BoxDecoration(
              color: Obsidian.surfaceLowest.withValues(alpha: 0.6),
              borderRadius: BorderRadius.circular(8),
            ),
            child: Row(
              children: [
                const Icon(Icons.trending_down_rounded,
                    size: 14, color: Obsidian.redSoft),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                      'Went as low as '
                      '${r.medianDrawdownPct!.toStringAsFixed(1)}% along the '
                      'way, typically — worst case '
                      '${r.worstDrawdownPct!.toStringAsFixed(1)}%.',
                      style: Obsidian.body(
                          color: Obsidian.outline, size: 11)),
                ),
              ],
            ),
          ),
          if (!r.meaningful) ...[
            const SizedBox(height: 10),
            Text(r.note,
                style: Obsidian.body(color: Obsidian.amber, size: 10.5)),
          ],
        ],
      ),
    );
  }

  Widget _stat(String label, double? v) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          Text(label, style: Obsidian.labelSm(size: 8.5)),
          const SizedBox(height: 2),
          Text(v == null
                  ? '—'
                  : '${v >= 0 ? '+' : ''}${v.toStringAsFixed(1)}%',
              style: Obsidian.dataTable(
                  size: 13.5,
                  color: v == null
                      ? null
                      : (v >= 0 ? Obsidian.green : Obsidian.red))),
        ],
      );
}
