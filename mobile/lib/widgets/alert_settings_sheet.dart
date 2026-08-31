/// Which alerts this coin is allowed to send, at two levels.
///
/// WHY A SHEET AND NOT A TOGGLE
///     There are two things worth controlling — the whole coin, and one
///     timeframe of it — and one tap cannot express both. Tap-for-one and
///     long-press-for-the-other was the alternative, and it is the same
///     invisible gesture that made swipe-to-delete undiscoverable on the
///     market screen. Everything is on screen and everything is one tap.
///
/// WHY THE COIN SWITCH DISABLES THE ROWS
///     Muting the coin wins over anything more specific, so leaving the
///     per-timeframe switches live underneath would let you turn 1h "on"
///     while it stays silent. A control that does nothing is worse than one
///     that is visibly unavailable.
library;

import 'package:flutter/material.dart';

import '../api/muted.dart';
import '../theme/liquid_obsidian.dart';
import 'glass.dart';

class AlertSettingsSheet extends StatefulWidget {
  const AlertSettingsSheet({super.key, required this.symbol, this.intervals});

  final String symbol;
  final List<String>? intervals;

  @override
  State<AlertSettingsSheet> createState() => _AlertSettingsSheetState();
}

class _AlertSettingsSheetState extends State<AlertSettingsSheet> {
  static const _all = ['1m', '5m', '15m', '1h', '4h', '1d'];
  bool _ready = false;

  @override
  void initState() {
    super.initState();
    Muted.instance.load().then((_) {
      if (mounted) setState(() => _ready = true);
    });
  }

  List<String> get _intervals => widget.intervals ?? _all;

  @override
  Widget build(BuildContext context) {
    final coinMuted = Muted.instance.isMuted(widget.symbol);
    return SafeArea(
      top: false,
      child: Padding(
        padding: const EdgeInsets.all(Obsidian.containerPadding),
        child: GlassPanel(
          active: true,
          padding: const EdgeInsets.fromLTRB(20, 20, 20, 12),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text('ALERTS · ${widget.symbol}',
                  style: Obsidian.labelSm(size: 11)),
              const SizedBox(height: 14),
              if (!_ready)
                const Padding(
                  padding: EdgeInsets.symmetric(vertical: 28),
                  child: Center(
                      child: CircularProgressIndicator(
                          color: Obsidian.primary)),
                )
              else ...[
                _row(
                  title: 'All alerts for ${widget.symbol}',
                  subtitle: coinMuted
                      ? 'Silenced — nothing from this coin'
                      : 'On for every timeframe below',
                  value: !coinMuted,
                  onChanged: (_) async {
                    await Muted.instance.toggle(widget.symbol);
                    if (mounted) setState(() {});
                  },
                ),
                Divider(
                    height: 20,
                    color: Colors.white.withValues(alpha: 0.06)),
                for (final iv in _intervals)
                  _row(
                    title: iv.toUpperCase(),
                    subtitle: null,
                    dense: true,
                    // the coin switch wins, so the rows show the effective
                    // state and cannot be toggled into a lie
                    value: !coinMuted &&
                        !Muted.instance.isIntervalMuted(widget.symbol, iv),
                    onChanged: coinMuted
                        ? null
                        : (_) async {
                            await Muted.instance.toggle(widget.symbol, iv);
                            if (mounted) setState(() {});
                          },
                  ),
                const SizedBox(height: 8),
                Text(
                    coinMuted
                        ? 'Turn the coin back on to choose timeframes.'
                        : 'Alerts that belong to no timeframe — filings and '
                            'scheduled releases — follow the coin switch.',
                    style:
                        Obsidian.body(color: Obsidian.outline, size: 11)),
              ],
              const SizedBox(height: 6),
              Center(
                child: TextButton(
                  onPressed: () => Navigator.of(context).pop(),
                  child: Text('Done',
                      style: Obsidian.body(color: Obsidian.primary)),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _row({
    required String title,
    required String? subtitle,
    required bool value,
    required ValueChanged<bool>? onChanged,
    bool dense = false,
  }) =>
      Padding(
        padding: EdgeInsets.symmetric(vertical: dense ? 2 : 6),
        child: Row(
          children: [
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(title,
                      style: dense
                          ? Obsidian.dataTable(size: 13.5)
                          : Obsidian.bodyLg()
                              .copyWith(fontWeight: FontWeight.w600)),
                  if (subtitle != null) ...[
                    const SizedBox(height: 3),
                    Text(subtitle,
                        style: Obsidian.body(
                            color: Obsidian.outline, size: 11.5)),
                  ],
                ],
              ),
            ),
            Switch(
              value: value,
              onChanged: onChanged,
              activeThumbColor: Obsidian.green,
            ),
          ],
        ),
      );
}
