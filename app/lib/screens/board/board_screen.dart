import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../models/crew_task.dart';
import '../../state/board_state.dart';
import '../../state/session.dart';
import '../../theme/hive_palette.dart';
import '../../theme/hive_theme.dart';
import '../../theme/hive_tokens.dart';
import '../../widgets/hive_motion.dart';
import '../../widgets/state_views.dart';
import 'board_stats_view.dart';
import 'task_detail_screen.dart';

/// Crew Board — the kanban that drives the hive coding pipeline. Two
/// tabs: the Board (columns proposed → done with task cards showing
/// assignee + SEPARATE hive/claude token chips + smoke/reviewer badges)
/// and Stats (pipeline counts, token totals, parse-fail, lessons).
class BoardScreen extends ConsumerWidget {
  const BoardScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = Theme.of(context).extension<HiveTokens>()!;
    final paused = ref.watch(boardPausedProvider).asData?.value ?? false;

    return DefaultTabController(
      length: 2,
      child: Scaffold(
        backgroundColor: Colors.transparent,
        appBar: AppBar(
          backgroundColor: Colors.transparent,
          elevation: 0,
          title: const Text('Crew Board'),
          actions: [
            IconButton(
              icon: Icon(
                paused ? Icons.play_circle : Icons.pause_circle,
                color: paused ? HivePalette.amber1 : t.inkDim,
              ),
              tooltip: paused ? 'Resume dispatcher' : 'Pause dispatcher',
              onPressed: () async {
                final gw = ref.read(gatewayClientProvider);
                if (gw == null) return;
                try {
                  await gw.setBoardPaused(!paused);
                  // The boardStateProvider will refresh via SyncService on
                  // the next WS frame or poll; invalidate for immediate update.
                  ref.invalidate(boardStateProvider);
                } catch (e) {
                  if (context.mounted) {
                    ScaffoldMessenger.of(context).showSnackBar(
                      SnackBar(
                        content: Text(
                          '${paused ? "Resume" : "Pause"} failed: $e',
                        ),
                      ),
                    );
                  }
                }
              },
            ),
          ],
          bottom: TabBar(
            labelColor: t.amber1,
            unselectedLabelColor: t.inkFaint,
            indicatorColor: t.amber1,
            tabs: const [Tab(text: 'BOARD'), Tab(text: 'STATS')],
          ),
        ),
        body: hiveScaffoldBackground(
          Column(
            children: [
              // Paused banner — amber, slim, shown only when dispatcher paused.
              AnimatedSize(
                duration: HiveTokens.base,
                child: paused
                    ? Container(
                        width: double.infinity,
                        padding: const EdgeInsets.symmetric(
                            horizontal: 16, vertical: 8),
                        color: const Color(0xFF2D1F00),
                        child: Row(
                          children: [
                            Icon(Icons.pause_circle_outline,
                                color: HivePalette.amber1, size: 16),
                            const SizedBox(width: 8),
                            Text(
                              'PAUSED — no new work starting. '
                              'In-flight tasks finish; reaper still runs.',
                              style: TextStyle(
                                color: HivePalette.amber1,
                                fontSize: 12,
                                fontWeight: FontWeight.w600,
                              ),
                            ),
                          ],
                        ),
                      )
                    : const SizedBox.shrink(),
              ),
              const Expanded(
                child: TabBarView(
                  children: [_BoardTab(), BoardStatsView()],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _BoardTab extends ConsumerWidget {
  const _BoardTab();

  static const Map<String, String> _columnLabels = {
    'proposed': 'Proposed',
    'backlog': 'Backlog',
    'ready': 'Ready',
    'in_progress': 'In Progress',
    'qa': 'QA',
    'review': 'Review',
    'done': 'Done',
  };

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = Theme.of(context).extension<HiveTokens>()!;
    final state = ref.watch(boardStateProvider);
    return Container(
      color: Colors.transparent,
      child: state.when(
        loading: () => const LoadingView(),
        error: (e, _) => ErrorView(
          error: e.toString(),
          onRetry: () => ref.invalidate(boardStateProvider),
        ),
        data: (board) {
          final byCol = board.byColumn;
          final liveTotal = kBoardColumns
              .fold<int>(0, (n, c) => n + (byCol[c]?.length ?? 0));
          if (liveTotal == 0) {
            return const EmptyView(
              title: 'Board is empty.',
              hint: 'Queue tasks for the hive to pick up.',
              icon: Icons.dashboard_outlined,
            );
          }
          return ListView(
            padding: const EdgeInsets.fromLTRB(12, 12, 12, 24),
            children: [
              for (final col in kBoardColumns)
                if ((byCol[col] ?? const []).isNotEmpty)
                  _BoardColumn(
                    label: _columnLabels[col] ?? col,
                    tasks: byCol[col]!,
                    tokens: t,
                    isInProgress: col == 'in_progress',
                    isQa: col == 'qa',
                    isReview: col == 'review',
                  ),
            ],
          );
        },
      ),
    );
  }
}

class _BoardColumn extends StatelessWidget {
  const _BoardColumn({
    required this.label,
    required this.tasks,
    required this.tokens,
    required this.isInProgress,
    required this.isQa,
    required this.isReview,
  });

  final String label;
  final List<CrewTask> tasks;
  final HiveTokens tokens;
  final bool isInProgress;
  final bool isQa;
  final bool isReview;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(4, 14, 4, 8),
          child: SectionHeader(label: label, count: tasks.length),
        ),
        for (final task in tasks)
          _TaskCard(
            task: task,
            tokens: tokens,
            glowing: isInProgress,
            showStatePill: isInProgress || isQa || isReview,
            isQa: isQa,
            isReview: isReview,
          ),
      ],
    );
  }
}

class _TaskCard extends StatelessWidget {
  const _TaskCard({
    required this.task,
    required this.tokens,
    required this.glowing,
    required this.showStatePill,
    required this.isQa,
    required this.isReview,
  });

  final CrewTask task;
  final HiveTokens tokens;
  final bool glowing;
  final bool showStatePill;
  final bool isQa;
  final bool isReview;

  @override
  Widget build(BuildContext context) {
    Widget card = Container(
      margin: const EdgeInsets.symmetric(vertical: 4),
      decoration: BoxDecoration(
        color: tokens.slate2.withValues(alpha: 0.65),
        borderRadius: BorderRadius.circular(HiveTokens.rLg),
        border: Border.all(
          color: glowing
              ? HivePalette.amber2.withValues(alpha: 0.55)
              : tokens.slate3.withValues(alpha: 0.5),
        ),
      ),
      child: InkWell(
        borderRadius: BorderRadius.circular(HiveTokens.rLg),
        onTap: () => Navigator.of(context).push(
          MaterialPageRoute<void>(
            builder: (_) => TaskDetailScreen(task: task),
          ),
        ),
        child: Padding(
          padding: const EdgeInsets.all(12),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Text(
                    task.slug,
                    style: TextStyle(
                      color: tokens.inkFaint,
                      fontSize: 10.5,
                      fontFamily: 'monospace',
                      letterSpacing: 0.04 * 10.5,
                      fontFeatures: const [FontFeature.tabularFigures()],
                    ),
                  ),
                  const Spacer(),
                  _AssigneeChip(assignee: task.assignee, tokens: tokens),
                ],
              ),
              const SizedBox(height: 4),
              Text(
                task.title,
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(
                  fontSize: 13.5,
                  fontWeight: FontWeight.w700,
                  color: tokens.ink,
                  height: 1.25,
                ),
              ),
              const SizedBox(height: 8),
              Wrap(
                spacing: 6,
                runSpacing: 4,
                crossAxisAlignment: WrapCrossAlignment.center,
                children: [
                  // State pill for in-progress, qa, and review columns
                  if (showStatePill && !isQa && !isReview)
                    _StatePill(
                      label: 'building · t${task.attemptCount}',
                      style: _PillStyle.copper,
                    ),
                  if (showStatePill && isQa)
                    const _StatePill(
                      label: 'qa',
                      style: _PillStyle.cyan,
                    ),
                  if (showStatePill && isReview)
                    const _StatePill(
                      label: 'review',
                      style: _PillStyle.amber,
                    ),
                  // Token chips — SEPARATE, never combined
                  if (task.hiveTokens > 0)
                    _TokenChip(
                      label: 'H ${_fmt(task.hiveTokens)}',
                      color: const Color(0xFF8FD19E),
                    ),
                  if (task.claudeTokens > 0)
                    _TokenChip(
                      label: 'C ${_fmt(task.claudeTokens)}',
                      color: const Color(0xFFC9A0FF),
                    ),
                  if (task.smokeOk == true)
                    const _TokenChip(
                        label: 'smoke ok', color: Color(0xFF8FD19E)),
                  if (task.smokeOk == false)
                    const _TokenChip(
                        label: 'smoke fail', color: Color(0xFFE08B8B)),
                  if (task.reviewBy != null && !isReview)
                    _TokenChip(label: 'review', color: tokens.amber2),
                  if (task.attemptCount > 1 && !glowing)
                    _TokenChip(
                      label: 'try ${task.attemptCount}',
                      color: tokens.inkFaint,
                    ),
                ],
              ),
            ],
          ),
        ),
      ),
    );

