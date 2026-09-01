/// Liquid Obsidian — the design system from `app_idea/liquid_obsidian/DESIGN.md`.
///
/// Every value here is transcribed from that file rather than eyeballed from
/// the screenshots. The screenshots are renders of these tokens, so when the
/// two disagree the tokens win.
///
/// Depth is not shadow. The spec is explicit: panels are refractive layers —
/// a background blur, a white tint, and a 1px stroke — over a dark void with
/// vibrant mesh gradients bled into the corners. `GlassPanel` implements that
/// literally, which is why there is no elevation/shadow scale in this file.
library;

import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';

class Obsidian {
  Obsidian._();

  // -- colours, straight from the token block ------------------------------
  static const surface = Color(0xFF111317);
  static const surfaceDim = Color(0xFF111317);
  static const surfaceBright = Color(0xFF37393E);
  static const surfaceLowest = Color(0xFF0C0E12);
  static const surfaceLow = Color(0xFF1A1C20);
  static const surfaceContainer = Color(0xFF1E2024);
  static const surfaceHigh = Color(0xFF282A2E);
  static const surfaceHighest = Color(0xFF333539);

  static const onSurface = Color(0xFFE2E2E8);
  static const onSurfaceVariant = Color(0xFFC1C6D7);
  static const outline = Color(0xFF8B90A0);
  static const outlineVariant = Color(0xFF414755);

  static const primary = Color(0xFFADC6FF);
  static const primaryContainer = Color(0xFF4B8EFF);
  static const onPrimary = Color(0xFF002E69);

  /// Success green. Reserved strictly for buy signals and positive trends.
  static const green = Color(0xFF00FFAB);
  static const greenDim = Color(0xFF00E297);
  static const onGreen = Color(0xFF003822);

  /// Caution amber. Reserved for "we cannot tell", never for "bad".
  ///
  /// Green and red are both taken by directional meaning — buy and sell — so
  /// neither can carry "unjudged" without implying a verdict. The screener
  /// needs a third state precisely because a stock nobody could evaluate is
  /// not a stock that failed, and painting it red would say it did.
  static const amber = Color(0xFFFFC46B);

  /// Danger red. Reserved for sell signals, losses and critical alerts.
  static const red = Color(0xFFFF5352);
  static const redSoft = Color(0xFFFFB3AE);
  static const error = Color(0xFFFFB4AB);

  static const background = Color(0xFF111317);

  // -- shape ---------------------------------------------------------------
  static const rSm = 4.0;
  static const r = 8.0;
  static const rMd = 12.0;
  static const rLg = 16.0;
  static const rXl = 24.0;

  // -- spacing. everything is a multiple of 4 ------------------------------
  static const unit = 4.0;
  static const containerPadding = 20.0;
  static const gutter = 16.0;
  static const panelGap = 12.0;

  /// The floating nav is detached from the screen edge by 16px and needs
  /// clearance beneath every scroll view. The spec calls for 80px.
  static const navClearance = 80.0;

  // -- typography ----------------------------------------------------------
  // Inter carries UI. JetBrains Mono carries every number, address and hash —
  // it distinguishes 0 from O, which matters when the number is a price.
  static TextStyle displayLg({Color? color}) => GoogleFonts.inter(
        fontSize: 32, // spec: scale 48 -> 32 on mobile so prices do not wrap
        fontWeight: FontWeight.w700,
        height: 1.1,
        letterSpacing: -0.02 * 32,
        color: color ?? onSurface,
      );

  static TextStyle headlineMd({Color? color}) => GoogleFonts.inter(
        fontSize: 24,
        fontWeight: FontWeight.w600,
        height: 1.3,
        letterSpacing: -0.01 * 24,
        color: color ?? onSurface,
      );

  static TextStyle bodyLg({Color? color}) => GoogleFonts.inter(
        fontSize: 16,
        fontWeight: FontWeight.w400,
        height: 1.5,
        color: color ?? onSurface,
      );

  static TextStyle body({Color? color, double size = 14}) => GoogleFonts.inter(
        fontSize: size,
        fontWeight: FontWeight.w400,
        height: 1.45,
        color: color ?? onSurfaceVariant,
      );

  static TextStyle dataTable({Color? color, double size = 14, FontWeight? w}) =>
      GoogleFonts.jetBrainsMono(
        fontSize: size,
        fontWeight: w ?? FontWeight.w500,
        height: 1.2,
        color: color ?? onSurface,
      );

  static TextStyle labelSm({Color? color, double size = 11}) =>
      GoogleFonts.jetBrainsMono(
        fontSize: size,
        fontWeight: FontWeight.w700,
        height: 1,
        letterSpacing: 0.05 * size,
        color: color ?? onSurfaceVariant,
      );

  // -- glow. drop shadows with zero offset and high spread -----------------
  static List<BoxShadow> glow(Color c, {double opacity = 0.4, double blur = 20}) =>
      [BoxShadow(color: c.withValues(alpha: opacity), blurRadius: blur, spreadRadius: 2)];

  /// Maps the API's `tone` string onto the palette. The API decides the tone;
  /// the app never infers bullishness from a number itself, because sign
  /// conventions differ per feature and guessing is how a screen ends up
  /// painting bad news green.
  static Color tone(String? t) => switch (t) {
        'up' => green,
        'down' => red,
        'warn' => Color(0xFFFFD479),
        _ => onSurfaceVariant,
      };

  static ThemeData theme() {
    final base = ThemeData.dark(useMaterial3: true);
    return base.copyWith(
      scaffoldBackgroundColor: background,
      colorScheme: base.colorScheme.copyWith(
        surface: surface,
        primary: primary,
        onPrimary: onPrimary,
        secondary: green,
        error: error,
      ),
      textTheme: base.textTheme.apply(
        bodyColor: onSurface,
        displayColor: onSurface,
      ),
    );
  }
}
