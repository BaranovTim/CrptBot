/// The timeframe selector.
///
/// Every timeframe is listed, including the ones with no fitted model, and
/// those are dimmed rather than hidden. Hiding them would make the row look
/// complete when it is not — and the whole reason each timeframe needs its
/// own model is that a pattern does not mean the same thing on a 1m chart as
/// on a 1d one, so "there is no model for 5m yet" is information, not clutter.
library;

import 'package:flutter/material.dart';

import '../api/models.dart';
import '../theme/liquid_obsidian.dart';

class TimeframeBar extends StatelessWidget {
  const TimeframeBar({
    super.key,
    required this.timeframes,
    required this.selected,
    required this.onSelect,
  });

  final List<TimeframeInfo> timeframes;
  final String selected;
  final ValueChanged<TimeframeInfo> onSelect;

  @override
  Widget build(BuildContext context) {
    if (timeframes.isEmpty) return const SizedBox.shrink();
    return SizedBox(
      height: 38,
      child: ListView.separated(
        scrollDirection: Axis.horizontal,
        physics: const BouncingScrollPhysics(),
        itemCount: timeframes.length,
        separatorBuilder: (_, _) => const SizedBox(width: 8),
        itemBuilder: (_, i) {
          final tf = timeframes[i];
          final on = tf.interval == selected;
          final colour = on
              ? Obsidian.green
              : (tf.trained ? Obsidian.onSurfaceVariant : Obsidian.outline);
          return InkWell(
            onTap: () => onSelect(tf),
            borderRadius: BorderRadius.circular(Obsidian.rMd),
            child: AnimatedContainer(
              duration: const Duration(milliseconds: 150),
              padding: const EdgeInsets.symmetric(horizontal: 16),
              alignment: Alignment.center,
              decoration: BoxDecoration(
                color: on
                    ? Obsidian.green.withValues(alpha: 0.14)
                    : Obsidian.surfaceHigh.withValues(alpha: 0.45),
                borderRadius: BorderRadius.circular(Obsidian.rMd),
                border: Border.all(
                    color: on
                        ? Obsidian.green.withValues(alpha: 0.55)
                        : Colors.white.withValues(alpha: 0.08)),
                boxShadow:
                    on ? Obsidian.glow(Obsidian.green, opacity: 0.22, blur: 14) : null,
              ),
              child: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(tf.label,
                      style: Obsidian.labelSm(color: colour, size: 12)),
                  if (!tf.trained) ...[
                    const SizedBox(width: 6),
                    // a hollow ring, not a filled dot: nothing is fitted here
                    Container(
                      width: 6,
                      height: 6,
                      decoration: BoxDecoration(
                        shape: BoxShape.circle,
                        border: Border.all(color: Obsidian.outline, width: 1),
                      ),
                    ),
                  ],
                ],
              ),
            ),
          );
        },
      ),
    );
  }
}
