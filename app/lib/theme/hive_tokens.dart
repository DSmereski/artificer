// lib/theme/hive_tokens.dart
import 'package:flutter/material.dart';

import 'hive_palette.dart';

/// Theme tokens for the Hive theme — registered as a Material 3
/// `ThemeExtension` so widgets read them via `Theme.of(context).extension<HiveTokens>()`.
///
/// Carries values that Material 3's `ColorScheme` doesn't model (slate/amber/
/// cyan/red/green/ink ladders, role accents, motion durations, marble background
/// pointers).
@immutable
class HiveTokens extends ThemeExtension<HiveTokens> {
  // === layout grid (4px) ===
  static const double s1 = 4;
  static const double s2 = 8;
  static const double s3 = 12;
  static const double s4 = 16;
  static const double s6 = 24;
  static const double s8 = 32;

  // === radius ===
  static const double rSm = 6;
  static const double rMd = 10;
  static const double rLg = 16;
  static const double rPill = 999;

  // === motion ===
  static const Duration fast = Duration(milliseconds: 120);
  static const Duration base = Duration(milliseconds: 200);
  static const Duration emphasized = Duration(milliseconds: 300);
  static const Curve curveStandard = Curves.easeOutCubic;
  static const Curve curveEmphasized = Curves.easeOutBack;

  const HiveTokens({
    // === slate ladder ===
    required this.slate0,
    required this.slate1,
    required this.slate2,
    required this.slate3,
    required this.slate4,
    // === amber ladder ===
    required this.amber1,
    required this.amber2,
    required this.amber3,
    required this.amberGlow,
    // === cyan ===
    required this.cyan,
    required this.cyanDim,
    // === semantic ===
    required this.red,
    required this.green,
    // === ink ladder ===
    required this.ink,
    required this.inkDim,
    required this.inkFaint,
    // === motion ===
    required this.pulseFast,
    required this.pulseMid,
    required this.pulseSlow,
    required this.flutter,
    required this.flyPath1,
    required this.flyPath2,
    // === marble background pointers ===
    this.marbleScreen,
    this.marbleCell,
  });

  /// Default dark token set sourced from `HivePalette` — new mockup palette.
  const HiveTokens.dark()
      : slate0 = HivePalette.slate0,
        slate1 = HivePalette.slate1,
        slate2 = HivePalette.slate2,
        slate3 = HivePalette.slate3,
        slate4 = HivePalette.slate4,
        amber1 = HivePalette.amber1,
        amber2 = HivePalette.amber2,
        amber3 = HivePalette.amber3,
        amberGlow = HivePalette.amberGlow,
        cyan = HivePalette.cyan,
        cyanDim = HivePalette.cyanDim,
        red = HivePalette.red,
        green = HivePalette.green,
        ink = HivePalette.ink,
        inkDim = HivePalette.inkDim,
        inkFaint = HivePalette.inkFaint,
        pulseFast = const Duration(milliseconds: 900),
        pulseMid = const Duration(milliseconds: 1400),
        pulseSlow = const Duration(milliseconds: 1600),
        flutter = const Duration(milliseconds: 90),
        flyPath1 = const Duration(milliseconds: 5200),
        flyPath2 = const Duration(milliseconds: 4400),
        marbleScreen = null,
        marbleCell = null;

  /// Standard token set — same as dark; kept for call-site compatibility.
  const HiveTokens.standard()
      : slate0 = HivePalette.slate0,
        slate1 = HivePalette.slate1,
        slate2 = HivePalette.slate2,
        slate3 = HivePalette.slate3,
        slate4 = HivePalette.slate4,
        amber1 = HivePalette.amber1,
        amber2 = HivePalette.amber2,
        amber3 = HivePalette.amber3,
        amberGlow = HivePalette.amberGlow,
        cyan = HivePalette.cyan,
        cyanDim = HivePalette.cyanDim,
        red = HivePalette.red,
        green = HivePalette.green,
        ink = HivePalette.ink,
        inkDim = HivePalette.inkDim,
        inkFaint = HivePalette.inkFaint,
        pulseFast = const Duration(milliseconds: 900),
        pulseMid = const Duration(milliseconds: 1400),
        pulseSlow = const Duration(milliseconds: 1600),
        flutter = const Duration(milliseconds: 90),
        flyPath1 = const Duration(milliseconds: 5200),
        flyPath2 = const Duration(milliseconds: 4400),
        marbleScreen = null,
        marbleCell = null;

  // =========================================================================
  // Slate ladder
  // =========================================================================
  final Color slate0;
  final Color slate1;
  final Color slate2;
  final Color slate3;
  final Color slate4;

  // =========================================================================
  // Amber ladder
  // =========================================================================
  final Color amber1;
  final Color amber2;
  final Color amber3;
  final Color amberGlow;

