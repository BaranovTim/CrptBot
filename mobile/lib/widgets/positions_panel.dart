/// What you are holding, marked against the live price.
///
/// WHY PROFIT IS NULL AND NOT ZERO WHEN THE PRICE IS UNKNOWN
///     A position card showing "+$0.00" reads as "flat", which is a claim.
///     "—" reads as "not known", which is the truth when the websocket has
///     not delivered a tick yet. Same argument `LevelsPanel.money` makes
///     about a stop loss of "$0.00", and the same reason it is made again
///     here rather than assumed.
///
/// THE DIRECTION IS APPLIED ONCE, IN `TradeEntry.pnlPct`
///     A short that falls 2% is +2%. Painting a winning short red is the
///     single most confusing thing this panel could do, so the sign lives in
///     the model where both this panel and the profile list read it, rather
///     than being re-derived in each.
library;

import 'package:flutter/material.dart';

import '../api/format.dart';
import '../api/trades.dart';
import '../theme/liquid_obsidian.dart';
import 'glass.dart';

/// `dp` is accepted and ignored: callers used it to ask for two decimals on a
/// profit figure, and the digit count is now decided by the magnitude of the
/// number itself. See `priceText` — a fixed four decimals wrote 1000PEPE's
/// 0.003624 as 0.0036 and lost the digits that coin moves in.
String money(double? v, {int? dp}) => priceText(v);

String signedPct(double? v) =>
    v == null ? '—' : '${v >= 0 ? '+' : ''}${v.toStringAsFixed(2)}%';

/// One card per open trade in this pair, above the news on the dashboard.
class PositionsPanel extends StatelessWidget {
  const PositionsPanel({
    super.key,
    required this.entries,
    required this.short,
    this.livePrice,
    this.onClose,
    this.onEdit,
  });

  final List<TradeEntry> entries;
  final String short;
  final double? livePrice;
  final void Function(TradeEntry)? onClose;
  final void Function(TradeEntry)? onEdit;

  @override
  Widget build(BuildContext context) {
    if (entries.isEmpty) return const SizedBox.shrink();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Text('YOUR POSITIONS', style: Obsidian.labelSm(size: 10.5)),
            const SizedBox(width: 8),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
              decoration: BoxDecoration(
                color: Obsidian.primary.withValues(alpha: 0.15),
                borderRadius: BorderRadius.circular(10),
              ),
              child: Text('${entries.length}',
                  style: Obsidian.dataTable(
                      size: 10.5, color: Obsidian.primary, w: FontWeight.w700)),
            ),
          ],
        ),
        const SizedBox(height: 10),
        for (final t in entries) ...[
          PositionCard(
              entry: t,
              livePrice: livePrice,
              short: short,
              onClose: onClose == null ? null : () => onClose!(t),
              onEdit: onEdit == null ? null : () => onEdit!(t)),
          const SizedBox(height: Obsidian.panelGap),
        ],
      ],
    );
  }
}

class PositionCard extends StatelessWidget {
  const PositionCard({
    super.key,
    required this.entry,
    required this.short,
    this.livePrice,
    this.onClose,
    this.onEdit,
    this.showSymbol = false,
  });

  final TradeEntry entry;
  final String short;
  final double? livePrice;
  final VoidCallback? onClose;
  final VoidCallback? onEdit;
  final bool showSymbol;

