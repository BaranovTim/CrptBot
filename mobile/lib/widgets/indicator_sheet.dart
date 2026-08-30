/// The panel a dashboard tile expands into.
///
/// WHY A SHEET AND NOT AN OVERLAY ON THE PRICE CHART
///     Overlaying was the alternative. RSI runs 0-100, volume is a ratio
///     around 1, structure is +1/-1, and price is 78,000. Sharing one axis
///     would flatten the indicator into a straight line; giving it a second
///     axis is the classic way to make two unrelated series look correlated,
///     because the reader's eye compares shapes that have been scaled
///     independently to fill the same box. Separate panel, own axis, stated
///     bounds.
///
/// WHY THE BOUNDS ARE FIXED WHERE THEY EXIST
///     RSI is drawn against 0-100 every time, not against the range it
///     happened to occupy. A sparkline that rescales to its own data makes a
///     flat week look identical to a violent one.
library;

import 'package:flutter/material.dart';

import '../api/client.dart';
import '../api/models.dart';
import '../theme/liquid_obsidian.dart';
import 'glass.dart';

class IndicatorSheet extends StatefulWidget {
  const IndicatorSheet({
    super.key,
    required this.client,
    required this.indicator,
    required this.symbol,
    required this.interval,
  });

  final ApiClient client;
  final Indicator indicator;
  final String symbol;
  final String interval;

  @override
  State<IndicatorSheet> createState() => _IndicatorSheetState();
}

class _IndicatorSheetState extends State<IndicatorSheet> {
  IndicatorSeries? _series;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final s = await widget.client.indicator(
        widget.indicator.key,
        symbol: widget.symbol,
        interval: widget.interval,
      );
      if (mounted) setState(() => _series = s);
    } catch (e) {
      if (mounted) setState(() => _error = '$e');
    }
  }

  @override
  Widget build(BuildContext context) {
    final c = Obsidian.tone(widget.indicator.tone);
    return SafeArea(
      top: false,
      child: Padding(
        padding: const EdgeInsets.all(Obsidian.containerPadding),
        child: GlassPanel(
          active: true,
          padding: const EdgeInsets.all(22),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Expanded(
                    child: Text(widget.indicator.label,
                        style: Obsidian.labelSm(color: c, size: 11)),
                  ),
                  Text('${widget.symbol} · ${widget.interval}',
                      style: Obsidian.labelSm(size: 10)),
                ],
              ),
              const SizedBox(height: 10),
              Text(widget.indicator.value,
                  style: Obsidian.displayLg().copyWith(fontSize: 34)),
              const SizedBox(height: 4),
              Text(widget.indicator.note, style: Obsidian.body(size: 12.5)),
              const SizedBox(height: 20),
              SizedBox(height: 132, child: _chart(c)),
              const SizedBox(height: 18),
              if (_series != null && _series!.explain.isNotEmpty)
                Text(_series!.explain,
                    style: Obsidian.body(color: Obsidian.outline, size: 12)),
              const SizedBox(height: 16),
              Center(
                child: TextButton(
                  onPressed: () => Navigator.of(context).pop(),
                  child: Text('Close',
                      style: Obsidian.body(color: Obsidian.primary)),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _chart(Color c) {
    if (_error != null) {
      return Center(
        child: Text(_error!,
            textAlign: TextAlign.center,
            style: Obsidian.body(color: Obsidian.redSoft, size: 12)),
      );
    }
    final s = _series;
    if (s == null) {
      return const Center(
          child: CircularProgressIndicator(color: Obsidian.primary));
    }
    if (s.isEmpty) {
      return Center(
        child: Text(
            s.note.isNotEmpty
                ? s.note
                : 'No history for this indicator on this timeframe.',
            style: Obsidian.body(color: Obsidian.outline, size: 12)),
      );
    }
    return Column(
      children: [
        Expanded(
          child: CustomPaint(
            size: Size.infinite,
            painter: _SeriesPainter(s, c),
          ),
        ),
        const SizedBox(height: 6),
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            Text(_fmt(s.min), style: Obsidian.labelSm(size: 9)),
            Text('${s.values.length} × ${widget.interval}',
                style: Obsidian.labelSm(size: 9)),
            Text(_fmt(s.max), style: Obsidian.labelSm(size: 9)),
          ],
        ),
      ],
    );
  }

  static String _fmt(double? v) {
    if (v == null) return '—';
    if (v.abs() >= 100) return v.toStringAsFixed(0);
    if (v.abs() >= 1) return v.toStringAsFixed(1);
    return v.toStringAsFixed(2);
  }
}

class _SeriesPainter extends CustomPainter {
  _SeriesPainter(this.series, this.color);

  final IndicatorSeries series;
  final Color color;

  @override
  void paint(Canvas canvas, Size size) {
    final vals = series.values;
    final finite = vals.whereType<double>().toList();
    if (finite.length < 2) return;

    var lo = series.min ?? finite.reduce((a, b) => a < b ? a : b);
    var hi = series.max ?? finite.reduce((a, b) => a > b ? a : b);
    if (hi - lo < 1e-9) {
      // a perfectly flat series must render as a flat line in the middle,
      // not divide by zero or fill the panel
      lo -= 0.5;
      hi += 0.5;
    }

    double y(double v) =>
        size.height - ((v - lo) / (hi - lo)).clamp(0.0, 1.0) * size.height;
    double x(int i) => size.width * (i / (vals.length - 1));

    // baseline: the midpoint for a bounded indicator, which is the line that
    // actually means something (50 on RSI, 0 on structure, 1x on volume)
    final mid = series.min != null && series.max != null
        ? (series.min! + series.max!) / 2
        : null;
    if (mid != null) {
      canvas.drawLine(
        Offset(0, y(mid)),
        Offset(size.width, y(mid)),
        Paint()
          ..color = Colors.white.withValues(alpha: 0.10)
          ..strokeWidth = 1,
      );
    }

    // Nulls break the line rather than being interpolated across. A gap in
    // an indicator is information; joining it would draw data that does not
    // exist.
    final stroke = Paint()
      ..color = color
      ..strokeWidth = 2
      ..style = PaintingStyle.stroke
      ..strokeJoin = StrokeJoin.round;

    Path? path;
    for (var i = 0; i < vals.length; i++) {
      final v = vals[i];
      if (v == null) {
        if (path != null) canvas.drawPath(path, stroke);
        path = null;
        continue;
      }
      final p = Offset(x(i), y(v));
      if (path == null) {
        path = Path()..moveTo(p.dx, p.dy);
      } else {
        path.lineTo(p.dx, p.dy);
      }
    }
    if (path != null) canvas.drawPath(path, stroke);

    // where it is now
    final lastIdx = vals.lastIndexWhere((v) => v != null);
    if (lastIdx >= 0) {
      canvas.drawCircle(Offset(x(lastIdx), y(vals[lastIdx]!)), 3.5,
          Paint()..color = color);
    }
  }

  @override
  bool shouldRepaint(covariant _SeriesPainter old) =>
      old.series != series || old.color != color;
}
