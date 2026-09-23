/// Logging a trade you already made, and watching it afterwards.
///
/// THE FRAMING IS LOAD-BEARING
///     Every label here says LOG, never BUY. This app holds no exchange key
///     and places no orders; these panels record a decision you took
///     somewhere else so the app can mark it against the same price feed it
///     draws everything else from. The header says "did you enter this
///     trade?" for exactly that reason, and it should stay saying it.
library;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../api/format.dart';
import '../api/trades.dart';
import '../theme/liquid_obsidian.dart';
import 'glass.dart';

String _money(double? v, {int? dp}) {
  if (v == null) return '—';
  final d = dp ?? priceDecimals(v);
  final s = v.toStringAsFixed(d);
  final parts = s.split('.');
  final whole = parts[0].replaceAllMapped(
      RegExp(r'(\d)(?=(\d{3})+$)'), (m) => '${m[1]},');
  return '\$$whole.${parts[1]}';
}

/// The form. Prefilled from the model's own call where one exists, because
/// re-typing four numbers you are looking at is how a journal stops being
/// kept.
class LogEntryCard extends StatefulWidget {
  const LogEntryCard({
    super.key,
    required this.symbol,
    required this.short,
    this.interval,
    this.livePrice,
    this.suggestedSide,
    this.suggestedTp,
    this.suggestedSl,
    this.suggestedEntry,
    this.onLogged,
  });

  final String symbol, short;

  /// The timeframe this card sits under, stamped onto the entry so it can
  /// be reopened on the same view from Profile.
  final String? interval;
  final double? livePrice;

  /// What the dashboard is currently advising, used only as a starting
  /// position for the toggle. It is a default, not a recommendation to act.
  final String? suggestedSide;
  final double? suggestedTp, suggestedSl;

  /// The call's resting order, when it enters with one: the entry price
  /// starts there rather than at the market, since that is where the trade
  /// the levels describe begins. MARKET beside the field still fills in the
  /// live price for someone who bought at the market anyway.
  final double? suggestedEntry;
  final VoidCallback? onLogged;

  @override
  State<LogEntryCard> createState() => _LogEntryCardState();
}

