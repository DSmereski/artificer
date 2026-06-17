import 'package:flutter/material.dart';

import '../theme/hive_tokens.dart';

/// Small colored dot — task status, connection, agent liveness.
class StatusDot extends StatelessWidget {
  const StatusDot(this.color, {super.key, this.size = 8});
  final Color color;
  final double size;

  @override
  Widget build(BuildContext context) => Container(
        width: size,
        height: size,
        decoration: BoxDecoration(color: color, shape: BoxShape.circle),
      );
}

/// Standard empty state: icon, headline, optional hint.
class EmptyState extends StatelessWidget {
  const EmptyState(
      {super.key, required this.icon, required this.title, this.hint});
  final IconData icon;
  final String title;
  final String? hint;

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context).extension<HiveTokens>()!;
    return Center(
      child: Column(mainAxisSize: MainAxisSize.min, children: [
        Icon(icon, size: 40, color: t.slate3),
        const SizedBox(height: HiveTokens.s3),
        Text(title, style: TextStyle(color: t.inkDim, fontSize: 15)),
        if (hint != null) ...[
          const SizedBox(height: HiveTokens.s1),
          Text(hint!, style: TextStyle(color: t.inkFaint, fontSize: 12)),
        ],
      ]),
    );
  }
}

/// Shimmer-free skeleton block (fades between two slate tones).
class SkeletonLoader extends StatefulWidget {
  const SkeletonLoader({super.key, this.height = 16, this.width});
  final double height;
  final double? width;

  @override
  State<SkeletonLoader> createState() => _SkeletonLoaderState();
}

class _SkeletonLoaderState extends State<SkeletonLoader>
    with SingleTickerProviderStateMixin {
  late final AnimationController _c = AnimationController(
      vsync: this, duration: const Duration(milliseconds: 900))
    ..repeat(reverse: true);

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context).extension<HiveTokens>()!;
    return FadeTransition(
      opacity: Tween(begin: 0.5, end: 1.0).animate(_c),
      child: Container(
        height: widget.height,
        width: widget.width,
        decoration: BoxDecoration(
          color: t.slate2,
          borderRadius: BorderRadius.circular(HiveTokens.rSm),
        ),
      ),
    );
  }
}
