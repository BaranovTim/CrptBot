/// The panels below the chart, shared by the crypto and stock dashboards.
///
/// WHY SHARED RATHER THAN COPIED
///     The stock page originally had its own "what the model is reading"
///     list — a different shape showing the same numbers. Two renderings of
///     one payload drift: a change to the crypto grid would silently not
///     reach stocks, and the two pages would start disagreeing about what an
///     indicator means. They are one market apart, not one concept apart.
///
/// WHY THE CALLBACKS
///     Live price and "open the history sheet" belong to the page, not to the
///     panel. Passing them in keeps these widgets ignorant of which dashboard
///     they are on, which is the only reason they can be shared at all.
library;

import 'package:flutter/material.dart';

import '../api/models.dart';
import '../api/format.dart';
import '../theme/liquid_obsidian.dart';
import 'glass.dart';

/// The 2x2 grid of indicator cards.
class IndicatorGrid extends StatelessWidget {
  const IndicatorGrid({super.key, required this.indicators, this.onTap});

  final List<Indicator> indicators;
  final void Function(Indicator)? onTap;

  @override
  Widget build(BuildContext context) {
    final items = indicators.take(4).toList();
    if (items.isEmpty) return const SizedBox.shrink();
    return Column(
      children: [
        for (var i = 0; i < items.length; i += 2)
          Padding(
            padding: const EdgeInsets.only(bottom: Obsidian.panelGap),
            // IntrinsicHeight so the pair matches height when one note wraps
            // to two lines and the other does not. CrossAxisAlignment.stretch
            // ALONE cannot do this inside a ListView: stretch asks children
            // to fill the cross axis, the Row's height there is unbounded,
            // and the layout fails with "BoxConstraints forces an infinite
            // height" — which takes the whole screen down, not just this row.
            child: IntrinsicHeight(
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Expanded(child: _card(items[i])),
                  const SizedBox(width: Obsidian.panelGap),
                  Expanded(
                    child: i + 1 < items.length
                        ? _card(items[i + 1])
                        : const SizedBox.shrink(),
                  ),
                ],
              ),
            ),
          ),
      ],
    );
  }

  Widget _card(Indicator ind) {
    final c = Obsidian.tone(ind.tone);
    return GlassPanel(
      padding: const EdgeInsets.all(16),
      onTap: onTap == null ? null : () => onTap!(ind),
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
                child: Icon(iconFor(ind.key), size: 15, color: c),
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
          if (onTap != null) ...[
            const SizedBox(height: 8),
            // a card that opens something has to say so, or nobody presses
            // it — the same mistake swipe-to-delete made on the market screen
            Row(
              children: [
                Text('History',
                    style:
                        Obsidian.labelSm(size: 9, color: Obsidian.outline)),
                const SizedBox(width: 3),
                const Icon(Icons.chevron_right_rounded,
                    size: 13, color: Obsidian.outline),
              ],
            ),
          ],
        ],
      ),
    );
  }

  static IconData iconFor(String key) => switch (key) {
        'rsi' => Icons.speed_rounded,
        'htf' => Icons.show_chart_rounded,
        'horizon' => Icons.calendar_month_rounded,
        'vol' => Icons.ssid_chart_rounded,
        'volume' => Icons.bar_chart_rounded,
        _ => Icons.insights_rounded,
      };
}

/// Current price, take profit, stop loss, and the window probability.
///
/// READ FOR THE SIDE THE CALL NAMES, NOT ALWAYS FOR A LONG.
///     On a SELL the take profit is BELOW the price and the stop is ABOVE,
///     and the probability that matters is the chance of going DOWN. Drawing
///     a short's levels with a long's arithmetic is not a cosmetic error: it
///     puts the stop where the target should be and tells you to protect the
///     wrong side of the trade.
///
///     With no side at all — a FLAT call — these are not a trade's levels.
///     They are the two barriers the model measures its question against, and
///     the panel says so rather than implying an entry nobody proposed.
class LevelsPanel extends StatelessWidget {
  const LevelsPanel({
    super.key,
    required this.price,
    required this.takeProfit,
    required this.stopLoss,
    this.pUp,
    this.windowBars,
    this.interval = '',
    this.side = '',
    this.outcome = '',
    this.fixed = false,
    this.entryLimit,
    this.entryUntil,
  });

  final double? price, takeProfit, stopLoss, pUp;

  /// The resting order the call enters with, and until when it is good.
  /// When set, the target and stop are measured from here, not the price.
  final double? entryLimit;
  final DateTime? entryUntil;
  final int? windowBars;
  final String interval;

  /// LONG, SHORT, or "" when the call is FLAT and no entry was proposed.
  final String side;

  /// "target" when the price has already reached the take profit, "stop"
  /// when it has already hit the stop, "" while the call is open. A level
  /// that is gone is labelled as gone: a take profit printed BELOW the
  /// current price, with no word about it, was the question this answers.
  final String outcome;

  /// Levels that are prices on the chart (structure) rather than distances
  /// re-anchored to the live price. Only fixed levels can be left behind.
  final bool fixed;

  bool get _short => side == 'SHORT';
  bool get _flat => side != 'LONG' && side != 'SHORT';

