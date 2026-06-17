import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../models/board_stats.dart';
import '../../state/board_state.dart';
import '../../theme/hive_tokens.dart';
import '../../widgets/state_views.dart';

/// Stats surface for the crew board — pipeline counts, token totals
/// (hive vs claude SEPARATE, never combined), avg-tokens-per-task,
/// parse-fail rate (should sit ~0 after P1), lessons learned, smoke
/// gate, and top projects.
class BoardStatsView extends ConsumerWidget {
  const BoardStatsView({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = Theme.of(context).extension<HiveTokens>()!;
    final stats = ref.watch(boardStatsProvider);
    return stats.when(
      loading: () => const LoadingView(),
      error: (e, _) => ErrorView(
        error: e.toString(),
        onRetry: () => ref.invalidate(boardStatsProvider),
      ),
      data: (s) => ListView(
        padding: const EdgeInsets.fromLTRB(12, 14, 12, 24),
        children: [
          _section('PIPELINE', t),
          _pipelineGrid(s, t),
          const SizedBox(height: 16),
          _section('TOKENS — hive & claude tracked separately, never combined', t),
          _tokenGrid(s, t),
          const SizedBox(height: 16),
          _section('QUALITY', t),
          _qualityGrid(s, t),
          if (s.topProjects.isNotEmpty) ...[
            const SizedBox(height: 16),
            _section('TOP PROJECTS', t),
            _projectsTable(s, t),
          ],
        ],
      ),
    );
  }

  Widget _section(String label, HiveTokens t) => Padding(
        padding: const EdgeInsets.fromLTRB(2, 10, 2, 8),
        child: Text(
          label,
          style: TextStyle(
            color: t.slate4,
            fontSize: 11,
            fontWeight: FontWeight.w700,
            letterSpacing: 0.8,
          ),
        ),
      );

  Widget _pipelineGrid(BoardStats s, HiveTokens t) {
    const cols = ['proposed', 'backlog', 'ready', 'in_progress',
                  'qa', 'review', 'done'];
    return Wrap(
      spacing: 8,
      runSpacing: 8,
      children: [
        for (final c in cols)
          _StatCard(
            value: '${s.byStatus[c] ?? 0}',
            label: c.replaceAll('_', ' '),
            tokens: t,
          ),
        _StatCard(
          value: '${s.byStatus['archived'] ?? 0}',
          label: 'archived',
          tokens: t,
          dim: true,
        ),
      ],
    );
  }

  Widget _tokenGrid(BoardStats s, HiveTokens t) => Wrap(
        spacing: 8,
        runSpacing: 8,
        children: [
          _StatCard(
            value: _fmt(s.hiveTokens),
            label: 'hive total',
            color: const Color(0xFF8FD19E),
            tokens: t,
          ),
          _StatCard(
            value: _fmt(s.claudeTokens),
            label: 'claude total',
            color: const Color(0xFFC9A0FF),
            tokens: t,
          ),
          _StatCard(
            value: _fmt(s.avgHiveTokensPerTask),
            label: 'avg hive / task',
            color: const Color(0xFF8FD19E),
            tokens: t,
          ),
          _StatCard(
            value: _fmt(s.avgClaudeTokensPerTask),
            label: 'avg claude / task',
            color: const Color(0xFFC9A0FF),
            tokens: t,
          ),
        ],
      );

  Widget _qualityGrid(BoardStats s, HiveTokens t) {
    final pct = (s.parseFailRate * 100);
    final pfColor =
        s.parseFailRate > 0.05 ? const Color(0xFFE08B8B) : const Color(0xFF8FD19E);
    return Wrap(
      spacing: 8,
      runSpacing: 8,
      children: [
        _StatCard(
          value: '${pct.toStringAsFixed(1)}%',
          label: 'parse-fail (${s.parseFailTurns} turns)',
          color: pfColor,
          tokens: t,
        ),
        _StatCard(
          value: '${s.lessons}',
          label: 'lessons learned',
          color: const Color(0xFFE8C97A),
          tokens: t,
        ),
        _StatCard(
          value: '${s.smokePass}✓ ${s.smokeFail}✗',
          label: 'smoke gate',
          tokens: t,
        ),
        _StatCard(
          value: s.avgAttempts.toStringAsFixed(1),
          label: 'avg attempts',
          tokens: t,
        ),
      ],
    );
  }

  Widget _projectsTable(BoardStats s, HiveTokens t) => Column(
        children: [
          for (final p in s.topProjects)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 3),
              child: Row(
                children: [
                  Expanded(
                    child: Text(p.slug,
                        style: TextStyle(
                            color: t.ink,
                            fontSize: 12,
                            fontFamily: 'monospace')),
                  ),
                  Text('${p.done} done · ${p.active} live  ',
                      style: TextStyle(color: t.slate4, fontSize: 11)),
                  Text('H ${_fmt(p.hiveTokens)} ',
                      style: const TextStyle(
                          color: Color(0xFF8FD19E), fontSize: 11)),
                  Text('C ${_fmt(p.claudeTokens)}',
                      style: const TextStyle(
                          color: Color(0xFFC9A0FF), fontSize: 11)),
                ],
              ),
            ),
        ],
      );

  static String _fmt(int n) {
    if (n >= 1000000) return '${(n / 1000000).toStringAsFixed(1)}M';
    if (n >= 1000) return '${(n / 1000).toStringAsFixed(1)}k';
    return '$n';
  }
}

class _StatCard extends StatelessWidget {
  const _StatCard({
    required this.value,
    required this.label,
    required this.tokens,
    this.color,
    this.dim = false,
  });

  final String value;
  final String label;
  final HiveTokens tokens;
  final Color? color;
  final bool dim;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 108,
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 12),
      decoration: BoxDecoration(
        color: tokens.slate1.withValues(alpha: 0.5),
        borderRadius: BorderRadius.circular(10),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            value,
            style: TextStyle(
              color: dim ? tokens.slate4 : (color ?? tokens.ink),
              fontSize: 20,
              fontWeight: FontWeight.w700,
            ),
          ),
          const SizedBox(height: 2),
          Text(
            label,
            style: TextStyle(color: tokens.slate4, fontSize: 10.5),
          ),
        ],
      ),
    );
  }
}