  @override
  Widget build(BuildContext context) {
    final pct = entry.pnlPct(livePrice);
    final abs = entry.pnl(livePrice);
    final up = (pct ?? 0) >= 0;
    final tone = pct == null
        ? Obsidian.outline
        : (up ? Obsidian.green : Obsidian.red);
    final sideTone = entry.isShort ? Obsidian.red : Obsidian.green;
    final mark = entry.markPrice(livePrice);

    return GlassPanel(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Container(
                padding:
                    const EdgeInsets.symmetric(horizontal: 9, vertical: 4),
                decoration: BoxDecoration(
                  color: sideTone.withValues(alpha: 0.14),
                  borderRadius: BorderRadius.circular(6),
                ),
                child: Text(entry.isShort ? 'SHORT' : 'LONG',
                    style: Obsidian.labelSm(color: sideTone, size: 10)),
              ),
              const SizedBox(width: 9),
              Text(
                  showSymbol
                      ? '${entry.size} ${_short(entry.symbol)}'
                      : '${entry.size} $short',
                  style: Obsidian.dataTable(size: 13.5, w: FontWeight.w600)),
              const Spacer(),
              if (!entry.isOpen)
                Container(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                  decoration: BoxDecoration(
                    color: Colors.white.withValues(alpha: 0.06),
                    borderRadius: BorderRadius.circular(6),
                  ),
                  child: Text('CLOSED',
                      style:
                          Obsidian.labelSm(color: Obsidian.outline, size: 9.5)),
                ),
            ],
          ),
          const SizedBox(height: 14),
          Row(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(signedPct(pct),
                      style: Obsidian.dataTable(
                          size: 26, color: tone, w: FontWeight.w700)),
                  const SizedBox(height: 2),
                  Text(
                      abs == null
                          ? 'waiting for a price'
                          : '${abs >= 0 ? '+' : ''}${money(abs, dp: 2)}',
                      style: Obsidian.dataTable(size: 13, color: tone)),
                ],
              ),
              const Spacer(),
              Column(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  _kv('Entry', money(entry.entryPrice)),
                  const SizedBox(height: 3),
                  _kv(entry.isOpen ? 'Now' : 'Exit', money(mark)),
                ],
              ),
            ],
          ),
          if (entry.takeProfit != null || entry.stopLoss != null || onEdit != null) ...[
            const SizedBox(height: 14),
            if (entry.takeProfit != null || entry.stopLoss != null) ...[
              _targetBar(),
              const SizedBox(height: 8),
            ],
            // THE LEVELS ARE THE CONTROL. Tapping the row opens the editor;
            // the small pencil says so without a second button competing
            // with "close" for the same width.
            InkWell(
              onTap: onEdit,
              borderRadius: BorderRadius.circular(Obsidian.rMd),
              child: Padding(
                padding: const EdgeInsets.symmetric(vertical: 4),
                child: Row(
                  children: [
                    Expanded(
                      child: _level('Take profit', entry.takeProfit,
                          Obsidian.green, entry),
                    ),
                    if (onEdit != null)
                      Padding(
                        padding: const EdgeInsets.symmetric(horizontal: 8),
                        child: Container(
                          padding: const EdgeInsets.symmetric(
                              horizontal: 8, vertical: 4),
                          decoration: BoxDecoration(
                            color: Colors.white.withValues(alpha: 0.06),
                            borderRadius: BorderRadius.circular(8),
                            border: Border.all(
                                color: Colors.white.withValues(alpha: 0.10)),
                          ),
                          child: Row(mainAxisSize: MainAxisSize.min, children: [
                            const Icon(Icons.edit_rounded,
                                size: 11, color: Obsidian.onSurfaceVariant),
                            const SizedBox(width: 4),
                            Text('EDIT',
                                style: Obsidian.labelSm(
                                    color: Obsidian.onSurfaceVariant, size: 8.5)),
                          ]),
                        ),
                      ),
                    Expanded(
                      child: _level('Stop loss', entry.stopLoss, Obsidian.red,
                          entry, right: true),
                    ),
                  ],
                ),
              ),
            ),
          ],
          if (onClose != null) ...[
            const SizedBox(height: 12),
            SizedBox(
              width: double.infinity,
              child: OutlinedButton(
                style: OutlinedButton.styleFrom(
                  side: BorderSide(color: Colors.white.withValues(alpha: 0.14)),
                  shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(Obsidian.rMd)),
                ),
                onPressed: onClose,
                child: Text('Close this position',
                    style: Obsidian.body(size: 12.5)),
              ),
            ),
          ],
        ],
      ),
    );
  }

  static String _short(String symbol) =>
      symbol.endsWith('USDT') ? symbol.substring(0, symbol.length - 4) : symbol;

  Widget _kv(String k, String v) => Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Text('$k ',
              style: Obsidian.body(color: Obsidian.outline, size: 11.5)),
          Text(v, style: Obsidian.dataTable(size: 13)),
        ],
      );

  /// Stop on the left, target on the right, entry in the middle. Empty when
  /// neither level was recorded — there is nothing to draw the ends at.
  Widget _targetBar() => BarrierBar(entry: entry, livePrice: livePrice);

  static Widget _level(String label, double? v, Color tone, TradeEntry e,
          {bool right = false}) =>
      Column(
        crossAxisAlignment:
            right ? CrossAxisAlignment.end : CrossAxisAlignment.start,
        children: [
          Text(label, style: Obsidian.body(color: Obsidian.outline, size: 10.5)),
          const SizedBox(height: 2),
          Text(money(v), style: Obsidian.dataTable(size: 12.5, color: tone)),
        ],
      );
}

/// Ask for the exit price, then close.
///
/// The price is ASKED FOR rather than taken from the live feed: you closed
/// the trade on an exchange, at a fill this app never saw, and recording the
/// current mid instead would put a number in your journal that never
/// happened. The live price is offered as the default because it is usually
/// close, and it is editable because usually is not always.
Future<double?> askExitPrice(BuildContext context, TradeEntry t,
    {double? livePrice}) async {
  final c = TextEditingController(text: priceInput(livePrice));
  return showDialog<double>(
    context: context,
    builder: (ctx) => AlertDialog(
      backgroundColor: Obsidian.surfaceContainer,
      title: Text('Close position', style: Obsidian.headlineMd()),
      content: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
              'What price did you actually get out at? Vanth never saw the '
              'fill, so this is the one number it cannot work out for you.',
              style: Obsidian.body(color: Obsidian.outline, size: 11.5)),
          const SizedBox(height: 12),
          TextField(
            controller: c,
            autofocus: true,
            keyboardType: const TextInputType.numberWithOptions(decimal: true),
            style: Obsidian.dataTable(size: 16),
          ),
        ],
      ),
      actions: [
        TextButton(
            onPressed: () => Navigator.of(ctx).pop(),
            child: Text('Cancel', style: Obsidian.body())),
        TextButton(
            onPressed: () => Navigator.of(ctx)
                .pop(double.tryParse(c.text.replaceAll(',', ''))),
            child: Text('Close',
                style: Obsidian.body(color: Obsidian.primary))),
      ],
    ),
  );
}


