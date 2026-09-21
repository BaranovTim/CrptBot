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
import '../api/notifications.dart';
import '../api/settings.dart';
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
  static const _all = ['4h', '1d'];

  /// (alert kind, label). `spike` deliberately rides with `signal`: both are
  /// the model telling you something moved, and splitting them would give two
  /// switches for one idea.
  /// Only two things interrupt you now.
  ///
  /// Spikes were removed, and news and filings no longer notify on their own
  /// — they ride along as context on an action change. Switches for kinds
  /// that can never fire would be controls that do nothing, which reads as a
  /// broken app rather than a simplified one.
  /// This coin's strength override; null means it follows the general one.
  String? _override;
  String _general = 'strong';
  bool _ready = false;
  bool _granted = true;

  @override
  void initState() {
    super.initState();
    Muted.instance.load().then((_) {
      if (mounted) setState(() => _ready = true);
    });
    Settings.instance.sensitivity().then(
        (v) => mounted ? setState(() => _general = v) : null);
    Settings.instance.sensitivityOverrides().then((m) => mounted
        ? setState(() => _override = m[widget.symbol.toUpperCase()])
        : null);
    _granted = Notifications.instance.granted;
  }

  List<String> get _intervals => widget.intervals ?? _all;

  @override
  Widget build(BuildContext context) {
    final coinMuted = Muted.instance.isMuted(widget.symbol);
    return SafeArea(
      top: false,
      child: Padding(
        padding: const EdgeInsets.all(Obsidian.containerPadding),
        // SCROLLS, and is bounded to most of the screen.
        //
        // The content is a min-size Column, so it grows with the number of
        // timeframes and categories — and a bottom sheet does not grow with
        // it. Left unbounded this overflows on a short phone: the yellow
        // stripes, and the Done button off the bottom edge with no way to
        // reach it.
        child: ConstrainedBox(
          constraints: BoxConstraints(
              maxHeight: MediaQuery.of(context).size.height * 0.86),
          child: SingleChildScrollView(
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
              if (!_granted) ...[
                Container(
                  padding: const EdgeInsets.symmetric(
                      horizontal: 12, vertical: 10),
                  decoration: BoxDecoration(
                    color: Obsidian.amber.withValues(alpha: 0.10),
                    borderRadius: BorderRadius.circular(Obsidian.rMd),
                    border: Border.all(
                        color: Obsidian.amber.withValues(alpha: 0.30)),
                  ),
                  child: Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const Padding(
                        padding: EdgeInsets.only(top: 1),
                        child: Icon(Icons.notifications_off_rounded,
                            size: 16, color: Obsidian.amber),
                      ),
                      const SizedBox(width: 9),
                      Expanded(
                        child: Text(
                            'Notifications are off for Vanth in your phone\'s '
                            'settings. Nothing below will reach you until '
                            'they are turned on there.',
                            style: Obsidian.body(
                                color: Obsidian.amber, size: 12)),
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: 14),
              ],
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
                Divider(
                    height: 26, color: Colors.white.withValues(alpha: 0.06)),
                Text('SIGNAL STRENGTH · THIS COIN ONLY',
                    style: Obsidian.labelSm(size: 11)),
                const SizedBox(height: 4),
                Text(
                    'The general setting in Profile \u2192 Preferences applies '
                    'to every coin. Pick a level here and ${widget.symbol} '
                    'uses it instead \u2014 nothing else changes.',
                    style:
                        Obsidian.body(color: Obsidian.outline, size: 11)),
                const SizedBox(height: 6),
                _levelRow(null, 'Use the general setting',
                    'Currently ${_levelName(_general)}'),
                _levelRow('strong', 'Strong only',
                    'Only the highest-conviction calls'),
                _levelRow('medium', 'Strong and medium', ''),
                _levelRow('small', 'Everything', 'Every call, however small'),
                const SizedBox(height: 10),
                Text(
                    'Which kinds of alert buzz, and how much news, are '
                    'general settings \u2014 Profile \u2192 Preferences \u2192 '
                    'Notifications.',
                    style: Obsidian.body(color: Obsidian.outline, size: 11)),
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
        ),
      ),
    );
  }

  static String _levelName(String v) => switch (v) {
        'medium' => 'strong and medium',
        'small' => 'everything',
        _ => 'strong only',
      };

  Widget _levelRow(String? value, String title, String subtitle) {
    final on = _override == value;
    return InkWell(
      onTap: () async {
        setState(() => _override = value);
        await Settings.instance.saveSensitivityOverride(widget.symbol, value);
      },
      borderRadius: BorderRadius.circular(Obsidian.rMd),
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 7, horizontal: 2),
        child: Row(
          children: [
            Icon(
                on
                    ? Icons.radio_button_checked_rounded
                    : Icons.radio_button_off_rounded,
                size: 18,
                color: on ? Obsidian.primary : Obsidian.outline),
            const SizedBox(width: 10),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(title, style: Obsidian.body(size: 13)),
                  if (subtitle.isNotEmpty)
                    Text(subtitle,
                        style: Obsidian.body(
                            color: Obsidian.outline, size: 11)),
                ],
              ),
            ),
          ],
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
