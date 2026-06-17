// lib/theme/hive_theme.dart
import 'package:flutter/material.dart';

import 'hive_palette.dart';
import 'hive_tokens.dart';

/// Material 3 theme seeded from gold-2, with a `HiveTokens` extension
/// carrying the bespoke palette + motion tokens that Material doesn't model.
///
/// [marbleScreen] is the asset path for the currently-selected marble
/// background variant, surfaced through `HiveTokens.marbleScreen`. Pass
/// `null` (the default) before assets are wired up — widgets fall back to
/// the radial-depth gradient background.
ThemeData buildHiveTheme({String? marbleScreen, String? marbleCell}) {
  final scheme = ColorScheme.fromSeed(
    seedColor: HivePalette.amber1,
    brightness: Brightness.dark,
  ).copyWith(
    surface: HivePalette.slate1,
    onSurface: HivePalette.ink,
    outline: HivePalette.amber1.withValues(alpha: 0.25),
  );

  const baseTokens = HiveTokens.standard();
  final tokens = baseTokens.copyWith(
    marbleScreen: marbleScreen,
    marbleCell: marbleCell,
  );

  // Warm-black base matching DESIGN.md `bg` oklch(0.14 0.014 55) approximation.
  const warmBlack = Color(0xFF0F0E0B);

  // Heavy display/title text style used in app-bars and section titles.
  const displayStyle = TextStyle(
    fontWeight: FontWeight.w800,
    letterSpacing: -0.01 * 22,
    color: HivePalette.ink,
  );

  return ThemeData(
    useMaterial3: true,
    colorScheme: scheme,
    // Transparent so HiveRadialBackground shows through
    scaffoldBackgroundColor: warmBlack,
    appBarTheme: AppBarTheme(
      backgroundColor: Colors.transparent,
      foregroundColor: HivePalette.ink,
      elevation: 0,
      scrolledUnderElevation: 0,
      titleTextStyle: displayStyle.copyWith(fontSize: 21),
    ),
    textTheme: const TextTheme(
      // Display sizes — w800
      displayLarge: TextStyle(
        fontSize: 22,
        fontWeight: FontWeight.w800,
        letterSpacing: -0.22,
        color: HivePalette.ink,
      ),
      displayMedium: TextStyle(
        fontSize: 21,
        fontWeight: FontWeight.w800,
        letterSpacing: -0.21,
        color: HivePalette.ink,
      ),
      // Title — w700
      titleLarge: TextStyle(
        fontSize: 18,
        fontWeight: FontWeight.w700,
        color: HivePalette.ink,
      ),
      titleMedium: TextStyle(
        fontSize: 15,
        fontWeight: FontWeight.w700,
        color: HivePalette.ink,
      ),
      // Body — w600
      bodyLarge: TextStyle(
        fontSize: 14,
        fontWeight: FontWeight.w600,
        color: HivePalette.ink,
        height: 1.4,
      ),
      bodyMedium: TextStyle(
        fontSize: 13,
        fontWeight: FontWeight.w600,
        color: HivePalette.ink,
        height: 1.35,
      ),
      // Labels / meta — w500
      labelLarge: TextStyle(
        fontSize: 12,
        fontWeight: FontWeight.w500,
        color: HivePalette.inkDim,
        fontFeatures: [FontFeature.tabularFigures()],
      ),
      labelMedium: TextStyle(
        fontSize: 11,
        fontWeight: FontWeight.w500,
        color: HivePalette.inkFaint,
        fontFeatures: [FontFeature.tabularFigures()],
      ),
      labelSmall: TextStyle(
        fontSize: 10,
        fontWeight: FontWeight.w500,
        color: HivePalette.inkFaint,
        fontFeatures: [FontFeature.tabularFigures()],
      ),
    ),
    navigationBarTheme: NavigationBarThemeData(
      // Warm-tinted near-black bar
      backgroundColor: const Color(0xFF100F0C),
      indicatorColor: HivePalette.amber2.withValues(alpha: 0.35),
      labelBehavior: NavigationDestinationLabelBehavior.onlyShowSelected,
      labelTextStyle: WidgetStateProperty.resolveWith((states) {
        if (states.contains(WidgetState.selected)) {
          return const TextStyle(
            fontSize: 10,
            fontWeight: FontWeight.w600,
            color: HivePalette.amber1,
          );
        }
        return const TextStyle(
          fontSize: 10,
          fontWeight: FontWeight.w500,
          color: HivePalette.inkFaint,
        );
      }),
      iconTheme: WidgetStateProperty.resolveWith((states) {
        if (states.contains(WidgetState.selected)) {
          return const IconThemeData(color: HivePalette.amber1, size: 24);
        }
        return const IconThemeData(color: HivePalette.inkFaint, size: 24);
      }),
    ),
    navigationRailTheme: NavigationRailThemeData(
      backgroundColor: const Color(0xFF100F0C),
      indicatorColor: HivePalette.amber2.withValues(alpha: 0.30),
      selectedIconTheme:
          const IconThemeData(color: HivePalette.amber1, size: 22),
      unselectedIconTheme:
          const IconThemeData(color: HivePalette.inkFaint, size: 22),
      selectedLabelTextStyle: const TextStyle(
        fontSize: 10,
        fontWeight: FontWeight.w600,
        color: HivePalette.amber1,
      ),
      unselectedLabelTextStyle: const TextStyle(
        fontSize: 10,
        fontWeight: FontWeight.w500,
        color: HivePalette.inkFaint,
      ),
    ),
    cardTheme: CardThemeData(
      color: HivePalette.slate2.withValues(alpha: 0.70),
      elevation: 0,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(HiveTokens.rLg),
        side: BorderSide(
          color: HivePalette.slate3.withValues(alpha: 0.6),
        ),
      ),
    ),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: HivePalette.slate1,
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(HiveTokens.rPill),
        borderSide: BorderSide(color: HivePalette.slate3.withValues(alpha: 0.6)),
      ),
      enabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(HiveTokens.rPill),
        borderSide: BorderSide(color: HivePalette.slate3.withValues(alpha: 0.6)),
      ),
      focusedBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(HiveTokens.rPill),
        borderSide: const BorderSide(color: HivePalette.amber2),
      ),
      hintStyle: const TextStyle(color: HivePalette.inkFaint, fontSize: 13),
      isDense: true,
      contentPadding:
          const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
    ),
    dividerColor: HivePalette.slate3.withValues(alpha: 0.5),
    extensions: [tokens],
  );
}

/// Apply the warm radial-depth background to a scaffold body.
/// Wraps [child] in the DESIGN.md gradient so screens don't need to
/// individually manage their background.
Widget hiveScaffoldBackground(Widget child) {
  return DecoratedBox(
    decoration: const BoxDecoration(
      gradient: RadialGradient(
        center: Alignment(0, -1.4),
        radius: 1.2,
        colors: [
          Color(0xFF2A2015), // warm ochre highlight
          Color(0xFF0E0C0A), // near-black warm base
        ],
        stops: [0.0, 1.0],
      ),
    ),
    child: child,
  );
}
