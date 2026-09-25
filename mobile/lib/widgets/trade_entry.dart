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

/// "Sat 14:00 UTC": when a limit order stops being good, in the same clock
/// the order chip on the card above uses.
String _hhmm(DateTime t) {
  final u = t.toUtc();
  const days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  return '${days[u.weekday - 1]} ${u.hour.toString().padLeft(2, '0')}:'
      '${u.minute.toString().padLeft(2, '0')} UTC';
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
    this.limitPrice,
    this.limitResting = false,
    this.limitUntil,
    this.limitTp,
    this.limitSl,
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

  /// THE CALL'S ORDER, for the LIMIT button beside the entry price: the
  /// limit while it rests, the fill once it has filled. `limitTp`/`limitSl`
  /// are that order's target and stop. Null when the call has no order.
  final double? limitPrice, limitTp, limitSl;

  /// The order has not filled yet. Logging it at the limit then logs an
  /// ORDER, not a trade: it waits for price to reach the limit, until
  /// `limitUntil`, and only then counts.
  final bool limitResting;
  final DateTime? limitUntil;
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

  /// The dollar amount the size came from, when a dollar button set it:
  /// the coins follow the entry price while it is set, and typing the
  /// coins yourself clears it.
  double? _usd;

  /// The dollar figure behind "Your amount", remembered between trades.
  double? _customUsd;

  /// Fields the person has typed in (or tapped) themselves. The rest follow
  /// the call: see `didUpdateWidget`.
  bool _entryTouched = false, _tpTouched = false, _slTouched = false, _sideTouched = false;
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
    Trades.instance.customUsd().then(
        (v) => mounted ? setState(() => _customUsd = v) : null);
  }

  // every digit the coin moves in -- see `priceDecimals`
  static String _fmt(double? v) => priceInput(v);

  /// THE FORM FOLLOWS THE CALL. The fields used to be filled once, when the
  /// card was first built, and never again -- so switching the page from 1d
  /// (which leaves the take profit empty on purpose) to 4h kept the empty
  /// field, and a call that arrived while the page was open kept yesterday's
  /// levels. Now: a new coin or timeframe starts the form over, and a changed
  /// suggestion refreshes every field the person has not typed in.
  @override
  void didUpdateWidget(covariant LogEntryCard old) {
    super.didUpdateWidget(old);
    if (old.symbol != widget.symbol || old.interval != widget.interval) {
      _entryTouched = _tpTouched = _slTouched = _sideTouched = false;
      _usd = null;
      _size.clear();
      _refill(force: true);
      return;
    }
    _refill(old: old);
  }

  void _refill({LogEntryCard? old, bool force = false}) {
    final w = widget;
    if (!_tpTouched && (force || old?.suggestedTp != w.suggestedTp)) {
      _tp.text = priceInput(w.suggestedTp);
    }
    if (!_slTouched && (force || old?.suggestedSl != w.suggestedSl)) {
      _sl.text = priceInput(w.suggestedSl);
    }
    if (!_entryTouched && (force || (old?.suggestedEntry != w.suggestedEntry))) {
      final e = w.suggestedEntry ?? (force ? w.livePrice : null);
      if (e != null) {
        _entry.text = priceInput(e);
        _followUsd();
      }
    }
    if (!_sideTouched && (force || old?.suggestedSide != w.suggestedSide)) {
      _side = w.suggestedSide == 'SHORT' ? 'SHORT' : 'LONG';
    }
  }

  @override
  void dispose() {
    for (final c in [_size, _entry, _tp, _sl]) {
      c.dispose();
    }
    super.dispose();
  }

  double? get _entryPrice => double.tryParse(_entry.text.replaceAll(',', ''));

  /// Logging the call's RESTING order at its limit, with the market not
  /// there yet: this is an order waiting to fill, not a trade.
  bool get _waitingLimit {
    final w = widget, e = _entryPrice, lim = w.limitPrice, live = w.livePrice;
    if (!w.limitResting || lim == null || e == null) return false;
    // the same number as typed: the field holds the limit rounded to the
    // digits the coin moves in, not the limit itself
    if (priceInput(e) != priceInput(lim)) return false;
    if (live == null) return true;
    final short = _side == 'SHORT';
    return short ? live < lim : live > lim;
  }

  /// LIMIT: the call's order -- its price, target and stop -- back in the
  /// form, following the call again from here.
  void _useLimit() {
    final w = widget;
    setState(() {
      _entryTouched = _tpTouched = _slTouched = _sideTouched = false;
      _side = w.suggestedSide == 'SHORT' ? 'SHORT' : 'LONG';
      _entry.text = priceInput(w.limitPrice);
      if (w.interval != '1d') _tp.text = priceInput(w.limitTp ?? w.suggestedTp);
      _sl.text = priceInput(w.limitSl ?? w.suggestedSl);
      _followUsd();
      _error = null;
    });
  }

  double? get _sizeVal => double.tryParse(_size.text.replaceAll(',', ''));

  /// Percentage from entry to the level, in the direction of the trade.
  double? _levelPct(String text) {
    final v = double.tryParse(text.replaceAll(',', ''));
    final e = _entryPrice;
    if (v == null || e == null || e <= 0) return null;
    final raw = (v - e) / e * 100.0;
    return _side == 'SHORT' ? -raw : raw;
  }

  /// THE SIZE IN DOLLARS. A trade is usually decided as "$250 of this",
  /// not as a number of coins; the buttons turn dollars into coins at the
  /// entry price (the limit when the call has one, else the price you typed,
  /// else the live price) and write them where the coins are typed.
  static const _usdButtons = [100.0, 250.0, 1000.0];

  void _fillFromUsd(double usd) {
    final px = _entryPrice ?? widget.livePrice;
    if (px == null || px <= 0) {
      setState(() => _error = 'Enter the entry price first, then pick the amount.');
      return;
    }
    setState(() {
      _usd = usd;
      _error = null;
      _size.text = _coins(usd / px);
    });
  }

  /// Coins to type: enough digits for a $100 position in BTC (0.001163)
  /// and none wasted on one in PEPE (18,904,109.59).
  static String _coins(double q) {
    String t;
    if (q >= 1000) {
      t = q.toStringAsFixed(2);
    } else if (q >= 1) {
      t = q.toStringAsFixed(4);
    } else {
      t = q.toStringAsPrecision(6);
    }
    if (t.contains('.') && !t.contains('e')) {
      t = t.replaceFirst(RegExp(r'0+$'), '').replaceFirst(RegExp(r'\.$'), '');
    }
    return t;
  }

  Future<void> _pickAmount() async {
    final c = TextEditingController(
        text: _customUsd == null ? '' : _customUsd!.toStringAsFixed(0));
    final v = await showDialog<double>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: Obsidian.surfaceContainer,
        title: Text('Your amount', style: Obsidian.headlineMd()),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
                'How many dollars to put into this trade. The app works out '
                'the number of ${widget.short} at the entry price and fills it '
                'in, and remembers the amount for next time.',
                style: Obsidian.body(color: Obsidian.outline, size: 11.5)),
            const SizedBox(height: 12),
            TextField(
              controller: c,
              autofocus: true,
              keyboardType: const TextInputType.numberWithOptions(decimal: true),
              style: Obsidian.dataTable(size: 15),
              decoration: InputDecoration(
                  prefixText: '\$ ',
                  hintText: '500',
                  hintStyle: Obsidian.dataTable(size: 15, color: Obsidian.outline)),
            ),
          ],
        ),
        actions: [
          TextButton(
              onPressed: () => Navigator.of(ctx).pop(),
              child: Text('Cancel', style: Obsidian.body())),
          TextButton(
              onPressed: () => Navigator.of(ctx).pop(
                  double.tryParse(c.text.replaceAll(',', '').replaceAll('\$', '').trim())),
              child: Text('Use it', style: Obsidian.body(color: Obsidian.primary))),
        ],
      ),
    );
    if (v == null || v <= 0 || !mounted) return;
    await Trades.instance.saveCustomUsd(v);
    if (!mounted) return;
    setState(() => _customUsd = v);
    _fillFromUsd(v);
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
    final waiting = _waitingLimit;
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
      // an order good for as long as the call's is; a day when the call
      // does not say
      pendingUntil: waiting
          ? (widget.limitUntil ?? DateTime.now().toUtc().add(const Duration(hours: 24)))
          : null,
    ));
    if (!mounted) return;
    _size.clear();
    _usd = null;
    // the next entry starts from the call again
    _entryTouched = _tpTouched = _slTouched = _sideTouched = false;
    _refill(force: true);
    setState(() => _saving = false);
    widget.onLogged?.call();
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
      backgroundColor: Obsidian.surfaceHigh,
      content: Text(
          waiting
              ? 'Logged as a limit order. It counts from when price reaches '
                  '${priceText(entry)}; if it does not in time, it closes as '
                  'not filled. This app did not place anything.'
              : 'Logged. It is in your positions now — this app did not '
                  'place anything.',
          style: Obsidian.body()),
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
                child: Text('Log Your Entry', style: Obsidian.headlineMd()),
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
          _label('SIZE / AMOUNT'),
          const SizedBox(height: 8),
          _field(_size, hint: '0.00', suffix: widget.short, onChanged: (_) {
            // typed by hand: the coins are yours now, not a dollar button's
            setState(() => _usd = null);
          }, approx: () {
            final s = _sizeVal, e = _entryPrice ?? widget.livePrice;
            return (s == null || e == null) ? null : '≈ ${_money(s * e, dp: 2)}';
          }()),
          const SizedBox(height: 10),
          _dollarRow(),
          const SizedBox(height: 18),
          _label('ENTRY PRICE'),
          const SizedBox(height: 8),
          _field(_entry,
              hint: '0.00',
              onChanged: (_) => setState(() {
                    _entryTouched = true;
                    _followUsd();
                  }),
              trailing: [
                // THE CALL'S ORDER: its limit (or its fill), target and stop
                if (widget.limitPrice != null) ('LIMIT', _useLimit),
                if (widget.livePrice != null)
                  ('MARKET', () => setState(() {
                        _entryTouched = true;
                        _entry.text = _fmt(widget.livePrice);
                        _followUsd();
                      })),
              ]),
          if (_waitingLimit) ...[
            const SizedBox(height: 6),
            Text(
                'A limit order: the price is not there yet. The entry counts '
                'from when price reaches ${priceText(_entryPrice)}'
                '${widget.limitUntil == null ? '' : ', and closes as not filled if it has not by ${_hhmm(widget.limitUntil!)}'}.',
                style: Obsidian.body(color: Obsidian.primary, size: 11)),
          ],
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
              label: Text(_waitingLimit ? 'Log Limit Order' : 'Log Trade Entry',
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
          onTap: () => setState(() {
            _side = value;
            _sideTouched = true;
          }),
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

  /// A dollar button's size keeps its dollars when the entry price moves.
  void _followUsd() {
    final usd = _usd, px = _entryPrice ?? widget.livePrice;
    if (usd != null && px != null && px > 0) _size.text = _coins(usd / px);
  }

  Widget _dollarRow() {
    final custom = _customUsd;
    final buttons = <(String, double?)>[
      for (final v in _usdButtons) ('\$${v.toStringAsFixed(0)}', v),
      (custom == null ? 'Your amount' : '\$${custom.toStringAsFixed(custom % 1 == 0 ? 0 : 2)}', null),
    ];
    return Row(
      children: [
        for (var i = 0; i < buttons.length; i++)
          Expanded(
            child: Padding(
              padding: EdgeInsets.only(right: i == buttons.length - 1 ? 0 : 8),
              child: _usdChip(buttons[i].$1, buttons[i].$2, i == buttons.length - 1),
            ),
          ),
      ],
    );
  }

  Widget _usdChip(String label, double? usd, bool isCustom) {
    final on = _usd != null &&
        (isCustom ? (_customUsd != null && _usd == _customUsd && !_usdButtons.contains(_usd))
                  : _usd == usd);
    return GestureDetector(
      // the custom one always opens its dialog, prefilled with the amount
      // it remembers, so changing it is never hidden behind a gesture
      onTap: () => isCustom ? _pickAmount() : _fillFromUsd(usd!),
      child: Container(
        padding: const EdgeInsets.symmetric(vertical: 11),
        alignment: Alignment.center,
        decoration: BoxDecoration(
          color: on
              ? Obsidian.green.withValues(alpha: 0.14)
              : Colors.white.withValues(alpha: 0.04),
          borderRadius: BorderRadius.circular(Obsidian.rSm + 4),
          border: Border.all(
              color: on ? Obsidian.green.withValues(alpha: 0.6) : Colors.transparent),
        ),
        child: Text(label,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: Obsidian.dataTable(
                size: 12.5, color: on ? Obsidian.green : Obsidian.onSurface)),
      ),
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
      List<(String, VoidCallback)> trailing = const [],
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
          for (final (label, onTap) in trailing)
            GestureDetector(
              behavior: HitTestBehavior.opaque,
              onTap: onTap,
              child: Padding(
                padding: const EdgeInsets.only(left: 10),
                child: Container(
                  padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 5),
                  decoration: BoxDecoration(
                    borderRadius: BorderRadius.circular(6),
                    border: Border.all(
                        color: Obsidian.primary.withValues(alpha: 0.35)),
                  ),
                  child: Text(label,
                      style: Obsidian.labelSm(
                          color: Obsidian.primary, size: 10.5)),
                ),
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
            onChanged: (_) => setState(() {
              if (identical(c, _tp)) _tpTouched = true;
              if (identical(c, _sl)) _slTouched = true;
            }),
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
                // on 1d the take profit is left empty ON PURPOSE: a daily
                // entry is closed by its trailing stop, not a target
                ? (identical(c, _tp) && widget.interval == '1d'
                    ? 'none on 1d: the stop trails'
                    : 'optional')
                : '${pct >= 0 ? '+' : ''}${pct.toStringAsFixed(2)}% $word',
            style: Obsidian.body(
                color: pct == null ? Obsidian.outline : tone, size: 11)),
      ],
    );
  }
}