    if (!glowing) return card;
    return LiveGlow(active: true, child: card);
  }

  static String _fmt(int n) {
    if (n >= 1000000) return '${(n / 1000000).toStringAsFixed(1)}M';
    if (n >= 1000) return '${(n / 1000).toStringAsFixed(1)}k';
    return '$n';
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Assignee with pulsing presence dot
// ─────────────────────────────────────────────────────────────────────────────

class _AssigneeChip extends StatelessWidget {
  const _AssigneeChip({required this.assignee, required this.tokens});

  final String assignee;
  final HiveTokens tokens;

  @override
  Widget build(BuildContext context) {
    if (assignee == 'none') return const SizedBox.shrink();
    final color = assignee == 'hive'
        ? const Color(0xFF8FD19E)
        : assignee == 'claude-code'
            ? const Color(0xFFC9A0FF)
            : HivePalette.amber2;
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        PulseDot(color: color, size: 5),
        const SizedBox(width: 4),
        Text(
          assignee,
          style: TextStyle(
            color: color,
            fontSize: 10.5,
            fontWeight: FontWeight.w600,
          ),
        ),
      ],
    );
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// State pills
// ─────────────────────────────────────────────────────────────────────────────

enum _PillStyle { copper, amber, cyan }

class _StatePill extends StatelessWidget {
  const _StatePill({required this.label, required this.style});

  final String label;
  final _PillStyle style;

  @override
  Widget build(BuildContext context) {
    final (bg, border, fg) = style == _PillStyle.copper
        ? (
            const Color(0x28B8862F), // amber2 @ ~16%
            const Color(0x44B8862F), // amber2 @ ~27%
            HivePalette.amber2,
          )
        : style == _PillStyle.cyan
            ? (
                const Color(0x2854B6D6), // cyan @ ~16%
                const Color(0x4454B6D6), // cyan @ ~27%
                HivePalette.cyan,
              )
            : (
                const Color(0x28E0A445), // amber1 @ ~16%
                const Color(0x44E0A445), // amber1 @ ~27%
                HivePalette.amber1,
              );
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
      decoration: BoxDecoration(
        color: bg,
        borderRadius: BorderRadius.circular(HiveTokens.rPill),
        border: Border.all(color: border),
      ),
      child: Text(
        label,
        style: TextStyle(
          color: fg,
          fontSize: 10,
          fontWeight: FontWeight.w600,
        ),
      ),
    );
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Token chip (unchanged functionality, updated styling)
// ─────────────────────────────────────────────────────────────────────────────

class _TokenChip extends StatelessWidget {
  const _TokenChip({required this.label, required this.color});

  final String label;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 2),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.14),
        borderRadius: BorderRadius.circular(HiveTokens.rSm),
      ),
      child: Text(
        label,
        style: TextStyle(
          color: color,
          fontSize: 10.5,
          fontWeight: FontWeight.w600,
          fontFeatures: const [FontFeature.tabularFigures()],
        ),
      ),
    );
  }
}
