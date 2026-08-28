/// "Glowing Status Indicators: small circular badges. For Bot Active, use
/// Success Green with a 10px outer glow. For Bot Paused, a dim neutral gray."
///
/// Perfectly circular, to simulate LED hardware.
library;

import 'package:flutter/material.dart';

import '../theme/liquid_obsidian.dart';

class StatusDot extends StatelessWidget {
  const StatusDot({super.key, required this.live, this.size = 12, this.color});

  final bool live;
  final double size;
  final Color? color;

  @override
  Widget build(BuildContext context) {
    final c = live ? (color ?? Obsidian.green) : Obsidian.outline;
    return Container(
      width: size,
      height: size,
      decoration: BoxDecoration(
        color: c,
        shape: BoxShape.circle,
        boxShadow: live ? Obsidian.glow(c, opacity: 0.75, blur: 10) : null,
      ),
    );
  }
}
