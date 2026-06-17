// lib/widgets/hive_motion.dart
//
// Alive-Kinetic motion primitives. EVERY animation is gated on
// MediaQuery.disableAnimations — static equivalents are rendered when
// reduced-motion is requested.

import 'package:flutter/material.dart';

import '../theme/hive_palette.dart';
import '../theme/hive_tokens.dart';

// ─────────────────────────────────────────────────────────────────────────────
// PulseDot
// ─────────────────────────────────────────────────────────────────────────────

/// A small dot that opacity-pulses 0.5→1 and scale-pulses 0.9→1.15 on a
/// ~1.6s loop. When reduced-motion is requested it renders as a solid dot.
class PulseDot extends StatefulWidget {
  const PulseDot({super.key, required this.color, this.size = 8});
  final Color color;
  final double size;

  @override
  State<PulseDot> createState() => _PulseDotState();
}

class _PulseDotState extends State<PulseDot>
    with SingleTickerProviderStateMixin {
  late final AnimationController _c = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 1600),
  )..repeat(reverse: true);

  late final Animation<double> _opacity =
      Tween<double>(begin: 0.5, end: 1.0).animate(
    CurvedAnimation(parent: _c, curve: Curves.easeInOut),
  );
  late final Animation<double> _scale =
      Tween<double>(begin: 0.9, end: 1.15).animate(
    CurvedAnimation(parent: _c, curve: Curves.easeInOut),
  );

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final reduce = MediaQuery.disableAnimationsOf(context);
    final dot = Container(
      width: widget.size,
      height: widget.size,
      decoration: BoxDecoration(color: widget.color, shape: BoxShape.circle),
    );
    if (reduce) return dot;
    return FadeTransition(
      opacity: _opacity,
      child: ScaleTransition(
        scale: _scale,
        child: dot,
      ),
    );
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// LiveGlow
// ─────────────────────────────────────────────────────────────────────────────

/// Wraps [child] in an animated amber box-shadow glow (~2.6s loop) when
/// [active] is true. No glow when inactive or reduced-motion is requested.
class LiveGlow extends StatefulWidget {
  const LiveGlow({super.key, required this.child, required this.active});
  final Widget child;
  final bool active;

  @override
  State<LiveGlow> createState() => _LiveGlowState();
}

class _LiveGlowState extends State<LiveGlow>
    with SingleTickerProviderStateMixin {
  late final AnimationController _c = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 2600),
  );

  @override
  void initState() {
    super.initState();
    _maybePulse();
  }

  @override
  void didUpdateWidget(LiveGlow old) {
    super.didUpdateWidget(old);
    _maybePulse();
  }

  void _maybePulse() {
    if (widget.active) {
      _c.repeat(reverse: true);
    } else {
      _c
        ..stop()
        ..value = 0;
    }
  }

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final reduce = MediaQuery.disableAnimationsOf(context);
    if (!widget.active || reduce) return widget.child;
    return AnimatedBuilder(
      animation: _c,
      builder: (context, child) {
        final t = _c.value;
        return DecoratedBox(
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(HiveTokens.rLg),
            boxShadow: [
              BoxShadow(
                color: HivePalette.amber1.withValues(alpha: 0.35 * t),
                blurRadius: 18 * t,
                spreadRadius: 1 * t,
              ),
            ],
          ),
          child: child,
        );
      },
      child: widget.child,
    );
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// NowBuildingCard
// ─────────────────────────────────────────────────────────────────────────────

/// The SIGNATURE alive element. Shows the currently-building task with:
/// - copper/amber gradient border
/// - glow loop (~2.6s)
/// - scan-sweep highlight (40%-wide amber band translating L→R, ~2.8s)
/// - progress bar (copper→amberGlow)
/// - slug / title / subtitle readout
///
/// Under reduced-motion: gradient border + progress bar remain; sweep + glow
/// are suppressed.
class NowBuildingCard extends StatefulWidget {
  const NowBuildingCard({
    super.key,
    required this.slug,
    required this.title,
    required this.subtitle,
    required this.progress,
  });

  /// Task identifier shown in mono (e.g. "T-0293").
  final String slug;

  /// Task title.
  final String title;

  /// Turn/token readout (e.g. "turn 4 · hive · 18.2k tok").
  final String subtitle;

  /// 0.0–1.0 progress for the bar.
  final double progress;

  @override
  State<NowBuildingCard> createState() => _NowBuildingCardState();
}