  // =========================================================================
  // Cyan (reserved for system data)
  // =========================================================================
  final Color cyan;
  final Color cyanDim;

  // =========================================================================
  // Semantic
  // =========================================================================
  final Color red;
  final Color green;

  // =========================================================================
  // Ink ladder
  // =========================================================================
  final Color ink;
  final Color inkDim;
  final Color inkFaint;

  // =========================================================================
  // Motion
  // =========================================================================
  final Duration pulseFast;
  final Duration pulseMid;
  final Duration pulseSlow;
  final Duration flutter;
  final Duration flyPath1;
  final Duration flyPath2;

  // Marble background pointers (asset paths). Phase 2 fills these in;
  // Phase 1 leaves them null so widgets fall back to gradients.
  final String? marbleScreen;
  final String? marbleCell;

  @override
  HiveTokens copyWith({
    Color? slate0,
    Color? slate1,
    Color? slate2,
    Color? slate3,
    Color? slate4,
    Color? amber1,
    Color? amber2,
    Color? amber3,
    Color? amberGlow,
    Color? cyan,
    Color? cyanDim,
    Color? red,
    Color? green,
    Color? ink,
    Color? inkDim,
    Color? inkFaint,
    Duration? pulseFast,
    Duration? pulseMid,
    Duration? pulseSlow,
    Duration? flutter,
    Duration? flyPath1,
    Duration? flyPath2,
    String? marbleScreen,
    String? marbleCell,
  }) {
    return HiveTokens(
      slate0: slate0 ?? this.slate0,
      slate1: slate1 ?? this.slate1,
      slate2: slate2 ?? this.slate2,
      slate3: slate3 ?? this.slate3,
      slate4: slate4 ?? this.slate4,
      amber1: amber1 ?? this.amber1,
      amber2: amber2 ?? this.amber2,
      amber3: amber3 ?? this.amber3,
      amberGlow: amberGlow ?? this.amberGlow,
      cyan: cyan ?? this.cyan,
      cyanDim: cyanDim ?? this.cyanDim,
      red: red ?? this.red,
      green: green ?? this.green,
      ink: ink ?? this.ink,
      inkDim: inkDim ?? this.inkDim,
      inkFaint: inkFaint ?? this.inkFaint,
      pulseFast: pulseFast ?? this.pulseFast,
      pulseMid: pulseMid ?? this.pulseMid,
      pulseSlow: pulseSlow ?? this.pulseSlow,
      flutter: flutter ?? this.flutter,
      flyPath1: flyPath1 ?? this.flyPath1,
      flyPath2: flyPath2 ?? this.flyPath2,
      marbleScreen: marbleScreen ?? this.marbleScreen,
      marbleCell: marbleCell ?? this.marbleCell,
    );
  }

  @override
  HiveTokens lerp(ThemeExtension<HiveTokens>? other, double t) {
    if (other is! HiveTokens) return this;
    return HiveTokens(
      slate0: Color.lerp(slate0, other.slate0, t)!,
      slate1: Color.lerp(slate1, other.slate1, t)!,
      slate2: Color.lerp(slate2, other.slate2, t)!,
      slate3: Color.lerp(slate3, other.slate3, t)!,
      slate4: Color.lerp(slate4, other.slate4, t)!,
      amber1: Color.lerp(amber1, other.amber1, t)!,
      amber2: Color.lerp(amber2, other.amber2, t)!,
      amber3: Color.lerp(amber3, other.amber3, t)!,
      amberGlow: Color.lerp(amberGlow, other.amberGlow, t)!,
      cyan: Color.lerp(cyan, other.cyan, t)!,
      cyanDim: Color.lerp(cyanDim, other.cyanDim, t)!,
      red: Color.lerp(red, other.red, t)!,
      green: Color.lerp(green, other.green, t)!,
      ink: Color.lerp(ink, other.ink, t)!,
      inkDim: Color.lerp(inkDim, other.inkDim, t)!,
      inkFaint: Color.lerp(inkFaint, other.inkFaint, t)!,
      // motion (snap at t=0.5)
      pulseFast: t < 0.5 ? pulseFast : other.pulseFast,
      pulseMid: t < 0.5 ? pulseMid : other.pulseMid,
      pulseSlow: t < 0.5 ? pulseSlow : other.pulseSlow,
      flutter: t < 0.5 ? flutter : other.flutter,
      flyPath1: t < 0.5 ? flyPath1 : other.flyPath1,
      flyPath2: t < 0.5 ? flyPath2 : other.flyPath2,
      // marble (snap at t=0.5)
      marbleScreen: t < 0.5 ? marbleScreen : other.marbleScreen,
      marbleCell: t < 0.5 ? marbleCell : other.marbleCell,
    );
  }
}
