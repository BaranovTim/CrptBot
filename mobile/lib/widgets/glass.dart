/// Level 1 and Level 2 refractive panels.
///
/// The spec describes depth as layering, not shadow: a background blur, a
/// white tint, and a 1px stroke. It also asks for "a subtle top-left light
/// sweep on the border to simulate a light source hitting the glass edge",
/// which Flutter cannot express as a BoxBorder — borders take a single
/// colour. So the stroke is a 1px gradient-filled container behind the fill,
/// which is the standard way to fake a gradient border and happens to be
/// exactly the effect asked for.
///
/// WHY THERE IS NO BackdropFilter HERE
/// -----------------------------------
/// There was, and it cost an afternoon. A BackdropFilter inside these
/// scrollables paints NOTHING on Impeller — no error, no warning, no red
/// box. Panels simply became invisible while their non-glass siblings (a
/// heading, a button) rendered normally, which is a confusing symptom to
/// read. Turning off ListView's repaint boundaries did not help either.
///
/// The honest observation is that the blur was never doing much work: Level 0
/// is a near-uniform obsidian floor carrying two 5-10% radial gradients, so
/// blurring it by 30px produces almost exactly the flat translucent fill you
/// get by not blurring at all. The tint and the stroke are what actually read
/// as glass. So the fill is opaque enough to stand on its own, and the filter
/// is gone.
///
/// `FrostedNav` keeps its real BackdropFilter: it sits outside the scroll
/// view, it works there, and it is the one surface with genuinely varied
/// content passing underneath it.
library;

import 'package:flutter/material.dart';

import '../theme/liquid_obsidian.dart';

class GlassPanel extends StatelessWidget {
  const GlassPanel({
    super.key,
    required this.child,
    this.padding = const EdgeInsets.all(Obsidian.gutter),
    this.radius = Obsidian.rLg,
    this.active = false,
    this.glow,
    this.glowOpacity = 0.4,
    this.onTap,
  });

  final Widget child;
  final EdgeInsets padding;
  final double radius;

  /// Level 2: heavier blur, brighter tint, higher-contrast stroke.
  final bool active;

  /// An accent glow. Zero offset, high spread — a light source, not a shadow.
  final Color? glow;
  final double glowOpacity;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final fill = Color.fromRGBO(40, 42, 46, active ? 0.86 : 0.72);
    final strokeTop = Colors.white.withValues(alpha: active ? 0.30 : 0.15);
    final strokeMid = Colors.white.withValues(alpha: active ? 0.10 : 0.05);
    final inner = BorderRadius.circular(radius - 1);

    Widget panel = DecoratedBox(
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(radius),
        gradient: LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [strokeTop, strokeMid, strokeTop.withValues(alpha: 0.6)],
          stops: const [0.0, 0.45, 1.0],
        ),
        boxShadow: glow == null
            ? null
            : Obsidian.glow(glow!, opacity: glowOpacity, blur: 28),
      ),
      child: Padding(
        padding: const EdgeInsets.all(1),
        child: ClipRRect(
          borderRadius: inner,
          child: Container(
            decoration: BoxDecoration(color: fill, borderRadius: inner),
            padding: padding,
            child: child,
          ),
        ),
      ),
    );

    if (onTap != null) {
      panel = Material(
        color: Colors.transparent,
        child: InkWell(
          borderRadius: BorderRadius.circular(radius),
          onTap: onTap,
          child: panel,
        ),
      );
    }
    return panel;
  }
}

/// Input fields are "darker than the card background (obsidian-inset) with a
/// 1px border that glows Electric Blue when focused."
class GlassField extends StatefulWidget {
  const GlassField({
    super.key,
    required this.controller,
    this.hint,
    this.obscure = false,
    this.keyboardType,
    this.trailing,
    this.onChanged,
    this.capitalization = TextCapitalization.none,
  });

  final TextEditingController controller;
  final String? hint;
  final bool obscure;
  final TextInputType? keyboardType;
  final Widget? trailing;
  final ValueChanged<String>? onChanged;

  /// Defaults to NONE.
  ///
  /// This was hardcoded to `characters`, which suited the one screen it was
  /// written for — typing a pair like BTCUSDT — and was wrong for every other
  /// use of a shared widget. On the sign-in form it upper-cased handles and,
  /// worse, passwords: a case-sensitive secret silently retyped for you.
  /// Only the symbol picker asks for caps now.
  final TextCapitalization capitalization;

  @override
  State<GlassField> createState() => _GlassFieldState();
}

class _GlassFieldState extends State<GlassField> {
  final _focus = FocusNode();

  @override
  void initState() {
    super.initState();
    _focus.addListener(() => setState(() {}));
  }

  @override
  void dispose() {
    _focus.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final on = _focus.hasFocus;
    return AnimatedContainer(
      duration: const Duration(milliseconds: 180),
      decoration: BoxDecoration(
        color: Obsidian.surfaceLowest,
        borderRadius: BorderRadius.circular(Obsidian.rMd),
        border: Border.all(
          color: on ? Obsidian.primary : Colors.white.withValues(alpha: 0.08),
        ),
        boxShadow: on ? Obsidian.glow(Obsidian.primary, opacity: 0.25) : null,
      ),
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
      child: Row(
        children: [
          Expanded(
            child: TextField(
              controller: widget.controller,
              focusNode: _focus,
              obscureText: widget.obscure,
              keyboardType: widget.keyboardType,
              onChanged: widget.onChanged,
              textCapitalization: widget.capitalization,
              // A password field must not be autocorrected or offered to the
              // keyboard's suggestion strip, and neither must a handle.
              autocorrect: false,
              enableSuggestions: false,
              style: Obsidian.dataTable(size: 16),
              cursorColor: Obsidian.primary,
              decoration: InputDecoration(
                border: InputBorder.none,
                isDense: true,
                contentPadding: const EdgeInsets.symmetric(vertical: 16),
                hintText: widget.hint,
                hintStyle: Obsidian.dataTable(
                    size: 16, color: Obsidian.outline),
              ),
            ),
          ),
          if (widget.trailing != null) widget.trailing!,
        ],
      ),
    );
  }
}