/// Edit an open entry's target and stop, in a sheet drawn like the rest of
/// the app. Returns (tp, sl) -- either null to clear that level -- or null
/// altogether on cancel.
///
/// Refuses a level on the wrong side of the price: a long's stop above the
/// price is not a stop, and would close the trade the moment it was saved.
/// Shows the distance of each level and the reward-to-risk as you type, so
/// the numbers mean something before they are committed.
Future<({double? tp, double? sl})?> askLevels(BuildContext context, TradeEntry t,
    {double? livePrice}) {
  return showModalBottomSheet<({double? tp, double? sl})>(
    context: context,
    backgroundColor: Colors.transparent,
    isScrollControlled: true,
    builder: (ctx) => _LevelsSheet(entry: t, livePrice: livePrice),
  );
}


class _LevelsSheet extends StatefulWidget {
  const _LevelsSheet({required this.entry, this.livePrice});
  final TradeEntry entry;
  final double? livePrice;

  @override
  State<_LevelsSheet> createState() => _LevelsSheetState();
}

class _LevelsSheetState extends State<_LevelsSheet> {
  late final TextEditingController _tp =
      TextEditingController(text: priceInput(widget.entry.takeProfit));
  late final TextEditingController _sl =
      TextEditingController(text: priceInput(widget.entry.stopLoss));
  String? _problem;

  TradeEntry get e => widget.entry;
  double get _ref => widget.livePrice ?? e.entryPrice;

  @override
  void initState() {
    super.initState();
    _tp.addListener(_changed);
    _sl.addListener(_changed);
  }

  @override
  void dispose() {
    _tp.dispose();
    _sl.dispose();
    super.dispose();
  }

  void _changed() => setState(() => _problem = null);

  static double? _parse(String v) {
    final x = v.trim().replaceAll(',', '');
    return x.isEmpty ? null : double.tryParse(x);
  }

  /// Signed distance from the entry, in the trade's favour.
  double? _pct(double? level) {
    if (level == null) return null;
    final s = e.isShort ? -1.0 : 1.0;
    return 100 * s * (level / e.entryPrice - 1);
  }

  String? _check(double? tp, double? sl) {
    final short = e.isShort;
    if (_tp.text.trim().isNotEmpty && tp == null ||
        _sl.text.trim().isNotEmpty && sl == null) {
      return 'That is not a number.';
    }
    if (tp != null && (short ? tp >= _ref : tp <= _ref)) {
      return short
          ? 'A short\'s target has to be below the price.'
          : 'A long\'s target has to be above the price.';
    }
    if (sl != null && (short ? sl <= _ref : sl >= _ref)) {
      return short
          ? 'A short\'s stop has to be above the price.'
          : 'A long\'s stop has to be below the price.';
    }
    return null;
  }

  void _save() {
    final tp = _parse(_tp.text), sl = _parse(_sl.text);
    final problem = _check(tp, sl);
    if (problem != null) {
      setState(() => _problem = problem);
      return;
    }
    Navigator.of(context).pop((tp: tp, sl: sl));
  }

