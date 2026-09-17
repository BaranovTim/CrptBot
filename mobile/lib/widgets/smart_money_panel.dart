// The followed traders, on this coin.
//
// Three things, in the order they matter: how the followed set is positioned
// on this coin right now (the consensus bar), who exactly holds it and how
// big, and what changed recently. And a label that does not go away:
// this is the public book of people with a good record, not a call. The
// server says so in `note`; the panel says so in its own words as well,
// because a row of green LONG chips with no caveat reads as advice.
import 'package:flutter/material.dart';

import '../api/format.dart';
import '../api/models.dart';
import '../theme/liquid_obsidian.dart';
import 'glass.dart';

/// $2.1M, $840k, $12,500 -- the notification's format, so a number reads
/// the same on the lock screen and on the panel behind it.
String usdCompact(double v) {
  final a = v.abs();
  final sign = v < 0 ? '-' : '';
  if (a >= 1e6) return '$sign\$${(a / 1e6).toStringAsFixed(1)}M';
  if (a >= 1e4) return '$sign\$${(a / 1e3).round()}k';
  return '$sign\$${a.round()}';
}

String agoText(DateTime at, {DateTime? now}) {
  final d = (now ?? DateTime.now().toUtc()).difference(at.toUtc());
  if (d.inMinutes < 1) return 'just now';
  if (d.inMinutes < 60) return '${d.inMinutes}m ago';
  if (d.inHours < 48) return '${d.inHours}h ago';
  return '${d.inDays}d ago';
}

class SmartMoneyPanel extends StatelessWidget {
  const SmartMoneyPanel({super.key, required this.data, this.now});

  final SmartMoney data;
  final DateTime? now;

  @override
  Widget build(BuildContext context) {
    final c = data.consensus;
    if (!data.available || data.tracked == 0) return const SizedBox.shrink();
    final events = data.events.take(5).toList();
    final holders = (c?.holders ?? const <SmartHolder>[]).take(5).toList();

    return GlassPanel(
      padding: const EdgeInsets.fromLTRB(18, 16, 18, 14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Text('SMART MONEY', style: Obsidian.labelSm(size: 11)),
              const SizedBox(width: 8),
              Container(
                padding:
                    const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                decoration: BoxDecoration(
                  color: Obsidian.primary.withValues(alpha: 0.15),
                  borderRadius: BorderRadius.circular(10),
                ),
                child: Text('${data.tracked} followed',
                    style: Obsidian.dataTable(
                        size: 10.5,
                        color: Obsidian.primary,
                        w: FontWeight.w700)),
              ),
              const Spacer(),
              if (data.polledAt != null)
                Text(agoText(data.polledAt!, now: now),
                    style: Obsidian.labelSm(color: Obsidian.outline, size: 10)),
            ],
          ),
          const SizedBox(height: 12),
          if (c != null) ...[
            _consensusLine(c),
            const SizedBox(height: 8),
            _consensusBar(c),
            const SizedBox(height: 12),
          ],
          if (holders.isNotEmpty) ...[
            for (final h in holders) _holderRow(h),
            const SizedBox(height: 6),
          ],
          if (c != null && holders.isEmpty)
            Padding(
              padding: const EdgeInsets.only(bottom: 8),
              child: Text('None of the followed hold this coin right now.',
                  style: Obsidian.body(
                      color: Obsidian.onSurfaceVariant, size: 12.5)),
            ),
          if (events.isNotEmpty) ...[
            Text('RECENT', style: Obsidian.labelSm(size: 10)),
            const SizedBox(height: 6),
            for (final e in events) _eventRow(e),
          ],
          const SizedBox(height: 4),
          Text(
            'Public books of the best-recorded traders on Hyperliquid — '
            'a record of what they did, not a call. Being measured.',
            style: Obsidian.body(color: Obsidian.outline, size: 10.5),
          ),
        ],
      ),
    );
  }

  Widget _consensusLine(SmartConsensus c) {
    final parts = <String>[
      '${c.holding} of ${c.tracked} hold it',
      if (c.long > 0) '${c.long} long ${usdCompact(c.longNotional)}',
      if (c.short > 0) '${c.short} short ${usdCompact(c.shortNotional)}',
    ];
    return Text(parts.join(' · '),
        style: Obsidian.dataTable(size: 13, color: Obsidian.onSurface));
  }

  /// Long notional to the right of centre in green, short to the left in
  /// red, scaled to the larger side. Nothing held draws nothing.
  Widget _consensusBar(SmartConsensus c) {
    final total = c.longNotional + c.shortNotional;
    if (total <= 0) return const SizedBox(height: 6);
    final longShare = c.longNotional / total;
    return ClipRRect(
      borderRadius: BorderRadius.circular(3),
      child: SizedBox(
        height: 6,
        child: Row(
          children: [
            Expanded(
              flex: ((1 - longShare) * 1000).round().clamp(0, 1000),
              child: Container(color: Obsidian.red.withValues(alpha: 0.8)),
            ),
            Expanded(
              flex: (longShare * 1000).round().clamp(0, 1000),
              child: Container(color: Obsidian.green.withValues(alpha: 0.8)),
            ),
          ],
        ),
      ),
    );
  }

  Widget _sideChip(bool isLong, String label) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 2),
        decoration: BoxDecoration(
          color: (isLong ? Obsidian.green : Obsidian.red)
              .withValues(alpha: 0.16),
          borderRadius: BorderRadius.circular(8),
        ),
        child: Text(label,
            style: Obsidian.dataTable(
                size: 10.5,
                color: isLong ? Obsidian.green : Obsidian.red,
                w: FontWeight.w700)),
      );

  Widget _holderRow(SmartHolder h) {
    final detail = <String>[
      usdCompact(h.notional),
      if (h.leverage != null) '${h.leverage!.toStringAsFixed(0)}×',
      if (h.entry != null) '@ ${priceText(h.entry, prefix: '')}',
    ].join(' · ');
    final record = h.winRate == null
        ? ''
        : '${(h.winRate! * 100).round()}% win rate';
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Row(
        children: [
          _sideChip(h.isLong, h.side),
          const SizedBox(width: 8),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(h.who,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: Obsidian.dataTable(
                        size: 12.5, color: Obsidian.onSurface)),
                Text(detail,
                    style: Obsidian.body(
                        color: Obsidian.onSurfaceVariant, size: 11)),
              ],
            ),
          ),
          if (record.isNotEmpty)
            Text(record,
                style: Obsidian.labelSm(
                    color: Obsidian.onSurfaceVariant, size: 10.5)),
        ],
      ),
    );
  }

  Widget _eventRow(SmartEvent e) {
    final verb = switch (e.kind) {
      'opened' => 'opened',
      'closed' => 'closed',
      'flipped' => 'flipped to',
      'added' => 'added to',
      'reduced' => 'reduced',
      _ => e.kind,
    };
    return Padding(
      padding: const EdgeInsets.only(bottom: 6),
      child: Row(
        children: [
          _sideChip(e.isLong, e.side),
          const SizedBox(width: 8),
          Expanded(
            child: Text('${e.who} $verb ${e.side.toLowerCase()} · '
                '${usdCompact(e.notional)}',
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: Obsidian.body(color: Obsidian.onSurface, size: 12)),
          ),
          Text(agoText(e.at, now: now),
              style: Obsidian.labelSm(color: Obsidian.outline, size: 10)),
        ],
      ),
    );
  }
}