class _NowBuildingCardState extends State<NowBuildingCard>
    with TickerProviderStateMixin {
  late final AnimationController _glow = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 2600),
  )..repeat(reverse: true);

  late final AnimationController _sweep = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 2800),
  )..repeat();

  @override
  void dispose() {
    _glow.dispose();
    _sweep.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context).extension<HiveTokens>()!;
    final reduce = MediaQuery.disableAnimationsOf(context);

    // Gradient border via a wrapped container trick:
    // outer = gradient, inner = card background, gap = 1.5px.
    const borderWidth = 1.5;
    const radius = HiveTokens.rLg;

    Widget inner = Container(
      padding: const EdgeInsets.all(HiveTokens.s3),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(radius - borderWidth),
        gradient: LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [
            const Color(0xFF3D3020),
            t.slate2,
          ],
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Header row
          Row(
            children: [
              const Text(
                '◎ NOW BUILDING',
                style: TextStyle(
                  fontSize: 10,
                  letterSpacing: 0.14 * 10,
                  fontWeight: FontWeight.w700,
                  color: HivePalette.amber1,
                ),
              ),
              const Spacer(),
              Text(
                widget.slug,
                style: TextStyle(
                  fontSize: 10,
                  color: t.inkFaint,
                  fontFamily: 'monospace',
                  letterSpacing: 0.04 * 10,
                ),
              ),
            ],
          ),
          const SizedBox(height: 4),
          Text(
            widget.title,
            maxLines: 2,
            overflow: TextOverflow.ellipsis,
            style: TextStyle(
              fontSize: 14.5,
              fontWeight: FontWeight.w700,
              color: t.ink,
              height: 1.25,
            ),
          ),
          const SizedBox(height: 7),
          // Progress bar
          Container(
            height: 5,
            decoration: BoxDecoration(
              color: t.slate3,
              borderRadius: BorderRadius.circular(HiveTokens.rPill),
            ),
            child: FractionallySizedBox(
              alignment: Alignment.centerLeft,
              widthFactor: widget.progress.clamp(0.0, 1.0),
              child: Container(
                decoration: BoxDecoration(
                  borderRadius: BorderRadius.circular(HiveTokens.rPill),
                  gradient: const LinearGradient(
                    colors: [HivePalette.amber2, HivePalette.amberGlow],
                  ),
                ),
              ),
            ),
          ),
          const SizedBox(height: 7),
          Text(
            widget.subtitle,
            style: TextStyle(
              fontSize: 11,
              color: t.inkFaint,
              fontFamily: 'monospace',
            ),
          ),
        ],
      ),
    );

    // Clip for the sweep effect
    if (!reduce) {
      inner = Stack(
        children: [
          inner,
          // Scan sweep
          Positioned.fill(
            child: ClipRRect(
              borderRadius: BorderRadius.circular(radius - borderWidth),
              child: AnimatedBuilder(
                animation: _sweep,
                builder: (context, _) {
                  // sweep band: 40% wide, translates from -120% to +320%
                  // total travel = 440% of width
                  final offset = -1.2 + _sweep.value * 4.4;
                  return Align(
                    alignment: Alignment.centerLeft,
                    child: FractionalTranslation(
                      translation: Offset(offset, 0),
                      child: FractionallySizedBox(
                        widthFactor: 0.4,
                        child: Container(
                          decoration: const BoxDecoration(
                            gradient: LinearGradient(
                              colors: [
                                Colors.transparent,
                                Color(0x1FFFB94D), // amberGlow 12%
                                Colors.transparent,
                              ],
                            ),
                          ),
                        ),
                      ),
                    ),
                  );
                },
              ),
            ),
          ),
        ],
      );
    }

    // Gradient border wrapper
    Widget bordered = Container(
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(radius),
        gradient: const LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [HivePalette.amber2, HivePalette.amber3],
        ),
      ),
      padding: const EdgeInsets.all(borderWidth),
      child: inner,
    );

    if (reduce) return bordered;

    // Glow wrapper
    return AnimatedBuilder(
      animation: _glow,
      builder: (context, child) {
        final g = _glow.value;
        return DecoratedBox(
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(radius),
            boxShadow: [
              BoxShadow(
                color: HivePalette.amber1.withValues(alpha: 0.30 * g),
                blurRadius: 18 * g,
                spreadRadius: 0,
              ),
            ],
          ),
          child: child,
        );
      },
      child: bordered,
    );
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// SectionHeader
// ─────────────────────────────────────────────────────────────────────────────

/// Uppercase amber section label with optional [count] and a trailing hairline
/// gradient rule that fades to transparent.
class SectionHeader extends StatelessWidget {
  const SectionHeader({super.key, required this.label, this.count});
  final String label;
  final int? count;

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context).extension<HiveTokens>()!;
    return Row(
      children: [
        Text(
          label.toUpperCase(),
          style: const TextStyle(
            fontSize: 11,
            fontWeight: FontWeight.w700,
            letterSpacing: 0.16 * 11,
            color: HivePalette.amber1,
          ),
        ),
        if (count != null) ...[
          const SizedBox(width: 7),
          Text(
            '${count!}',
            style: TextStyle(
              fontSize: 11,
              color: t.inkFaint,
              fontFamily: 'monospace',
            ),
          ),
        ],
        const SizedBox(width: 8),
        // Hairline gradient rule
        Expanded(
          child: Container(
            height: 1,
            decoration: BoxDecoration(
              gradient: LinearGradient(
                colors: [t.slate4, Colors.transparent],
              ),
            ),
          ),
        ),
      ],
    );
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// HexLogo
// ─────────────────────────────────────────────────────────────────────────────