  @override
  Widget build(BuildContext context) {
    final tp = _parse(_tp.text), sl = _parse(_sl.text);
    final tpPct = _pct(tp), slPct = _pct(sl);
    final rr = (tpPct != null && slPct != null && slPct < 0)
        ? tpPct / -slPct
        : null;
    final short = TradeRow.short(e.symbol);
    final tone = e.isShort ? Obsidian.red : Obsidian.green;
    final bottom = MediaQuery.of(context).viewInsets.bottom;

    return SafeArea(
      top: false,
      child: Padding(
        padding: EdgeInsets.fromLTRB(Obsidian.containerPadding, 0,
            Obsidian.containerPadding, Obsidian.containerPadding + bottom),
        child: GlassPanel(
          active: true,
          padding: const EdgeInsets.fromLTRB(22, 18, 22, 20),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Center(
                child: Container(
                  width: 36,
                  height: 4,
                  decoration: BoxDecoration(
                    color: Colors.white.withValues(alpha: 0.18),
                    borderRadius: BorderRadius.circular(2),
                  ),
                ),
              ),
              const SizedBox(height: 16),
              Row(
                children: [
                  Text('CHANGE THE LEVELS', style: Obsidian.labelSm(size: 11)),
                  const Spacer(),
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                    decoration: BoxDecoration(
                      color: tone.withValues(alpha: 0.14),
                      borderRadius: BorderRadius.circular(6),
                    ),
                    child: Text('$short ${e.side}',
                        style: Obsidian.labelSm(color: tone, size: 9.5)),
                  ),
                ],
              ),
              const SizedBox(height: 12),
              Row(
                children: [
                  Expanded(child: _fact('Entry', money(e.entryPrice))),
                  Expanded(child: _fact('Now', money(widget.livePrice))),
                  Expanded(child: _fact('Timeframe', e.interval ?? '—')),
                ],
              ),
              const SizedBox(height: 16),
              _field(
                controller: _tp,
                label: e.trailed ? 'TAKE PROFIT (optional)' : 'TAKE PROFIT',
                tone: Obsidian.green,
                hint: e.isShort ? 'below the price' : 'above the price',
                reading: tpPct == null
                    ? (e.trailed ? 'none — the trail is the exit' : 'none')
                    : '${tpPct >= 0 ? '+' : ''}${tpPct.toStringAsFixed(2)}% from entry',
              ),
              const SizedBox(height: 10),
              _field(
                controller: _sl,
                label: e.trailed ? 'STOP LOSS (trails from here)' : 'STOP LOSS',
                tone: Obsidian.red,
                hint: e.isShort ? 'above the price' : 'below the price',
                reading: slPct == null
                    ? 'none'
                    : '${slPct.toStringAsFixed(2)}% from entry',
              ),
              const SizedBox(height: 12),
              Row(
                children: [
                  Icon(
                      _problem != null
                          ? Icons.error_outline_rounded
                          : Icons.balance_rounded,
                      size: 14,
                      color: _problem != null ? Obsidian.error : Obsidian.outline),
                  const SizedBox(width: 6),
                  Expanded(
                    child: Text(
                        _problem ??
                            (rr != null
                                ? 'Reward to risk ${rr.toStringAsFixed(2)} : 1'
                                : e.trailed
                                    ? 'The stop follows each new swing and never moves back.'
                                    : 'Leave a field empty to have no level there.'),
                        style: Obsidian.body(
                            color: _problem != null ? Obsidian.error : Obsidian.outline,
                            size: 11.5)),
                  ),
                ],
              ),
              const SizedBox(height: 16),
              Row(
                children: [
                  Expanded(
                    child: SizedBox(
                      height: 44,
                      child: OutlinedButton(
                        style: OutlinedButton.styleFrom(
                          side: BorderSide(color: Colors.white.withValues(alpha: 0.14)),
                          shape: RoundedRectangleBorder(
                              borderRadius: BorderRadius.circular(Obsidian.rMd)),
                        ),
                        onPressed: () => Navigator.of(context).pop(),
                        child: Text('Cancel', style: Obsidian.body(size: 13)),
                      ),
                    ),
                  ),
                  const SizedBox(width: 10),
                  Expanded(
                    flex: 2,
                    child: SizedBox(
                      height: 44,
                      child: FilledButton(
                        style: FilledButton.styleFrom(
                          backgroundColor: Obsidian.primary,
                          foregroundColor: Obsidian.surfaceLowest,
                          shape: RoundedRectangleBorder(
                              borderRadius: BorderRadius.circular(Obsidian.rMd)),
                        ),
                        onPressed: _save,
                        child: Text('Save levels',
                            style: Obsidian.body(
                                    color: Obsidian.surfaceLowest, size: 13)
                                .copyWith(fontWeight: FontWeight.w600)),
                      ),
                    ),
                  ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _fact(String k, String v) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(k, style: Obsidian.body(color: Obsidian.outline, size: 10.5)),
          const SizedBox(height: 2),
          Text(v,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: Obsidian.dataTable(size: 12.5)),
        ],
      );

  Widget _field({
    required TextEditingController controller,
    required String label,
    required Color tone,
    required String hint,
    required String reading,
  }) =>
      Container(
        padding: const EdgeInsets.fromLTRB(14, 10, 14, 10),
        decoration: BoxDecoration(
          color: Colors.white.withValues(alpha: 0.04),
          borderRadius: BorderRadius.circular(Obsidian.rMd),
          border: Border.all(color: tone.withValues(alpha: 0.28)),
        ),
        child: Row(
          children: [
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(label, style: Obsidian.labelSm(color: tone, size: 9.5)),
                  TextField(
                    controller: controller,
                    keyboardType:
                        const TextInputType.numberWithOptions(decimal: true),
                    style: Obsidian.dataTable(size: 18),
                    decoration: InputDecoration(
                      isDense: true,
                      border: InputBorder.none,
                      hintText: hint,
                      hintStyle: Obsidian.body(color: Obsidian.outline, size: 13),
                      contentPadding: const EdgeInsets.only(top: 6, bottom: 2),
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(width: 12),
            Text(reading,
                textAlign: TextAlign.right,
                style: Obsidian.body(color: Obsidian.onSurfaceVariant, size: 11)),
          ],
        ),
      );
}


/// One trade as a LIST ROW, the shape the market screen uses.
///
/// WHY THIS EXISTS ALONGSIDE `PositionCard`
///     The card is right on a dashboard, where one pair is the subject of the
///     whole screen and there is room to show the barriers and a progress
///     bar. In a profile list of every trade you have ever logged, that same
///     card is a screenful each and you cannot see three of them at once.
///     Same data, same colours, a quarter of the height.
class TradeRow extends StatelessWidget {
  const TradeRow({
    super.key,
    required this.entry,
    this.livePrice,
    this.onClose,
    this.onTap,
  });

  final TradeEntry entry;
  final double? livePrice;
  final VoidCallback? onClose;
  final VoidCallback? onTap;

  static String short(String symbol) =>
      symbol.endsWith('USDT') ? symbol.substring(0, symbol.length - 4) : symbol;

  @override
  Widget build(BuildContext context) {
    final pct = entry.pnlPct(livePrice);
    final abs = entry.pnl(livePrice);
    final tone = pct == null
        ? Obsidian.outline
        : (pct >= 0 ? Obsidian.green : Obsidian.red);
    final sideTone = entry.isShort ? Obsidian.red : Obsidian.green;

    return GlassPanel(
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
      onTap: onTap,
      child: Row(
        children: [
          // The medallion doubles as the direction: a short is red, a long
          // green, so the list reads as positions before it reads as coins.
          Container(
            width: 40,
            height: 40,
            decoration: BoxDecoration(
              color: sideTone.withValues(alpha: 0.12),
              shape: BoxShape.circle,
              border: Border.all(color: sideTone.withValues(alpha: 0.35)),
            ),
            alignment: Alignment.center,
            child: Icon(
                entry.isShort
                    ? Icons.trending_down_rounded
                    : Icons.trending_up_rounded,
                size: 18,
                color: sideTone),
          ),
          const SizedBox(width: 11),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Flexible(
                      child: Text(short(entry.symbol),
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: Obsidian.bodyLg().copyWith(
                              fontWeight: FontWeight.w600, fontSize: 14.5)),
                    ),
                    const SizedBox(width: 6),
                    Text(entry.isShort ? 'SHORT' : 'LONG',
                        style: Obsidian.labelSm(color: sideTone, size: 9.5)),
                    if (!entry.isOpen) ...[
                      const SizedBox(width: 6),
                      // WHICH KIND OF CLOSED. "Closed at 90" does not say
                      // whether you took that price or your stop did, and
                      // the difference is the whole point of logging it.
                      Text(
                          switch (entry.closedBy) {
                            'take_profit' => 'HIT TP',
                            'stop_loss' => 'HIT SL',
                            'time_limit' => 'TIME LIMIT',
                            _ => 'CLOSED',
                          },
                          style: Obsidian.labelSm(
                              color: switch (entry.closedBy) {
                                'take_profit' => Obsidian.greenDim,
                                'stop_loss' => Obsidian.redSoft,
                                'time_limit' => Obsidian.amber,
                                _ => Obsidian.outline,
                              },
                              size: 9.5)),
                    ],
                  ],
                ),
                const SizedBox(height: 5),
                Text(
                    '${trim(entry.size)} @ ${money(entry.entryPrice)}'
                    '  ->  ${money(entry.markPrice(livePrice))}',
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style:
                        Obsidian.dataTable(size: 11, color: Obsidian.outline)),
              ],
            ),
          ),
          ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 96),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.end,
              children: [
                FittedBox(
                  fit: BoxFit.scaleDown,
                  alignment: Alignment.centerRight,
                  child: Text(signedPct(pct),
                      maxLines: 1,
                      style: Obsidian.dataTable(
                          size: 15, color: tone, w: FontWeight.w700)),
                ),
                const SizedBox(height: 3),
                Text(
                    abs == null
                        ? '—'
                        : '${abs >= 0 ? '+' : ''}${money(abs, dp: 2)}',
                    maxLines: 1,
                    style: Obsidian.dataTable(size: 11.5, color: tone)),
              ],
            ),
          ),
          if (onClose != null)
            SizedBox(
              width: 36,
              height: 40,
              child: IconButton(
                padding: EdgeInsets.zero,
                onPressed: onClose,
                tooltip: 'Close this position',
                icon: const Icon(Icons.check_circle_outline_rounded,
                    size: 19, color: Obsidian.outline),
              ),
            ),
        ],
      ),
    );
  }

  /// 0.50000000 is noise in a list. Trailing zeros go, the number stays exact.
  static String trim(double v) {
    var s = v.toStringAsFixed(8);
    if (s.contains('.')) {
      s = s.replaceFirst(RegExp(r'0+$'), '').replaceFirst(RegExp(r'\.$'), '');
    }
    return s;
  }
}