class _LogEntryCardState extends State<LogEntryCard> {
  late final TextEditingController _size;
  late final TextEditingController _entry;
  late final TextEditingController _tp;
  late final TextEditingController _sl;
  String _side = 'LONG';
  double? _balance;
  int? _pct;
  bool _saving = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _size = TextEditingController();
    _entry = TextEditingController(
        text: priceInput(widget.suggestedEntry ?? widget.livePrice));
    _tp = TextEditingController(text: priceInput(widget.suggestedTp));
    _sl = TextEditingController(text: priceInput(widget.suggestedSl));
    _side = widget.suggestedSide == 'SHORT' ? 'SHORT' : 'LONG';
    Trades.instance.balance().then(
        (b) => mounted ? setState(() => _balance = b) : null);
  }

  // every digit the coin moves in -- see `priceDecimals`
  static String _fmt(double? v) => priceInput(v);

  @override
  void dispose() {
    for (final c in [_size, _entry, _tp, _sl]) {
      c.dispose();
    }
    super.dispose();
  }

  double? get _entryPrice => double.tryParse(_entry.text.replaceAll(',', ''));
  double? get _sizeVal => double.tryParse(_size.text.replaceAll(',', ''));

  /// Percentage from entry to the level, in the direction of the trade.
  double? _levelPct(String text) {
    final v = double.tryParse(text.replaceAll(',', ''));
    final e = _entryPrice;
    if (v == null || e == null || e <= 0) return null;
    final raw = (v - e) / e * 100.0;
    return _side == 'SHORT' ? -raw : raw;
  }

  Future<void> _pickBalance() async {
    final c = TextEditingController(text: _balance?.toString() ?? '');
    final v = await showDialog<double>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: Obsidian.surfaceContainer,
        title: Text('Available ${widget.short}',
            style: Obsidian.headlineMd()),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
                'Vanth holds no exchange key, so it cannot read your '
                'balance. Enter it yourself and the percentage buttons will '
                'work from that figure.',
                style: Obsidian.body(color: Obsidian.outline, size: 11.5)),
            const SizedBox(height: 12),
            TextField(
              controller: c,
              autofocus: true,
              keyboardType: const TextInputType.numberWithOptions(decimal: true),
              style: Obsidian.dataTable(size: 15),
              decoration: InputDecoration(hintText: '1.42',
                  hintStyle: Obsidian.dataTable(size: 15, color: Obsidian.outline)),
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
              child: Text('Save',
                  style: Obsidian.body(color: Obsidian.primary))),
        ],
      ),
    );
    if (v == null || v <= 0 || !mounted) return;
    await Trades.instance.saveBalance(v);
    if (mounted) setState(() => _balance = v);
  }

  Future<void> _submit() async {
    final size = _sizeVal;
    final entry = _entryPrice;
    if (size == null || size <= 0) {
      setState(() => _error = 'Enter how much you bought or sold.');
      return;
    }
    if (entry == null || entry <= 0) {
      setState(() => _error = 'Enter the price you got filled at.');
      return;
    }
    setState(() {
      _saving = true;
      _error = null;
    });
    await Trades.instance.add(TradeEntry.create(
      symbol: widget.symbol,
      side: _side,
      size: size,
      entryPrice: entry,
      takeProfit: double.tryParse(_tp.text.replaceAll(',', '')),
      stopLoss: double.tryParse(_sl.text.replaceAll(',', '')),
      interval: widget.interval,
    ));
    if (!mounted) return;
    _size.clear();
    setState(() => _saving = false);
    widget.onLogged?.call();
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
      backgroundColor: Obsidian.surfaceHigh,
      content: Text('Logged. It is in your positions now — this app did not '
          'place anything.', style: Obsidian.body()),
    ));
  }

  @override
  Widget build(BuildContext context) {
    final green = Obsidian.green;
    return GlassPanel(
      padding: const EdgeInsets.all(18),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(Icons.check_box_rounded, color: green, size: 22),
              const SizedBox(width: 10),
              Expanded(
                child: Text('Log Market Entry', style: Obsidian.headlineMd()),
              ),
              Container(
                padding:
                    const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
                decoration: BoxDecoration(
                  border: Border.all(color: green.withValues(alpha: 0.5)),
                  borderRadius: BorderRadius.circular(20),
                ),
                child: Row(mainAxisSize: MainAxisSize.min, children: [
                  Container(
                      width: 6,
                      height: 6,
                      decoration:
                          BoxDecoration(color: green, shape: BoxShape.circle)),
                  const SizedBox(width: 6),
                  Text('READY',
                      style: Obsidian.labelSm(color: green, size: 10.5)),
                ]),
              ),
            ],
          ),
          const SizedBox(height: 4),
          // NOT a buy button, and it says so before anything else.
          Text('Manual trade entry · Did you enter this trade?',
              style: Obsidian.body(color: Obsidian.outline, size: 12)),
          const SizedBox(height: 6),
          Text(
              'Set a take profit or stop loss and the app closes the entry '
              'in your log when price reaches it. It still places no orders.',
              style: Obsidian.body(color: Obsidian.outline, size: 11)),
          const SizedBox(height: 16),
          _sideToggle(),
          const SizedBox(height: 18),
          _label('SIZE / AMOUNT', trailing: GestureDetector(
            onTap: _pickBalance,
            child: Text(
                _balance == null
                    ? 'Set available'
                    : 'Avail: ${_balance!.toStringAsFixed(4)} ${widget.short}',
                style: Obsidian.dataTable(size: 11.5, color: Obsidian.outline)),
          )),
          const SizedBox(height: 8),
          _field(_size, hint: '0.00', suffix: widget.short, onChanged: (_) {
            setState(() => _pct = null);
          }, approx: () {
            final s = _sizeVal, e = _entryPrice ?? widget.livePrice;
            return (s == null || e == null) ? null : '≈ ${_money(s * e, dp: 2)}';
          }()),
          const SizedBox(height: 10),
          _percentRow(),
          const SizedBox(height: 18),
          _label('ENTRY PRICE'),
          const SizedBox(height: 8),
          _field(_entry,
              hint: '0.00',
              onChanged: (_) => setState(() {}),
              trailingLabel: widget.livePrice == null ? null : 'MARKET',
              onTrailingTap: widget.livePrice == null
                  ? null
                  : () => setState(() => _entry.text = _fmt(widget.livePrice))),
          const SizedBox(height: 18),
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                  child: _levelField('TAKE PROFIT (TP)', _tp, Obsidian.green,
                      'Gain')),
              const SizedBox(width: 12),
              Expanded(
                  child:
                      _levelField('STOP LOSS (SL)', _sl, Obsidian.red, 'Risk')),
            ],
          ),
          if (_error != null) ...[
            const SizedBox(height: 12),
            Text(_error!,
                style: Obsidian.body(color: Obsidian.redSoft, size: 12)),
          ],
          const SizedBox(height: 18),
          SizedBox(
            height: 52,
            width: double.infinity,
            child: FilledButton.icon(
              style: FilledButton.styleFrom(
                backgroundColor: green,
                foregroundColor: Colors.black,
                shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(Obsidian.rMd)),
              ),
              onPressed: _saving ? null : _submit,
              icon: _saving
                  ? const SizedBox(
                      width: 16,
                      height: 16,
                      child: CircularProgressIndicator(
                          strokeWidth: 2, color: Colors.black))
                  : const Icon(Icons.check_circle_rounded, size: 20),
              label: Text('Log Trade Entry',
                  style: Obsidian.labelSm(color: Colors.black, size: 14)),
            ),
          ),
        ],
      ),
    );
  }

  Widget _sideToggle() {
    Widget half(String value, String text, IconData icon, Color c) {
      final on = _side == value;
      return Expanded(
        child: GestureDetector(
          onTap: () => setState(() => _side = value),
          child: AnimatedContainer(
            duration: const Duration(milliseconds: 140),
            padding: const EdgeInsets.symmetric(vertical: 16),
            decoration: BoxDecoration(
              color: on ? c.withValues(alpha: 0.12) : Colors.transparent,
              border: Border.all(
                  color: on ? c.withValues(alpha: 0.7) : Colors.transparent,
                  width: 1.5),
              borderRadius: BorderRadius.circular(Obsidian.rMd),
            ),
            child: Row(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                Icon(icon, size: 17, color: on ? c : Obsidian.outline),
                const SizedBox(width: 8),
                Text(text,
                    style: Obsidian.labelSm(
                        color: on ? c : Obsidian.outline, size: 12.5)),
              ],
            ),
          ),
        ),
      );
    }

    return Container(
      padding: const EdgeInsets.all(5),
      decoration: BoxDecoration(
        color: Colors.white.withValues(alpha: 0.03),
        borderRadius: BorderRadius.circular(Obsidian.rLg),
      ),
      child: Row(children: [
        half('LONG', 'BUY / LONG', Icons.trending_up_rounded, Obsidian.green),
        half('SHORT', 'SELL / SHORT', Icons.trending_down_rounded, Obsidian.red),
      ]),
    );
  }

  Widget _percentRow() {
    final disabled = _balance == null;
    return Row(
      children: [25, 50, 75, 100].map((p) {
        final on = _pct == p;
        return Expanded(
          child: Padding(
            padding: EdgeInsets.only(right: p == 100 ? 0 : 8),
            child: GestureDetector(
              onTap: () {
                if (disabled) {
                  _pickBalance();
                  return;
                }
                setState(() {
                  _pct = p;
                  _size.text =
                      (_balance! * p / 100).toStringAsFixed(6);
                });
              },
              child: Container(
                padding: const EdgeInsets.symmetric(vertical: 11),
                alignment: Alignment.center,
                decoration: BoxDecoration(
                  color: on
                      ? Obsidian.green.withValues(alpha: 0.14)
                      : Colors.white.withValues(alpha: 0.04),
                  borderRadius: BorderRadius.circular(Obsidian.rSm + 4),
                  border: Border.all(
                      color: on
                          ? Obsidian.green.withValues(alpha: 0.6)
                          : Colors.transparent),
                ),
                child: Text('$p%',
                    style: Obsidian.dataTable(
                        size: 12.5,
                        color: on
                            ? Obsidian.green
                            : (disabled
                                ? Obsidian.outline.withValues(alpha: 0.5)
                                : Obsidian.onSurface))),
              ),
            ),
          ),
        );
      }).toList(),
    );
  }

  Widget _label(String text, {Widget? trailing}) => Row(
        children: [
          Expanded(child: Text(text, style: Obsidian.labelSm(size: 10.5))),
          ?trailing,
        ],
      );

  Widget _field(TextEditingController c,
      {String? hint,
      String? suffix,
      String? approx,
      String? trailingLabel,
      VoidCallback? onTrailingTap,
      ValueChanged<String>? onChanged}) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 4),
      decoration: BoxDecoration(
        color: Colors.black.withValues(alpha: 0.28),
        borderRadius: BorderRadius.circular(Obsidian.rMd),
        border: Border.all(color: Colors.white.withValues(alpha: 0.07)),
      ),
      child: Row(
        children: [
          Expanded(
            child: TextField(
              controller: c,
              onChanged: onChanged,
              keyboardType:
                  const TextInputType.numberWithOptions(decimal: true),
              inputFormatters: [
                FilteringTextInputFormatter.allow(RegExp(r'[0-9.,]')),
              ],
              style: Obsidian.dataTable(size: 17, w: FontWeight.w600),
              decoration: InputDecoration(
                border: InputBorder.none,
                isDense: true,
                contentPadding: const EdgeInsets.symmetric(vertical: 14),
                hintText: hint,
                hintStyle: Obsidian.dataTable(
                    size: 17, color: Obsidian.outline.withValues(alpha: 0.6)),
                suffixText: suffix,
                suffixStyle:
                    Obsidian.dataTable(size: 13, color: Obsidian.outline),
              ),
            ),
          ),
          if (approx != null)
            Text(approx,
                style: Obsidian.dataTable(size: 12.5, color: Obsidian.outline)),
          if (trailingLabel != null)
            GestureDetector(
              onTap: onTrailingTap,
              child: Padding(
                padding: const EdgeInsets.only(left: 8),
                child: Text(trailingLabel,
                    style:
                        Obsidian.labelSm(color: Obsidian.primary, size: 10.5)),
              ),
            ),
        ],
      ),
    );
  }

  Widget _levelField(
      String label, TextEditingController c, Color tone, String word) {
    final pct = _levelPct(c.text);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(label, style: Obsidian.labelSm(size: 10.5)),
        const SizedBox(height: 8),
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 14),
          decoration: BoxDecoration(
            color: Colors.black.withValues(alpha: 0.28),
            borderRadius: BorderRadius.circular(Obsidian.rMd),
            border: Border.all(color: tone.withValues(alpha: 0.45)),
          ),
          child: TextField(
            controller: c,
            onChanged: (_) => setState(() {}),
            keyboardType: const TextInputType.numberWithOptions(decimal: true),
            inputFormatters: [
              FilteringTextInputFormatter.allow(RegExp(r'[0-9.,]')),
            ],
            style: Obsidian.dataTable(size: 16, color: tone, w: FontWeight.w600),
            decoration: InputDecoration(
              border: InputBorder.none,
              isDense: true,
              contentPadding: const EdgeInsets.symmetric(vertical: 15),
              hintText: '—',
              hintStyle: Obsidian.dataTable(
                  size: 16, color: Obsidian.outline.withValues(alpha: 0.5)),
            ),
          ),
        ),
        const SizedBox(height: 5),
        Text(
            pct == null
                ? 'optional'
                : '${pct >= 0 ? '+' : ''}${pct.toStringAsFixed(2)}% $word',
            style: Obsidian.body(
                color: pct == null ? Obsidian.outline : tone, size: 11)),
      ],
    );
  }
}
