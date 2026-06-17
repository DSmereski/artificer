import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../state/session.dart';
import '../../theme/hive_tokens.dart';
import '../../widgets/state_views.dart';

final _jobsProvider = FutureProvider<List<Map<String, dynamic>>>((ref) async {
  final gw = ref.watch(gatewayClientProvider);
  if (gw == null) return const [];
  return gw.calendarJobs();
});

/// Calendar — scheduled jobs (list + delete). Create is a follow-up form;
/// the list + delete cover the common review case.
class CalendarScreen extends ConsumerWidget {
  const CalendarScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = Theme.of(context).extension<HiveTokens>()!;
    final jobs = ref.watch(_jobsProvider);
    return Scaffold(
      appBar: AppBar(
          title: const Text('Calendar'),
          backgroundColor: Colors.transparent,
          elevation: 0),
      body: jobs.when(
        loading: () => const LoadingView(),
        error: (e, _) => ErrorView(
            error: e.toString(), onRetry: () => ref.invalidate(_jobsProvider)),
        data: (list) => list.isEmpty
            ? const EmptyView(
                title: 'No scheduled jobs.',
                hint: 'Calendar fires show on the Home feed.',
                icon: Icons.event_outlined)
            : ListView.separated(
                padding: const EdgeInsets.symmetric(vertical: 8),
                itemCount: list.length,
                separatorBuilder: (_, _) => Divider(
                    height: 1, color: t.amber1.withValues(alpha: 0.08)),
                itemBuilder: (_, i) {
                  final j = list[i];
                  final when = (j['scheduled_at'] ?? '') as String;
                  final rec = (j['recurrence'] ?? 'none') as String;
                  return ListTile(
                    leading: Icon(Icons.event, color: t.amber1, size: 20),
                    title: Text((j['title'] ?? '') as String),
                    subtitle: Text(
                        '${when.replaceFirst("T", " ").split(".").first}'
                        '${rec != "none" ? " · $rec" : ""}',
                        style: TextStyle(color: t.slate4, fontSize: 11.5)),
                    trailing: IconButton(
                      icon: Icon(Icons.delete_outline,
                          size: 20, color: t.slate4),
                      onPressed: () async {
                        final gw = ref.read(gatewayClientProvider);
                        final id = (j['id'] ?? '').toString();
                        if (gw == null || id.isEmpty) return;
                        try {
                          await gw.deleteCalendarJob(id);
                          ref.invalidate(_jobsProvider);
                        } catch (e) {
                          if (context.mounted) {
                            ScaffoldMessenger.of(context).showSnackBar(
                              SnackBar(
                                  content: Text('Delete failed: $e')),
                            );
                          }
                        }
                      },
                    ),
                  );
                },
              ),
      ),
    );
  }
}
