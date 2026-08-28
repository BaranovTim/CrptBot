/// The price line on the dashboard card.
///
/// Hand-painted rather than pulled from a charting package: the design wants
/// one specific thing — a smooth glowing stroke over a dotted grid with a
/// gradient falloff beneath — and every chart library would need more
/// configuration to suppress its own axes, legends and tooltips than this
/// takes to draw.
///
/// The curve is Catmull-Rom converted to cubic Béziers. A polyline through
/// hourly closes looks jagged and cheap; naive quadratic smoothing overshoots
/// and can draw a low below the actual low, which on a price chart is a lie.
/// Catmull-Rom passes exactly through every real point.
library;

import 'package:flutter/material.dart';

import '../theme/liquid_obsidian.dart';

class Sparkline extends StatelessWidget {
  const Sparkline({
    super.key,
    required this.values,
    this.color = Obsidian.green,
    this.height = 180,
  });

  final List<double> values;
  final Color color;
  final double height;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      height: height,
      width: double.infinity,
      child: CustomPaint(painter: _SparkPainter(values, color)),
    );
  }
}

class _SparkPainter extends CustomPainter {
  _SparkPainter(this.values, this.color);

  final List<double> values;
  final Color color;

  @override
  void paint(Canvas canvas, Size size) {
    _grid(canvas, size);
    if (values.length < 2) return;

    var lo = values.reduce((a, b) => a < b ? a : b);
    var hi = values.reduce((a, b) => a > b ? a : b);
    if (hi - lo < 1e-9) {
      // a dead-flat series would divide by zero; centre it instead
      hi += 1;
      lo -= 1;
    }
    const padY = 14.0;
    final h = size.height - padY * 2;
    final dx = size.width / (values.length - 1);

    final pts = <Offset>[
      for (var i = 0; i < values.length; i++)
        Offset(i * dx, padY + h - ((values[i] - lo) / (hi - lo)) * h),
    ];

    final path = _catmullRom(pts);

    // fill beneath, fading out — the design's soft green wash
    final fill = Path.from(path)
      ..lineTo(size.width, size.height)
      ..lineTo(0, size.height)
      ..close();
    canvas.drawPath(
      fill,
      Paint()
        ..shader = LinearGradient(
          begin: Alignment.topCenter,
          end: Alignment.bottomCenter,
          colors: [color.withValues(alpha: 0.22), color.withValues(alpha: 0.0)],
        ).createShader(Offset.zero & size),
    );

    // the glow is the same path drawn wide and blurred underneath the stroke
    canvas.drawPath(
      path,
      Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = 8
        ..strokeCap = StrokeCap.round
        ..color = color.withValues(alpha: 0.35)
        ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 10),
    );
    canvas.drawPath(
      path,
      Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = 3
        ..strokeCap = StrokeCap.round
        ..strokeJoin = StrokeJoin.round
        ..color = color,
    );

    // the live end, as an LED
    final last = pts.last;
    canvas.drawCircle(last, 4, Paint()..color = color);
    canvas.drawCircle(
      last,
      9,
      Paint()
        ..color = color.withValues(alpha: 0.35)
        ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 8),
    );
  }

  void _grid(Canvas canvas, Size size) {
    final dot = Paint()..color = Colors.white.withValues(alpha: 0.06);
    const stepX = 34.0, stepY = 30.0;
    for (var x = 6.0; x < size.width; x += stepX) {
      for (var y = 10.0; y < size.height - 6; y += stepY) {
        canvas.drawCircle(Offset(x, y), 1, dot);
      }
    }
  }

  /// Catmull-Rom through every point, emitted as cubic segments.
  Path _catmullRom(List<Offset> p) {
    final path = Path()..moveTo(p.first.dx, p.first.dy);
    for (var i = 0; i < p.length - 1; i++) {
      final p0 = i == 0 ? p[0] : p[i - 1];
      final p1 = p[i];
      final p2 = p[i + 1];
      final p3 = (i + 2 < p.length) ? p[i + 2] : p2;
      path.cubicTo(
        p1.dx + (p2.dx - p0.dx) / 6,
        p1.dy + (p2.dy - p0.dy) / 6,
        p2.dx - (p3.dx - p1.dx) / 6,
        p2.dy - (p3.dy - p1.dy) / 6,
        p2.dx,
        p2.dy,
      );
    }
    return path;
  }

  @override
  bool shouldRepaint(covariant _SparkPainter old) =>
      old.values != values || old.color != color;
}
