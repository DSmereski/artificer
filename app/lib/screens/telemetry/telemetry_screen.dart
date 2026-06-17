import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../state/session.dart';
import '../../theme/hive_tokens.dart';
import '../../widgets/state_views.dart';

final _telemetryProvider =
    FutureProvider<(Map<String, dynamic>, Map<String, dynamic>)>((ref) async {
  final gw = ref.watch(gatewayClientProvider);
  if (gw == null) return (<String, dynamic>{}, <String, dynamic>{});
  Map<String, dynamic> conc = {};
  Map<String, dynamic> turn = {};
  try {
    conc = await gw.concurrency();
  } catch (_) {}
  try {
    turn = await gw.lastTurn();
  } catch (_) {}
  return (conc, turn);
});

/// Power-user telemetry — helper concurrency + last-turn introspection.
class TelemetryScreen extends ConsumerWidget {
  const TelemetryScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = Theme.of(context).extension<HiveTokens>()!;
    final tel = ref.watch(_telemetryProvider);
    return Scaffold(
      appBar: AppBar(
          title: const Text('Telemetry'),
          backgroundColor: Colors.transparent,
          elevation: 0),
      body: tel.when(
        loading: () => const LoadingView(),
        error: (e, _) => ErrorView(error: e.toString()),
        data: (data) {
          final (conc, turn) = data;
          return ListView(
            padding: const EdgeInsets.fromLTRB(14, 12, 14, 24),
            children: [
              _section('CONCURRENCY', t),
              _json(conc, t),
              const SizedBox(height: 16),
              _section('LAST TURN', t),
              _json(turn, t),
            ],
          );
        },
      ),
    );
  }

  Widget _section(String s, HiveTokens t) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 8),
        child: Text(s,
            style: TextStyle(
                color: t.slate4,
                fontSize: 11,
                fontWeight: FontWeight.w700,
                letterSpacing: 1)),
      );

  Widget _json(Map<String, dynamic> m, HiveTokens t) {
    if (m.isEmpty) {
      return Text('—', style: TextStyle(color: t.slate4));
    }
    const enc = JsonEncoder.withIndent('  ');
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
          color: t.slate1.withValues(alpha: 0.5),
          borderRadius: BorderRadius.circular(8)),
      child: SelectableText(enc.convert(m),
          style: TextStyle(
              color: t.ink, fontFamily: 'monospace', fontSize: 12)),
    );
  }
}
