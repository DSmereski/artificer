import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../models/escalation.dart';
import '../../state/home_state.dart';
import '../../state/session.dart';
import '../../theme/hive_tokens.dart';
import '../../widgets/state_views.dart';

/// First-class escalation triage — the human-in-the-loop queue for the
/// autonomous crew. v1 buried this in the Activity screen with no
/// reopen; v2 promotes it with resolve + reopen.
class EscalationsScreen extends ConsumerWidget {
  const EscalationsScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = Theme.of(context).extension<HiveTokens>()!;
    final esc = ref.watch(escalationsProvider);
    return esc.when(
      loading: () => const LoadingView(),
      error: (e, _) => ErrorView(
          error: e.toString(),
          onRetry: () => ref.invalidate(escalationsProvider)),
      data: (list) {
        final open = list.where((e) => !e.resolved).toList();
        if (open.isEmpty) {
          return const EmptyView(
              title: 'No open escalations.',
              hint: 'The crew hasn\'t flagged anything for you.',
              icon: Icons.verified_outlined);
        }
        return ListView.builder(
          padding: const EdgeInsets.fromLTRB(12, 12, 12, 24),
          itemCount: open.length,
          itemBuilder: (_, i) => _EscalationCard(esc: open[i], tokens: t),
        );
      },
    );
  }
}

class _EscalationCard extends ConsumerStatefulWidget {
  const _EscalationCard({required this.esc, required this.tokens});
  final Escalation esc;
  final HiveTokens tokens;

  @override
  ConsumerState<_EscalationCard> createState() => _EscalationCardState();
}

class _EscalationCardState extends ConsumerState<_EscalationCard> {
  bool _busy = false;

  Future<void> _act(Future<void> Function() op) async {
    setState(() => _busy = true);
    try {
      await op();
      ref.invalidate(escalationsProvider);
    } catch (_) {
      // surfaced by the list's error path on next fetch
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = widget.tokens;
    final e = widget.esc;
    final gw = ref.read(gatewayClientProvider);
    final sevColor = e.severity == 'high'
        ? const Color(0xFFE08B8B)
        : e.severity == 'low'
            ? t.slate4
            : t.amber2;
    return Card(
      color: t.slate1.withValues(alpha: 0.55),
      margin: const EdgeInsets.symmetric(vertical: 5),
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Container(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                  decoration: BoxDecoration(
                      color: sevColor.withValues(alpha: 0.16),
                      borderRadius: BorderRadius.circular(6)),
                  child: Text(e.severity,
                      style: TextStyle(
                          color: sevColor,
                          fontSize: 11,
                          fontWeight: FontWeight.w700)),
                ),
                const Spacer(),
                Text(e.reportedAt.isNotEmpty ? e.reportedAt.split('T').first : '',
                    style: TextStyle(color: t.slate4, fontSize: 10.5)),
              ],
            ),
            const SizedBox(height: 8),
            Text(e.title,
                style: TextStyle(
                    color: t.ink, fontSize: 15, fontWeight: FontWeight.w600)),
            if (e.summary.isNotEmpty) ...[
              const SizedBox(height: 4),
              Text(e.summary,
                  style: TextStyle(color: t.slate4, fontSize: 12.5, height: 1.3)),
            ],
            if (e.userMsg.isNotEmpty) ...[
              const SizedBox(height: 8),
              Container(
                padding: const EdgeInsets.all(8),
                decoration: BoxDecoration(
                    color: t.slate0.withValues(alpha: 0.5),
                    borderRadius: BorderRadius.circular(6)),
                child: Text('“${e.userMsg}”',
                    style: TextStyle(
                        color: t.ink,
                        fontSize: 12,
                        fontStyle: FontStyle.italic)),
              ),
            ],
            const SizedBox(height: 10),
            Row(
              mainAxisAlignment: MainAxisAlignment.end,
              children: [
                TextButton(
                  onPressed: _busy || gw == null
                      ? null
                      : () => _act(() => gw.reopenEscalation(e.slug)),
                  child: const Text('Reopen'),
                ),
                const SizedBox(width: 8),
                FilledButton(
                  onPressed: _busy || gw == null
                      ? null
                      : () => _act(() => gw.resolveEscalation(e.slug)),
                  child: const Text('Resolve'),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}