/// The Hive brand mark: the Material `hive_outlined` honeycomb glyph in amber
/// with a subtle drop-shadow glow. This is the canonical mark shared across
/// every surface (app, wallpaper dashboard, web crew board); see DESIGN.md.
/// Previously a unicode `⬡`, switched to the honeycomb glyph for cross-app
/// logo unity.
class HexLogo extends StatelessWidget {
  const HexLogo({super.key, this.size = 22});
  final double size;

  @override
  Widget build(BuildContext context) {
    return Icon(
      Icons.hive_outlined,
      size: size,
      color: HivePalette.amber1,
      shadows: const [
        Shadow(
          color: Color(0x80E0A445), // amber1 @ 50%
          blurRadius: 8,
        ),
      ],
    );
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// HiveRadialBackground
// ─────────────────────────────────────────────────────────────────────────────

/// The warm radial-depth background specified in DESIGN.md.
/// Wrap this around screen content or use as a scaffold background.
class HiveRadialBackground extends StatelessWidget {
  const HiveRadialBackground({super.key, required this.child});
  final Widget child;

  @override
  Widget build(BuildContext context) {
    return DecoratedBox(
      decoration: const BoxDecoration(
        gradient: RadialGradient(
          center: Alignment(0, -1.4), // 50% -10% offset approximation
          radius: 1.2,
          colors: [
            Color(0xFF2A2015), // warm, ochre-tinted highlight
            Color(0xFF0E0C0A), // near-black warm base
          ],
          stops: [0.0, 1.0],
        ),
      ),
      child: child,
    );
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// HiveAmberGlowNavTile (for nav active-state glow)
// ─────────────────────────────────────────────────────────────────────────────

/// Paints an amber glow halo behind a nav icon when [active].
/// Used by the shell to override the active nav-item indicator.
class AmberGlowBox extends StatelessWidget {
  const AmberGlowBox({super.key, required this.child, required this.active});
  final Widget child;
  final bool active;

  @override
  Widget build(BuildContext context) {
    if (!active) return child;
    return DecoratedBox(
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(HiveTokens.rMd),
        color: HivePalette.amber2.withValues(alpha: 0.25),
        boxShadow: [
          BoxShadow(
            color: HivePalette.amber1.withValues(alpha: 0.35),
            blurRadius: 12,
            spreadRadius: 0,
          ),
        ],
      ),
      child: child,
    );
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Sweep-line painter used internally
// (exposed as a utility for potential reuse elsewhere)
// ─────────────────────────────────────────────────────────────────────────────

/// Paints a single diagonal sweep line on a canvas.
class SweepLinePainter extends CustomPainter {
  SweepLinePainter(this.progress, this.color);
  final double progress; // 0..1
  final Color color;

  @override
  void paint(Canvas canvas, Size size) {
    if (size.isEmpty) return;
    final x = -size.width * 0.4 + progress * size.width * 1.8;
    final paint = Paint()
      ..shader = LinearGradient(
        colors: [Colors.transparent, color.withValues(alpha: 0.12), Colors.transparent],
      ).createShader(Rect.fromLTWH(x, 0, size.width * 0.4, size.height));
    canvas.drawRect(
      Rect.fromLTWH(x, 0, size.width * 0.4, size.height),
      paint,
    );
  }

  @override
  bool shouldRepaint(SweepLinePainter old) =>
      old.progress != progress || old.color != color;
}

// ─────────────────────────────────────────────────────────────────────────────
// StaticProgressBar (utility for internal reuse)
// ─────────────────────────────────────────────────────────────────────────────

/// Horizontal progress bar, copper→amberGlow gradient fill.
class HiveProgressBar extends StatelessWidget {
  const HiveProgressBar({
    super.key,
    required this.progress,
    this.height = 5,
  });
  final double progress;
  final double height;

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context).extension<HiveTokens>()!;
    return Container(
      height: height,
      decoration: BoxDecoration(
        color: t.slate3,
        borderRadius: BorderRadius.circular(HiveTokens.rPill),
      ),
      child: FractionallySizedBox(
        alignment: Alignment.centerLeft,
        widthFactor: progress.clamp(0.0, 1.0),
        child: Container(
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(HiveTokens.rPill),
            gradient: const LinearGradient(
              colors: [HivePalette.amber2, HivePalette.amberGlow],
            ),
          ),
        ),
      ),
    );
  }
}

