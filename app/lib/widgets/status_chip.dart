import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../data/sync/sync_providers.dart';
import '../data/sync/sync_service.dart';
import '../theme/hive_palette.dart';
import '../theme/hive_tokens.dart';
import 'hive_motion.dart';
import 'pending_actions_sheet.dart';

/// Always-visible connection pill: green live / amber syncing / grey offline,
/// animated PulseDot for the status indicator, plus "N queued" when the
/// outbox is non-empty. Color is NEVER the only signal -- a text label is
/// always included (color-blind safe). Tap opens the pending-actions sheet.
class StatusChip extends ConsumerWidget {
  const StatusChip({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final phase = ref.watch(syncPhaseProvider).asData?.value ??
        SyncPhase.disconnected;
    final queued = ref.watch(queuedCountProvider).asData?.value ?? 0;

    final (dotColor, labelColor, label, bg, border) = switch (phase) {
      SyncPhase.live => (
          HivePalette.green,
          HivePalette.green,
          'live',
          const Color(0x1A52C385), // green @ 10%
          const Color(0x4052C385), // green @ 25%
        ),
      SyncPhase.hydrating => (
          HivePalette.amber1,
          HivePalette.amber1,
          'syncing',
          const Color(0x1AE0A445), // amber1 @ 10%
          const Color(0x40E0A445), // amber1 @ 25%
        ),
      SyncPhase.disconnected => (
          HivePalette.inkFaint,
          HivePalette.inkFaint,
          'offline',
          const Color(0x0DFFFFFF), // near-transparent
          const Color(0x267E7B73), // inkFaint @ 15%
        ),
    };

    return InkWell(
      borderRadius: BorderRadius.circular(HiveTokens.rPill),
      onTap: queued > 0 ? () => showPendingActionsSheet(context) : null,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
        decoration: BoxDecoration(
          color: bg,
          borderRadius: BorderRadius.circular(HiveTokens.rPill),
          border: Border.all(color: border),
        ),
        child: Row(mainAxisSize: MainAxisSize.min, children: [
          PulseDot(color: dotColor, size: 7),
          const SizedBox(width: 6),
          Text(
            queued > 0 ? '$label · $queued queued' : label,
            style: TextStyle(
              color: labelColor,
              fontSize: 11.5,
              fontWeight: FontWeight.w600,
              fontFeatures: const [FontFeature.tabularFigures()],
            ),
          ),
        ]),
      ),
    );
  }
}
