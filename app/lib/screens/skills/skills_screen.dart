import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../state/session.dart';
import '../../theme/hive_tokens.dart';
import '../../widgets/state_views.dart';
import 'author_skill_screen.dart';

/// Skills browser — the bot's authored capabilities (/v1/skills). v1
/// never surfaced these. Read-only list + detail for now; authoring
/// (POST /v1/skills) is a follow-up.
final _skillsProvider = FutureProvider<List<Map<String, dynamic>>>((ref) async {
  final gw = ref.watch(gatewayClientProvider);
  if (gw == null) return const [];
  return gw.skills();
});

class SkillsScreen extends ConsumerWidget {
  const SkillsScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = Theme.of(context).extension<HiveTokens>()!;
    final skills = ref.watch(_skillsProvider);
    return Scaffold(
      appBar: AppBar(
          title: const Text('Skills'),
          backgroundColor: Colors.transparent,
          elevation: 0),
      floatingActionButton: FloatingActionButton.extended(
        icon: const Icon(Icons.add),
        label: const Text('New skill'),
        onPressed: () async {
          final created = await Navigator.of(context).push<bool>(
              MaterialPageRoute(builder: (_) => const AuthorSkillScreen()));
          if (created == true) ref.invalidate(_skillsProvider);
        },
      ),
      body: skills.when(
        loading: () => const LoadingView(),
        error: (e, _) => ErrorView(
            error: e.toString(), onRetry: () => ref.invalidate(_skillsProvider)),
        data: (list) => list.isEmpty
            ? const EmptyView(
                title: 'No skills yet.',
                hint: 'The bot\'s authored capabilities appear here.',
                icon: Icons.auto_awesome_outlined)
            : ListView.separated(
                padding: const EdgeInsets.symmetric(vertical: 8),
                itemCount: list.length,
                separatorBuilder: (_, _) => Divider(
                    height: 1, color: t.amber1.withValues(alpha: 0.08)),
                itemBuilder: (_, i) {
                  final s = list[i];
                  return ListTile(
                    leading: Icon(Icons.auto_awesome_outlined,
                        color: t.amber1, size: 20),
                    title: Text((s['name'] ?? '') as String),
                    subtitle: Text((s['description'] ?? '') as String,
                        maxLines: 2, overflow: TextOverflow.ellipsis,
                        style: TextStyle(color: t.slate4, fontSize: 11.5)),
                    trailing: (s['read_only'] == true)
                        ? Icon(Icons.lock_outline, size: 16, color: t.slate4)
                        : null,
                    onTap: () => showModalBottomSheet<void>(
                      context: context,
                      showDragHandle: true,
                      builder: (_) => _SkillSheet(skill: s, tokens: t),
                    ),
                  );
                },
              ),
      ),
    );
  }
}

class _SkillSheet extends StatelessWidget {
  const _SkillSheet({required this.skill, required this.tokens});
  final Map<String, dynamic> skill;
  final HiveTokens tokens;

  @override
  Widget build(BuildContext context) {
    final body = (skill['body'] ?? skill['description'] ?? '') as String;
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 0, 16, 24),
      child: SingleChildScrollView(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text((skill['name'] ?? '') as String,
                style: TextStyle(
                    color: tokens.ink,
                    fontSize: 18,
                    fontWeight: FontWeight.w700)),
            const SizedBox(height: 10),
            SelectableText(body,
                style: TextStyle(color: tokens.ink, fontSize: 13, height: 1.4)),
          ],
        ),
      ),
    );
  }
}
