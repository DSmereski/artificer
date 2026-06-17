import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../data/db/app_database.dart';
import '../data/sync/sync_providers.dart';
import '../theme/hive_tokens.dart';

void showPendingActionsSheet(BuildContext context) => showModalBottomSheet(
      context: context,
      backgroundColor: Theme.of(context).extension<HiveTokens>()!.slate0,
      builder: (_) => const _PendingActionsSheet(),
    );

class _PendingActionsSheet extends ConsumerWidget {
  const _PendingActionsSheet();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = Theme.of(context).extension<HiveTokens>()!;
    final db = ref.watch(appDatabaseProvider);
    return StreamBuilder<List<OutboxRow>>(
      stream: db.watchOutbox(),
      builder: (context, snap) {
        final rows = snap.data ?? const <OutboxRow>[];
        if (rows.isEmpty) {
          return const SizedBox(
              height: 160,
              child: Center(child: Text('No pending actions')));
        }
        return ListView.builder(
          shrinkWrap: true,
          itemCount: rows.length,
          itemBuilder: (context, i) {
            final r = rows[i];
            final failed = r.status == 'failed';
            return ListTile(
              leading: Icon(
                failed ? Icons.error_outline : Icons.schedule,
                color: failed ? t.red : t.amber2,
              ),
              title: Text('${r.op} · ${r.targetId}',
                  style: TextStyle(color: t.ink)),
              subtitle: Text(
                failed
                    ? 'failed after ${r.attempts} attempts: ${r.lastError ?? ''}'
                    : '${r.status} · ${r.attempts} attempts',
                style: TextStyle(color: t.inkFaint, fontSize: 12),
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
              ),
              trailing: Row(mainAxisSize: MainAxisSize.min, children: [
                if (failed)
                  IconButton(
                    icon: Icon(Icons.refresh, color: t.amber2),
                    tooltip: 'Retry',
                    onPressed: () => db.retryOutbox(r.id),
                  ),
                IconButton(
                  icon: Icon(Icons.delete_outline, color: t.slate4),
                  tooltip: 'Discard',
                  onPressed: () => db.discardOutbox(r.id),
                ),
              ]),
            );
          },
        );
      },
    );
  }
}