/// A logged trade, in the shape the profile mockup uses: identity and badges
/// on top, P&L on the right, the three levels along the bottom.
///
/// Wider than `TradeRow` and narrower than `PositionCard` — this is the list
/// you scan when reviewing what you did, so entry, target and stop all have
/// to be visible without a tap.
class JournalCard extends StatelessWidget {
  const JournalCard({
    super.key,
    required this.entry,
    this.livePrice,
    this.onClose,
    this.onDelete,
    this.onOpen,
    this.onEdit,
  });

  final TradeEntry entry;
  final double? livePrice;
  final VoidCallback? onClose, onDelete, onEdit;

  /// Tap anywhere on the card that is not a button: open this pair's
  /// dashboard, on the timeframe it was logged from.
  final VoidCallback? onOpen;

  static const _medallion = <String, Color>{
    'BTC': Color(0xFFF7931A), 'ETH': Color(0xFF6F8AE8),
    'SOL': Color(0xFFB07CF0), 'XRP': Color(0xFF6FD3E8),
    'DOGE': Color(0xFFD9C066), 'ZEC': Color(0xFFE8B36F),
  };

  @override
  Widget build(BuildContext context) {
    final short = TradeRow.short(entry.symbol);
    final tint = _medallion[short] ?? Obsidian.primary;
    final sideTone = entry.isShort ? Obsidian.red : Obsidian.green;
    final pct = entry.pnlPct(livePrice);
    final abs = entry.pnl(livePrice);
    final tone = pct == null
        ? Obsidian.outline
        : (pct >= 0 ? Obsidian.green : Obsidian.red);

    // A LIVE TRADE WEARS A GREEN RING.
    //
    // The list mixes open positions with a history of closed ones, and the
    // open ones are the only rows you can still do anything about. Colour
    // does that at a glance, where reading a small "ACTIVE" chip on every
    // card does not.
    final card = GlassPanel(
      padding: const EdgeInsets.all(14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Container(
                width: 34,
                height: 34,
                decoration: BoxDecoration(
                  color: tint.withValues(alpha: 0.10),
                  borderRadius: BorderRadius.circular(9),
                  border: Border.all(color: tint.withValues(alpha: 0.30)),
                ),
                alignment: Alignment.center,
                child: Text(short,
                    maxLines: 1,
                    style: Obsidian.labelSm(color: tint, size: 9.5)),
              ),
              const SizedBox(width: 10),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Wrap(
                      spacing: 6,
                      runSpacing: 4,
                      crossAxisAlignment: WrapCrossAlignment.center,
                      children: [
                        Text('$short / USDT',
                            style: Obsidian.bodyLg().copyWith(
                                fontSize: 13.5, fontWeight: FontWeight.w700)),
                        _chip(entry.isShort ? 'SELL / SHORT' : 'BUY / LONG',
                            sideTone, filled: true),
                        _statusChip(),
                      ],
                    ),
                    const SizedBox(height: 4),
                    Text(_when(),
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: Obsidian.dataTable(
                            size: 10.5, color: Obsidian.outline)),
                  ],
                ),
              ),
              const SizedBox(width: 6),
              Column(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  Text(signedPct(pct),
                      style: Obsidian.dataTable(
                          size: 13, color: tone, w: FontWeight.w700)),
                  const SizedBox(height: 2),
                  Text(
                      abs == null
                          ? '—'
                          : '${abs >= 0 ? '+' : ''}${money(abs, dp: 2)}',
                      style: Obsidian.dataTable(
                          size: 11.5, color: tone, w: FontWeight.w600)),
                ],
              ),
            ],
          ),
          const SizedBox(height: 11),
          Divider(height: 1, color: Colors.white.withValues(alpha: 0.06)),
          const SizedBox(height: 9),
          Row(
            children: [
              Expanded(child: _level('ENTRY', entry.entryPrice, null)),
              // THE PRICE IT IS MARKED AT. Entry, target and stop are all
              // fixed numbers you chose; without this the card shows four
              // things you already knew and nothing about where price
              // actually is.
              Expanded(
                  child: _level(entry.isOpen ? 'NOW' : 'EXIT',
                      entry.markPrice(livePrice), tone)),
              Expanded(
                  child: _level('TAKE PROFIT', entry.takeProfit,
                      Obsidian.green)),
              Expanded(
                  child: _level('STOP LOSS', entry.stopLoss, Obsidian.red)),
            ],
          ),
          // WHERE PRICE SITS BETWEEN YOUR STOP AND YOUR TARGET, for a live
          // trade that has at least one of them. The same widget the
          // dashboard card draws.
          if (entry.isOpen && entry.barrierPosition(livePrice) != null) ...[
            const SizedBox(height: 11),
            BarrierBar(entry: entry, livePrice: livePrice),
          ],
          if (onClose != null || onDelete != null) ...[
            const SizedBox(height: 11),
            Row(
              children: [
                if (onClose != null)
                  Expanded(
                    child: SizedBox(
                      height: 36,
                      child: OutlinedButton.icon(
                        style: OutlinedButton.styleFrom(
                          side: BorderSide(
                              color: Obsidian.primary.withValues(alpha: 0.35)),
                          shape: RoundedRectangleBorder(
                              borderRadius:
                                  BorderRadius.circular(Obsidian.rMd)),
                        ),
                        onPressed: onClose,
                        icon: const Icon(Icons.check_circle_outline_rounded,
                            size: 15, color: Obsidian.primary),
                        label: Text('Close log',
                            style: Obsidian.body(
                                color: Obsidian.primary, size: 12)),
                      ),
                    ),
                  ),
                if (onEdit != null) ...[
                  if (onClose != null) const SizedBox(width: 8),
                  SizedBox(
                    width: 42,
                    height: 36,
                    child: OutlinedButton(
                      style: OutlinedButton.styleFrom(
                        padding: EdgeInsets.zero,
                        side: BorderSide(
                            color: Colors.white.withValues(alpha: 0.10)),
                        shape: RoundedRectangleBorder(
                            borderRadius: BorderRadius.circular(Obsidian.rMd)),
                      ),
                      onPressed: onEdit,
                      child: const Icon(Icons.tune_rounded,
                          size: 16, color: Obsidian.onSurfaceVariant),
                    ),
                  ),
                ],
                if ((onClose != null || onEdit != null) && onDelete != null)
                  const SizedBox(width: 8),
                if (onDelete != null)
                  SizedBox(
                    width: 42,
                    height: 36,
                    child: OutlinedButton(
                      style: OutlinedButton.styleFrom(
                        padding: EdgeInsets.zero,
                        side: BorderSide(
                            color: Colors.white.withValues(alpha: 0.10)),
                        shape: RoundedRectangleBorder(
                            borderRadius: BorderRadius.circular(Obsidian.rMd)),
                      ),
                      onPressed: onDelete,
                      child: const Icon(Icons.delete_outline_rounded,
                          size: 16, color: Obsidian.outline),
                    ),
                  ),
              ],
            ),
          ],
        ],
      ),
    );

    final framed = !entry.isOpen
        ? card
        : Container(
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(Obsidian.rLg),
              border: Border.all(
                  color: Obsidian.green.withValues(alpha: 0.45), width: 1.4),
            ),
            child: card,
          );
    if (onOpen == null) return framed;
    // The buttons inside keep their own handlers; a tap that lands on them
    // never reaches this. Everything else on the card is "show me this".
    return InkWell(
      onTap: onOpen,
      borderRadius: BorderRadius.circular(Obsidian.rLg),
      child: framed,
    );
  }

  Widget _statusChip() {
    if (entry.isOpen) return _chip('ACTIVE', Obsidian.green, filled: true);
    if (entry.closedBy == 'take_profit') {
      return _chip('CLOSED TP', Obsidian.greenDim);
    }
    if (entry.closedBy == 'stop_loss') {
      return _chip('CLOSED SL', Obsidian.redSoft);
    }
    if (entry.closedBy == 'time_limit') {
      return _chip('TIME LIMIT', Obsidian.amber);
    }
    return _chip('CLOSED', Obsidian.outline);
  }

  /// "1d 6h left" / "window closed" -- how much of the model's horizon is
  /// left on an open entry. Null when the entry predates timeframes or the
  /// limit is off, in which case nothing is drawn rather than a dash that
  /// looks like a broken value.
  static String? timeLeftText(TradeEntry e, DateTime now) {
    final limit = e.timeLimit;
    if (limit == null || !e.isOpen) return null;
    final left = limit.difference(now);
    if (left.isNegative) return 'window closed';
    final h = left.inHours, m = left.inMinutes % 60;
    if (h >= 24) return '${h ~/ 24}d ${h % 24}h left';
    if (h >= 1) return '${h}h ${m}m left';
    return '${left.inMinutes}m left';
  }

  static Widget _chip(String text, Color c, {bool filled = false}) =>
      Container(
        padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
        decoration: BoxDecoration(
          color: c.withValues(alpha: filled ? 0.15 : 0.07),
          borderRadius: BorderRadius.circular(4),
          border: filled
              ? Border.all(color: c.withValues(alpha: 0.35))
              : null,
        ),
        child: Text(text, style: Obsidian.labelSm(color: c, size: 9)),
      );

  /// SCALED DOWN, NOT TRUNCATED.
  ///
  /// Four price columns on a narrow phone is tight, and a clipped price is
  /// worse than a small one — "$68,50…" is not a number you can act on.
  static Widget _level(String label, double? v, Color? tone) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(label,
              maxLines: 1,
              style: Obsidian.labelSm(color: Obsidian.outline, size: 8.5)),
          const SizedBox(height: 3),
          FittedBox(
            fit: BoxFit.scaleDown,
            alignment: Alignment.centerLeft,
            child: Text(money(v),
                maxLines: 1,
                style: Obsidian.dataTable(
                    size: 11.5, color: tone ?? Obsidian.onSurface)),
          ),
        ],
      );

  String _when() {
    final t = entry.openedAt.toLocal();
    final now = DateTime.now();
    final sameDay =
        t.year == now.year && t.month == now.month && t.day == now.day;
    const months = ['Jan','Feb','Mar','Apr','May','Jun',
                    'Jul','Aug','Sep','Oct','Nov','Dec'];
    final hhmm = '${t.hour.toString().padLeft(2, '0')}:'
        '${t.minute.toString().padLeft(2, '0')}';
    final when = sameDay ? 'Today, $hhmm' : '${months[t.month - 1]} ${t.day}, $hhmm';
    return '$when  ·  Size: ${TradeRow.trim(entry.size)} '
        '${TradeRow.short(entry.symbol)}';
  }
}


