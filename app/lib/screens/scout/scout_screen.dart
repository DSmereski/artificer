import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../state/session.dart';
import '../../theme/hive_tokens.dart';
import '../../widgets/state_views.dart';

/// Scout — live GPU + disk monitoring (/v1/scout/status, 3s poll).
final scoutStatusProvider = StreamProvider<Map<String, dynamic>>((ref) {
  final gw = ref.watch(gatewayClientProvider);
  if (gw == null) return const Stream.empty();
  final ctrl = StreamController<Map<String, dynamic>>();
  Future<void> tick() async {
    try {
      ctrl.add(await gw.scoutStatus());
    } catch (e) {
      if (!ctrl.isClosed) ctrl.addError(e);
    }
  }

  unawaited(tick());
  final timer = Timer.periodic(const Duration(seconds: 3), (_) => tick());
  ref.onDispose(() {
    timer.cancel();
    ctrl.close();
  });
  return ctrl.stream;
});

class ScoutScreen extends ConsumerWidget {
  const ScoutScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = Theme.of(context).extension<HiveTokens>()!;
    final status = ref.watch(scoutStatusProvider);
    return Scaffold(
      appBar: AppBar(
          title: const Text('Scout'),
          backgroundColor: Colors.transparent,
          elevation: 0),
      body: status.when(
        loading: () => const LoadingView(),
        error: (e, _) => ErrorView(error: e.toString()),
        data: (s) {
          final gpus = (s['gpus'] as List?)?.cast<Map<String, dynamic>>() ??
              const [];
          final disks = (s['disks'] as List?)?.cast<Map<String, dynamic>>() ??
              const [];
          return ListView(
            padding: const EdgeInsets.fromLTRB(12, 12, 12, 24),
            children: [
              _label('GPUs', t),
              for (final g in gpus) _GpuCard(g: g, tokens: t),
              if (disks.isNotEmpty) ...[
                const SizedBox(height: 14),
                _label('DISKS', t),
                for (final d in disks) _DiskRow(d: d, tokens: t),
              ],
            ],
          );
        },
      ),
    );
  }

  Widget _label(String s, HiveTokens t) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 8),
        child: Text(s,
            style: TextStyle(
                color: t.slate4,
                fontSize: 11,
                fontWeight: FontWeight.w700,
                letterSpacing: 1)),
      );
}

class _GpuCard extends StatelessWidget {
  const _GpuCard({required this.g, required this.tokens});
  final Map<String, dynamic> g;
  final HiveTokens tokens;

  @override
  Widget build(BuildContext context) {
    final util = (g['utilization_pct'] ?? 0) as num;
    final vramPct = ((g['vram_used_pct'] ?? 0) as num).toDouble();
    final temp = (g['temp_c'] ?? 0) as num;
    final game = g['game'] as String?;
    return Container(
      margin: const EdgeInsets.symmetric(vertical: 5),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
          color: tokens.slate1.withValues(alpha: 0.5),
          borderRadius: BorderRadius.circular(10)),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text('${g['index'] ?? ''} · ${g['name'] ?? 'GPU'}',
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(
                        color: tokens.ink,
                        fontSize: 13,
                        fontWeight: FontWeight.w600)),
              ),
              Text('$temp°C  ·  $util%',
                  style: TextStyle(color: tokens.amber2, fontSize: 12)),
            ],
          ),
          const SizedBox(height: 8),
          _bar('VRAM ${(vramPct).toStringAsFixed(0)}%', vramPct / 100, tokens),
          if (game != null && game.isNotEmpty) ...[
            const SizedBox(height: 6),
            Text('▶ $game',
                style: TextStyle(color: tokens.slate4, fontSize: 11)),
          ],
        ],
      ),
    );
  }

  Widget _bar(String label, double frac, HiveTokens t) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(label, style: TextStyle(color: t.slate4, fontSize: 10.5)),
          const SizedBox(height: 3),
          ClipRRect(
            borderRadius: BorderRadius.circular(4),
            child: LinearProgressIndicator(
              value: frac.clamp(0, 1),
              minHeight: 6,
              backgroundColor: t.slate0,
              color: frac > 0.85 ? const Color(0xFFE08B8B) : t.amber1,
            ),
          ),
        ],
      );
}

class _DiskRow extends StatelessWidget {
  const _DiskRow({required this.d, required this.tokens});
  final Map<String, dynamic> d;
  final HiveTokens tokens;

  @override
  Widget build(BuildContext context) {
    final usedPct = ((d['used_pct'] ?? 0) as num).toDouble();
    final free = ((d['free_gb'] ?? 0) as num).toDouble();
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 5),
      child: Row(
        children: [
          SizedBox(
              width: 40,
              child: Text('${d['drive'] ?? ''}',
                  style: TextStyle(
                      color: tokens.ink,
                      fontSize: 12,
                      fontWeight: FontWeight.w600))),
          Expanded(
            child: ClipRRect(
              borderRadius: BorderRadius.circular(4),
              child: LinearProgressIndicator(
                value: (usedPct / 100).clamp(0, 1),
                minHeight: 6,
                backgroundColor: tokens.slate0,
                color: usedPct > 90 ? const Color(0xFFE08B8B) : tokens.amber1,
              ),
            ),
          ),
          const SizedBox(width: 8),
          Text('${free.toStringAsFixed(0)} GB free',
              style: TextStyle(color: tokens.slate4, fontSize: 11)),
        ],
      ),
    );
  }
}
