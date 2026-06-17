// lib/theme/hive_palette.dart
import 'package:flutter/material.dart';

/// Raw color tokens for the Hive theme. Sourced from the approved mockup
/// direction in `.superpowers/brainstorm/redesign-2026-05-06/content/mockup-clean.html`.
/// Palette: dark slate + amber-dominant + cyan reserved for system data +
/// red/green semantic + ink ladder.
///
/// Don't read these directly from widgets — go through `HiveTokens` (the
/// ThemeExtension) so dark/light variants and overrides flow through one
/// source of truth.
class HivePalette {
  HivePalette._();

  // === slate ladder ===
  static const slate0 = Color(0xFF0E0F11);
  static const slate1 = Color(0xFF15171A);
  static const slate2 = Color(0xFF1D2024);
  static const slate3 = Color(0xFF292D32);
  static const slate4 = Color(0xFF3D4248);

  // === amber ladder ===
  static const amberGlow = Color(0xFFFFB94D);
  static const amber1 = Color(0xFFE0A445);
  static const amber2 = Color(0xFFB8862F);
  static const amber3 = Color(0xFF7E5C1F);

  // === cyan (reserved for system data) ===
  static const cyan = Color(0xFF54B6D6);
  static const cyanDim = Color(0xFF3A7E97);

  // === semantic ===
  static const red = Color(0xFFD24545);
  static const green = Color(0xFF52C385);

  // === ink ladder ===
  static const ink = Color(0xFFF2EFEA);
  static const inkDim = Color(0xFFB1ACA2);
  static const inkFaint = Color(0xFF7E7B73);

  // Dark text color used on amber-filled surfaces (HexHead active/working).
  // Distinct from slate0 (bluish) — this is a chocolate-black for amber contrast.
  static const inkOnAmber = Color(0xFF1A0E00);
}
