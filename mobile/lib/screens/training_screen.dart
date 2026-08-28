/// The training screen.
///
/// The mockup says "Initiate deep learning sequence" over a start button.
/// There is no such button here, and that is a deliberate substitution:
/// fitting a model in this project means years of history, purged K-fold with
/// an embargo, uniqueness weighting and a champion/challenger gate. It is a
/// batch job measured in minutes that must be inspected before it is trusted,
/// not something to kick off from a phone and walk away from.
///
/// So the screen shows what a fitted model IS, and hands over the exact
/// command. The visual language is the mockup's.
library;

import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../api/client.dart';
import '../api/models.dart';
import '../theme/liquid_obsidian.dart';
import '../widgets/glass.dart';

class TrainingScreen extends StatefulWidget {
  const TrainingScreen({super.key, required this.client, required this.symbol});

  final ApiClient client;
  final String symbol;

  @override
  State<TrainingScreen> createState() => _TrainingScreenState();
}

class _TrainingScreenState extends State<TrainingScreen> {
  TrainingInfo? _info;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void didUpdateWidget(covariant TrainingScreen old) {
    super.didUpdateWidget(old);
    if (old.symbol != widget.symbol) _load();
  }

  Future<void> _load() async {
    try {
      final i = await widget.client.training(widget.symbol);
      if (!mounted) return;
      setState(() {
        _info = i;
        _error = null;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _error = e.toString());
    }
  }

  @override
  Widget build(BuildContext context) {
    final i = _info;
    if (_error != null) {
      return Center(
          child: Text(_error!, style: Obsidian.body(color: Obsidian.error)));
    }
    if (i == null) {
      return const Center(
          child: CircularProgressIndicator(color: Obsidian.primary));
    }

    return ListView(
      // addRepaintBoundaries: a BackdropFilter samples what is painted
      // BEHIND it, and ListView puts every child in its own RepaintBoundary
      // by default. Inside that layer the backdrop is empty, so the glass
      // panels blur nothing and paint nothing — the screen comes up blank
      // with no error anywhere. Opting out gives the filter a real backdrop.
      addRepaintBoundaries: false,
      padding: const EdgeInsets.fromLTRB(Obsidian.containerPadding, 8,
          Obsidian.containerPadding, Obsidian.navClearance + 24),
      children: [
        GlassPanel(
          radius: Obsidian.rXl,
          padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 34),
          child: Column(
            children: [
              SizedBox(
                width: 160,
                height: 160,
                child: CustomPaint(
                  painter: _DashedRing(
                      color: i.trained ? Obsidian.green : Obsidian.outline),
                  child: Center(
                    child: Icon(
                      i.trained
                          ? Icons.check_rounded
                          : Icons.model_training_rounded,
                      size: 46,
                      color: i.trained ? Obsidian.green : Obsidian.outline,
                    ),
                  ),
                ),
              ),
              const SizedBox(height: 28),
              Text(i.title,
                  textAlign: TextAlign.center, style: Obsidian.displayLg()),
              const SizedBox(height: 14),
              Text(i.detail,
                  textAlign: TextAlign.center, style: Obsidian.body(size: 15)),
            ],
          ),
        ),
        const SizedBox(height: Obsidian.gutter),
        if (i.trained) _horizons(i) else _command(i),
      ],
    );
  }

  Widget _horizons(TrainingInfo i) => GlassPanel(
        padding: EdgeInsets.zero,
        child: Column(
          children: [
            for (var k = 0; k < i.horizons.length; k++) ...[
              if (k > 0)
                Divider(
                    height: 1,
                    thickness: 1,
                    color: Colors.white.withValues(alpha: 0.05)),
              Padding(
                padding:
                    const EdgeInsets.symmetric(horizontal: 18, vertical: 16),
                child: Row(
                  children: [
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text('${i.horizons[k]['name']} — '
                              '${i.horizons[k]['bars']}-bar horizon',
                              style: Obsidian.bodyLg()),
                          const SizedBox(height: 4),
                          Text(
                              'barriers +${i.horizons[k]['k_up']} / '
                              '−${i.horizons[k]['k_dn']} ATR',
                              style: Obsidian.body(size: 12.5)),
                        ],
                      ),
                    ),
                    Text('${i.horizons[k]['features']} feats',
                        style: Obsidian.dataTable(
                            color: Obsidian.primary, size: 13)),
                  ],
                ),
              ),
            ],
          ],
        ),
      );

  Widget _command(TrainingInfo i) => GlassPanel(
        padding: const EdgeInsets.all(18),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Text('RUN ON YOUR MAC', style: Obsidian.labelSm(size: 11)),
                const Spacer(),
                InkWell(
                  onTap: () {
                    Clipboard.setData(ClipboardData(text: i.command ?? ''));
                    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
                      backgroundColor: Obsidian.surfaceHigh,
                      content: Text('Command copied', style: Obsidian.body()),
                    ));
                  },
                  child: Row(
                    children: [
                      const Icon(Icons.copy_rounded,
                          size: 14, color: Obsidian.primary),
                      const SizedBox(width: 6),
                      Text('Copy',
                          style: Obsidian.labelSm(
                              color: Obsidian.primary, size: 11)),
                    ],
                  ),
                ),
              ],
            ),
            const SizedBox(height: 12),
            Container(
              width: double.infinity,
              padding: const EdgeInsets.all(14),
              decoration: BoxDecoration(
                color: Obsidian.surfaceLowest,
                borderRadius: BorderRadius.circular(Obsidian.rMd),
                border:
                    Border.all(color: Colors.white.withValues(alpha: 0.08)),
              ),
              child: Text(i.command ?? '',
                  style: Obsidian.dataTable(size: 12, color: Obsidian.green)),
            ),
          ],
        ),
      );
}

/// The mockup's dashed ring, drawn as evenly spaced arcs.
class _DashedRing extends CustomPainter {
  _DashedRing({required this.color});

  final Color color;

  @override
  void paint(Canvas canvas, Size size) {
    final rect = Rect.fromCircle(
        center: Offset(size.width / 2, size.height / 2),
        radius: size.width / 2 - 6);
    final paint = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = 3
      ..strokeCap = StrokeCap.round
      ..color = color.withValues(alpha: 0.55);

    const dashes = 26;
    const sweep = (2 * math.pi) / dashes;
    for (var i = 0; i < dashes; i++) {
      canvas.drawArc(rect, i * sweep, sweep * 0.55, false, paint);
    }
  }

  @override
  bool shouldRepaint(covariant _DashedRing old) => old.color != color;
}
