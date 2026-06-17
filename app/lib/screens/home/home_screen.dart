import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../models/digest.dart';
import '../../state/activity_feed.dart';
import '../../state/board_state.dart';
import '../../state/home_state.dart';
import '../../theme/hive_palette.dart';
import '../../theme/hive_tokens.dart';
import '../../widgets/hive_motion.dart';
import '../../widgets/state_views.dart';

/// v2 landing — now-building banner, digest ("what's new") + the unified
/// live activity feed off the /v1/events spine.
class HomeScreen extends ConsumerWidget {
  const HomeScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = Theme.of(context).extension<HiveTokens>()!;
    final digest = ref.watch(digestProvider);
    final feed = ref.watch(activityFeedProvider);
    final boardAsync = ref.watch(boardStateProvider);

    return RefreshIndicator(
      onRefresh: () async => ref.invalidate(digestProvider),
      child: ListView(
        padding: const EdgeInsets.fromLTRB(14, 14, 14, 28),
        children: [
          // ── Now-Building banner ─────────────────────────────────────────
          boardAsync.when(
            loading: () => const SizedBox.shrink(),
            error: (e, _) => const SizedBox.shrink(),
            data: (board) {
              final inProgress =
                  (board.byColumn['in_progress'] ?? const []);
              if (inProgress.isEmpty) return const SizedBox.shrink();
              final task = inProgress.first;
              final tokenTotal = task.hiveTokens + task.claudeTokens;
              final subtitle =
                  'turn ${task.attemptCount} · ${task.assignee} · ${_fmtTok(tokenTotal)} tok';
              return Padding(
                padding: const EdgeInsets.only(bottom: 16),
                child: NowBuildingCard(
                  slug: task.slug,
                  title: task.title,
                  subtitle: subtitle,
                  // Use attempt-count as a rough proxy for progress (caps at 80%)
                  progress: (task.attemptCount / 5.0).clamp(0.05, 0.80),
                ),
              );
            },
          ),

          // ── "What's new" digest ─────────────────────────────────────────
          SectionHeader(
            label: "What's New",
            count: null,
          ),
          const SizedBox(height: 8),
          digest.when(
            loading: () => const SizedBox(
                height: 90, child: LoadingView()),
            error: (e, _) => _DigestEmpty(tokens: t),
            data: (d) => _DigestStrip(d: d, tokens: t),
          ),
          const SizedBox(height: 22),

          // ── Live activity feed ──────────────────────────────────────────
          feed.when(
            loading: () => Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const SectionHeader(label: 'Live Activity'),
                const SizedBox(height: 8),
                const LoadingView(),
              ],
            ),
            error: (e, _) => Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const SectionHeader(label: 'Live Activity'),
                const SizedBox(height: 8),
                ErrorView(error: e.toString()),
              ],
            ),
            data: (events) => Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                SectionHeader(
                  label: 'Live Activity',
                  count: events.isEmpty ? null : events.length,
                ),
                const SizedBox(height: 8),
                if (events.isEmpty)
                  _QuietState(tokens: t)
                else
                  for (final e in events.take(40))
                    _FeedRow(event: e, tokens: t),
              ],
            ),
          ),
        ],
      ),
    );
  }

  static String _fmtTok(int n) {
    if (n >= 1000000) return '${(n / 1000000).toStringAsFixed(1)}M';
    if (n >= 1000) return '${(n / 1000).toStringAsFixed(1)}k';
    return '$n';
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Quiet empty state
// ─────────────────────────────────────────────────────────────────────────────

class _QuietState extends StatelessWidget {
  const _QuietState({required this.tokens});
  final HiveTokens tokens;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 20),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          const HexLogo(size: 28),
          const SizedBox(height: 10),
          Text(
            'Quiet. The swarm has it.',
            style: TextStyle(
              color: tokens.inkDim,
              fontSize: 14,
              fontWeight: FontWeight.w600,
            ),
          ),
          const SizedBox(height: 4),
          Text(
            'Live activity appears here.',
            style: TextStyle(color: tokens.inkFaint, fontSize: 12),
          ),
        ],
      ),
    );
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Digest strip
// ─────────────────────────────────────────────────────────────────────────────

class _DigestStrip extends StatelessWidget {
  const _DigestStrip({required this.d, required this.tokens});
  final Digest d;
  final HiveTokens tokens;

  @override
  Widget build(BuildContext context) {
    final cards = <(String, int, IconData)>[
      ('Escalations', d.newEscalations, Icons.priority_high),
      ('Images', d.newImages, Icons.image_outlined),
      ('Pinned turns', d.newPinnedTurns, Icons.push_pin_outlined),
      ('Calendar fires', d.completedCalendarFires, Icons.event_available),
    ];
    if (!d.hasNews) {
      return Padding(
        padding: const EdgeInsets.symmetric(vertical: 8),
        child: Text(
          'All caught up.',
          style: TextStyle(
            color: tokens.inkFaint,
            fontSize: 13,
            fontStyle: FontStyle.italic,
          ),
        ),
      );
    }
    return Wrap(
      spacing: 8,
      runSpacing: 8,
      children: [
        for (final c in cards.where((c) => c.$2 > 0))
          Container(
            width: 110,
            padding: const EdgeInsets.all(12),
            decoration: BoxDecoration(
                color: tokens.slate2.withValues(alpha: 0.55),
                borderRadius: BorderRadius.circular(HiveTokens.rMd),
                border: Border.all(
                    color: tokens.slate3.withValues(alpha: 0.5))),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Icon(c.$3, color: tokens.amber1, size: 18),
                const SizedBox(height: 6),
                Text(
                  '${c.$2}',
                  style: TextStyle(
                    color: tokens.ink,
                    fontSize: 22,
                    fontWeight: FontWeight.w800,
                    fontFeatures: const [FontFeature.tabularFigures()],
                  ),
                ),
                Text(
                  c.$1,
                  style: TextStyle(color: tokens.inkFaint, fontSize: 11),
                ),
              ],
            ),
          ),
      ],
    );
  }
}

class _DigestEmpty extends StatelessWidget {
  const _DigestEmpty({required this.tokens});
  final HiveTokens tokens;
  @override
  Widget build(BuildContext context) => Text('Digest unavailable',
      style: TextStyle(color: tokens.inkFaint, fontSize: 12));
}

// ─────────────────────────────────────────────────────────────────────────────
// Feed row with PulseDot
// ─────────────────────────────────────────────────────────────────────────────

class _FeedRow extends StatelessWidget {
  const _FeedRow({required this.event, required this.tokens});
  final ActivityEvent event;
  final HiveTokens tokens;

  @override
  Widget build(BuildContext context) {
    final dotColor = _color(event.type);
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: const EdgeInsets.only(top: 4, right: 10),
            child: PulseDot(color: dotColor, size: 8),
          ),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  event.title,
                  style: TextStyle(
                    color: tokens.ink,
                    fontSize: 13,
                    fontWeight: FontWeight.w600,
                  ),
                ),
                if (event.detail.isNotEmpty)
                  Text(
                    event.detail,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style:
                        TextStyle(color: tokens.inkFaint, fontSize: 11.5),
                  ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Color _color(String type) {
    if (type == 'board_event') return HivePalette.green;
    if (type == 'scout_alert' || type.contains('error')) {
      return HivePalette.red;
    }
    if (type == 'hive_turn_done') return HivePalette.amber1;
    return HivePalette.amber2;
  }
}
