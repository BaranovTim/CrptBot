/// Level 0, the floor: deep obsidian with vibrant mesh gradients bled into
/// the corners at low opacity. Transcribed from the `.mesh-bg` rule.
library;

import 'package:flutter/material.dart';

import '../theme/liquid_obsidian.dart';

class MeshBackground extends StatelessWidget {
  const MeshBackground({super.key, required this.child});

  final Widget child;

  @override
  Widget build(BuildContext context) {
    return Container(
      color: Obsidian.background,
      child: Stack(
        children: [
          // radial-gradient(circle at 10% 20%, rgba(173,198,255,.1), transparent 40%)
          const Positioned.fill(
            child: DecoratedBox(
              decoration: BoxDecoration(
                gradient: RadialGradient(
                  center: Alignment(-0.8, -0.6),
                  radius: 0.9,
                  colors: [Color(0x1AADC6FF), Color(0x00ADC6FF)],
                  stops: [0.0, 1.0],
                ),
              ),
            ),
          ),
          // radial-gradient(circle at 90% 80%, rgba(0,226,151,.05), transparent 40%)
          const Positioned.fill(
            child: DecoratedBox(
              decoration: BoxDecoration(
                gradient: RadialGradient(
                  center: Alignment(0.8, 0.6),
                  radius: 0.9,
                  colors: [Color(0x0D00E297), Color(0x0000E297)],
                  stops: [0.0, 1.0],
                ),
              ),
            ),
          ),
          child,
        ],
      ),
    );
  }
}