/// Stop at the left edge, target at the right, entry in the centre. The fill
/// grows out from the centre toward wherever price is: red on the stop
/// side, green on the target side.
///
/// THE BAR THIS REPLACES ran 0..100% "toward the target" and showed 0% for
/// every adverse move -- so a trade a hair above its stop and one sitting
/// exactly at entry were drawn identically. Price on the wrong side of the
/// entry is the case you most need to see, and it was the one case the bar
/// had no way to show.
///
/// The two halves are scaled independently (see `barrierPosition`), so the
/// ends ARE the levels you set, whatever their distances from the entry.
class BarrierBar extends StatelessWidget {
  const BarrierBar(
      {super.key, required this.entry, this.livePrice,
      this.showTimeLeft = true});

  final TradeEntry entry;
  final double? livePrice;

  /// The countdown under the percentage. Off when the time limit is
  /// switched off in Preferences -- a countdown to nothing is a lie.
  final bool showTimeLeft;

  @override
  Widget build(BuildContext context) {
    final pos = entry.barrierPosition(livePrice);
    final track = Colors.white.withValues(alpha: 0.06);
    final left = pos == null || pos >= 0 ? 0.0 : -pos;
    final right = pos == null || pos <= 0 ? 0.0 : pos;
    // THE FURTHEST IT HAS BEEN, each way, since the entry. Drawn under the
    // solid fill in a lighter tone, so the part that shows is exactly the
    // stretch between where price is and where it got to: "it reached 90%
    // to TP and has come back to 85%". Never narrower than the solid fill,
    // because the extremes include the current price by construction.
    final best = entry.bestPosition ?? 0.0;
    final worst = entry.worstPosition ?? 0.0;
    final tone = pos == null
        ? Obsidian.outline
        : pos < 0
            ? Obsidian.red
            : Obsidian.green;
    final timeLeft = showTimeLeft
        ? JournalCard.timeLeftText(entry, DateTime.now())
        : null;
    final pct = pos == null ? null : (pos.abs() * 100).round();
    final caption = pct == null
        ? '\u2014'
        : pos! < 0
            ? '$pct% to SL'
            : '$pct% to TP';

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        SizedBox(
          height: 6,
          child: Stack(
            children: [
              Positioned.fill(
                child: ClipRRect(
                  borderRadius: BorderRadius.circular(3),
                  child: ColoredBox(color: track),
                ),
              ),
              // Two halves. The left one grows leftwards from the centre,
              // the right one rightwards. The extremes go first and the
              // live fill on top; both sides can carry an extreme at once
              // -- a trade that dipped toward the stop and then ran toward
              // the target shows a light red on the left and a solid green
              // on the right.
              _halves(
                left: worst,
                right: best,
                leftTone: Obsidian.red.withValues(alpha: 0.28),
                rightTone: Obsidian.green.withValues(alpha: 0.28),
              ),
              _halves(
                left: left,
                right: right,
                leftTone: tone,
                rightTone: tone,
              ),
              // The entry, drawn last so it stays visible through the fill.
              Align(
                alignment: Alignment.center,
                child: Container(
                  width: 2,
                  height: 6,
                  color: Obsidian.onSurface.withValues(alpha: 0.85),
                ),
              ),
            ],
          ),
        ),
        const SizedBox(height: 4),
        Row(
          children: [
            Text(
              entry.stopLoss == null ? 'SL \u2014' : 'SL ${priceText(entry.stopLoss)}',
              style: Obsidian.dataTable(size: 9.5, color: Obsidian.red),
            ),
            const Spacer(),
            Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(caption,
                    style: Obsidian.dataTable(size: 10, color: tone)),
                if (timeLeft != null)
                  Text(timeLeft,
                      style: Obsidian.dataTable(
                          size: 9, color: Obsidian.outline)),
              ],
            ),
            const Spacer(),
            Text(
              entry.takeProfit == null
                  ? 'TP \u2014'
                  : 'TP ${priceText(entry.takeProfit)}',
              style: Obsidian.dataTable(size: 9.5, color: Obsidian.green),
            ),
          ],
        ),
      ],
    );
  }

  static Widget _halves({
    required double left,
    required double right,
    required Color leftTone,
    required Color rightTone,
  }) =>
      Row(
        children: [
          Expanded(
            child: Align(
              alignment: Alignment.centerRight,
              child: FractionallySizedBox(
                widthFactor: left.clamp(0.0, 1.0),
                child: _fill(leftTone, leftSide: true),
              ),
            ),
          ),
          Expanded(
            child: Align(
              alignment: Alignment.centerLeft,
              child: FractionallySizedBox(
                widthFactor: right.clamp(0.0, 1.0),
                child: _fill(rightTone, leftSide: false),
              ),
            ),
          ),
        ],
      );

  static Widget _fill(Color tone, {required bool leftSide}) => Container(
        height: 6,
        decoration: BoxDecoration(
          color: tone,
          borderRadius: BorderRadius.horizontal(
            left: leftSide ? const Radius.circular(3) : Radius.zero,
            right: leftSide ? Radius.zero : const Radius.circular(3),
          ),
        ),
      );
}