  /// A FLAT band the current bar has already left: both barriers on the
  /// same side of the price. The model's question was asked from the last
  /// close; the market has since answered it, one way or the other.
  bool get _left {
    final p = price, tp = takeProfit, sl = stopLoss;
    if (!fixed || p == null || tp == null || sl == null) return false;
    final hi = tp > sl ? tp : sl, lo = tp > sl ? sl : tp;
    return p > hi || p < lo;
  }

  /// The chance the trade the panel describes WINS.
  ///
  /// `pUp` is always the probability of the upper barrier first, because that
  /// is the question every model here is trained on. For a short that is the
  /// losing outcome, so the panel reports its complement rather than printing
  /// a number that means the opposite of what the label says.
  double? get _pWin =>
      pUp == null ? null : (_short ? 1 - pUp! : pUp!);

  @override
  Widget build(BuildContext context) => GlassPanel(
        padding: EdgeInsets.zero,
        child: Column(
          children: [
            _row('Current Price', money(price), Obsidian.onSurface),
            _divider(),
            if (entryLimit != null && !_flat) ...[
              _row(
                _short ? 'Entry (sell limit)' : 'Entry (buy limit)',
                money(entryLimit),
                Obsidian.primary,
                sub: 'Place a limit order here instead of '
                    '${_short ? 'selling' : 'buying'} at the market'
                    '${entryUntil == null ? '' : ', good until ${_hhmm(entryUntil!)}'}. '
                    'If it never fills, there is no trade. The target and stop '
                    'below are measured from this price.',
              ),
              _divider(),
            ],
            _row(
              _flat
                  ? 'Upper barrier'
                  : outcome == 'target'
                      ? 'Take Profit (TP1) — reached'
                      : 'Take Profit (TP1)',
              money(takeProfit),
              _flat
                  ? Obsidian.onSurface
                  : outcome == 'target'
                      ? Obsidian.outline
                      : Obsidian.green,
              sub: _flat
                  ? (_left
                      ? 'No entry is proposed, and the price has already '
                          'left this band during the current bar. New '
                          'barriers come at the next close.'
                      : 'No entry is proposed right now, so these two are the '
                          "model's barriers rather than a trade's levels.")
                  : outcome == 'target'
                      ? 'The price went through it during this bar. The '
                          'trade the model scored is over; this is not a '
                          'level to enter against.'
                      : _short
                          ? 'Below the price, because the call is a SELL — the '
                              'short is closed in profit down here.'
                          : null,
            ),
            _divider(),
            _row(
              _flat
                  ? 'Lower barrier'
                  : outcome == 'stop'
                      ? 'Stop Loss (SL) — hit'
                      : 'Stop Loss (SL)',
              money(stopLoss),
              _flat
                  ? Obsidian.onSurface
                  : outcome == 'stop'
                      ? Obsidian.outline
                      : Obsidian.red,
              sub: outcome == 'stop'
                  ? 'The price went through it during this bar. The call '
                      'failed; the next one comes at the close.'
                  : _short && !_flat
                      ? 'Above the price. A short loses as price rises, so the '
                          'stop sits over it, not under.'
                      : null,
            ),
            if (_pWin != null) ...[
              _divider(),
              _row(
                _short
                    ? 'Chance down (${windowBars ?? '?'} × $interval)'
                    : 'Chance up (${windowBars ?? '?'} × $interval)',
                '${(_pWin! * 100).toStringAsFixed(1)}%',
                Obsidian.primary,
                sub: "The model's probability that price reaches "
                    '${_flat ? 'the upper barrier' : 'Take Profit'} before '
                    '${_flat ? 'the lower one' : 'Stop Loss'} within the next '
                    '${windowBars ?? '?'} $interval bars. 50% is a coin flip; '
                    'it is not a forecast of how far price moves.',
              ),
            ],
          ],
        ),
      );

  static String _hhmm(DateTime t) {
    const d = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
    final u = t.toUtc();
    return '${d[u.weekday - 1]} ${u.hour.toString().padLeft(2, '0')}:'
        '${u.minute.toString().padLeft(2, '0')} UTC';
  }

  static Widget _divider() =>
      Divider(height: 1, color: Colors.white.withValues(alpha: 0.06));

  static Widget _row(String label, String value, Color c, {String? sub}) =>
      Padding(
        padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(label, style: Obsidian.body(size: 13.5)),
                ),
                Text(value,
                    style: Obsidian.dataTable(
                        size: 16, color: c, w: FontWeight.w600)),
              ],
            ),
            if (sub != null) ...[
              const SizedBox(height: 6),
              Text(sub,
                  style: Obsidian.body(color: Obsidian.outline, size: 11)),
            ],
          ],
        ),
      );

  /// Nulls render as a dash, never as zero. A stop loss of "$0.00" reads as
  /// a level, and it is the most dangerous number this panel could invent.
  /// Nulls render as a dash, never as zero. A stop loss of "$0.00" reads as
  /// a level, and it is the most dangerous number this panel could invent.
  ///
  /// Digits come from `priceText`, which keeps the ones that matter — a
  /// fixed four decimals wrote 1000PEPE's 0.003624 as 0.0036.
  static String money(double? v) => priceText(v);
}
