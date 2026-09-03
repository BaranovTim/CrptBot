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
  static const _all = ['1m', '5m', '15m', '1h', '4h', '1d'];

  /// (alert kind, label). `spike` deliberately rides with `signal`: both are
  /// the model telling you something moved, and splitting them would give two
  /// switches for one idea.
  /// Only two things interrupt you now.
  ///
  /// Spikes were removed, and news and filings no longer notify on their own
  /// — they ride along as context on an action change. Switches for kinds
  /// that can never fire would be controls that do nothing, which reads as a
  /// broken app rather than a simplified one.
  static const _kinds = [
    ('signal', 'When the call changes'),
    ('calendar', 'Scheduled releases'),
  ];

  /// News is FOUR CHOICES, not a switch, unlike every other kind.
  ///
  /// The others arrive a few times a day and the only sensible question is
  /// whether you want them. News arrives about seventy times a day, so the
  /// same switch would be answered "off" by almost everyone — and off means
  /// missing the one release that mattered. The middle two settings exist so
  /// that "less" is available without "none".
  static const _newsLevels = [
    ('all', 'Everything', 'About 70 headlines a day'),
    ('directional', 'Only BULL or BEAR', 'Skips what the scorer cannot read'),
    ('strong', 'Strong influence only', 'STRONG IMPACT, either direction'),
    ('none', 'None', 'Silent — still all in the News tab'),
  ];

  String _newsLevel = 'all';
  bool _ready = false;

  @override
  void initState() {
    super.initState();
    Muted.instance.load().then((_) {
      if (mounted) setState(() => _ready = true);
    });
    Settings.instance.newsAlerts().then(
        (v) => mounted ? setState(() => _newsLevel = v) : null);
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
                Text('EVERY COIN', style: Obsidian.labelSm(size: 11)),
                const SizedBox(height: 4),
                Text(
                    'Which kinds are allowed to buzz, everywhere. Separated '
                    'because news runs at about seventy items a day — enough '
                    'that silencing it has to be easier than silencing the '
                    'trade signals with it.',
                    style: Obsidian.body(color: Obsidian.outline, size: 11)),
                const SizedBox(height: 6),
                for (final k in _kinds)
                  _row(
                    title: k.$2,
                    subtitle: null,
                    dense: true,
                    value: !Muted.instance.isKindMuted(k.$1),
                    onChanged: (_) async {
                      await Muted.instance.toggleKind(k.$1);
                      if (mounted) setState(() {});
                    },
                  ),
                const SizedBox(height: 14),
                Text('NEWS IN CONTEXT', style: Obsidian.labelSm(size: 11)),
                const SizedBox(height: 6),
                for (final n in _newsLevels) _newsRow(n.$1, n.$2, n.$3),
                const SizedBox(height: 8),
                Text(
                    'Headlines no longer buzz on their own — they are '
                    'attached to a notification when the call changes. This '
                    'chooses which ones are worth attaching.\n\n'
                    'BULL, BEAR and impact come from a keyword scorer that '
                    'stays silent on about half of all headlines, and it is '
                    'not an input to the model — so an attached headline is '
                    'what was happening at the time, never the reason.',
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

  /// A radio row, because these four are exclusive and a column of switches
  /// would let you turn all of them off — a state with no meaning, and no way
  /// back except guessing which one used to be on.
  Widget _newsRow(String value, String title, String subtitle) {
    final on = _newsLevel == value;
    return InkWell(
      onTap: () async {
        setState(() => _newsLevel = value);
        await Settings.instance.saveNewsAlerts(value);
      },
      borderRadius: BorderRadius.circular(Obsidian.rMd),
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 7, horizontal: 2),
        child: Row(
          children: [
            Icon(
                on
                    ? Icons.radio_button_checked_rounded
                    : Icons.radio_button_unchecked_rounded,
                size: 18,
                color: on ? Obsidian.primary : Obsidian.outline),
            const SizedBox(width: 11),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(title,
                      style: Obsidian.body(
                          size: 13.5,
                          color: on ? Obsidian.primary : null)),
                  const SizedBox(height: 2),
                  Text(subtitle,
                      style:
                          Obsidian.body(color: Obsidian.outline, size: 11)),
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
