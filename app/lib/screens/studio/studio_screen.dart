import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../state/session.dart';
import '../../theme/hive_tokens.dart';
import '../../widgets/state_views.dart';

final _recentProvider = FutureProvider<List<Map<String, dynamic>>>((ref) async {
  final gw = ref.watch(gatewayClientProvider);
  if (gw == null) return const [];
  return gw.recentImages();
});

/// Studio — recent image gallery + a one-line render submit.
class StudioScreen extends ConsumerStatefulWidget {
  const StudioScreen({super.key});

  @override
  ConsumerState<StudioScreen> createState() => _StudioScreenState();
}

class _StudioScreenState extends ConsumerState<StudioScreen> {
  final _prompt = TextEditingController();
  bool _submitting = false;

  @override
  void dispose() {
    _prompt.dispose();
    super.dispose();
  }

  Future<void> _render() async {
    final gw = ref.read(gatewayClientProvider);
    final p = _prompt.text.trim();
    if (gw == null || p.isEmpty) return;
    setState(() => _submitting = true);
    try {
      await gw.submitRender(p);
      _prompt.clear();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(content: Text('Render queued — watch Home feed')));
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text('Render failed: $e')));
      }
    } finally {
      if (mounted) setState(() => _submitting = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context).extension<HiveTokens>()!;
    final gw = ref.read(gatewayClientProvider);
    final recent = ref.watch(_recentProvider);
    return Scaffold(
      appBar: AppBar(
          title: const Text('Studio'),
          backgroundColor: Colors.transparent,
          elevation: 0),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(12, 8, 12, 8),
            child: Row(children: [
              Expanded(
                child: TextField(
                  controller: _prompt,
                  textInputAction: TextInputAction.send,
                  onSubmitted: (_) => _render(),
                  decoration: const InputDecoration(
                      hintText: 'Describe an image…',
                      border: OutlineInputBorder(),
                      isDense: true),
                ),
              ),
              const SizedBox(width: 8),
              IconButton.filled(
                icon: _submitting
                    ? const SizedBox(
                        width: 18,
                        height: 18,
                        child: CircularProgressIndicator(strokeWidth: 2))
                    : const Icon(Icons.auto_awesome),
                onPressed: _submitting ? null : _render,
              ),
            ]),
          ),
          Expanded(
            child: recent.when(
              loading: () => const LoadingView(),
              error: (e, _) => ErrorView(
                  error: e.toString(),
                  onRetry: () => ref.invalidate(_recentProvider)),
              data: (imgs) {
                final done = imgs
                    .where((m) =>
                        (m['result_ids'] as List?)?.isNotEmpty ?? false)
                    .toList();
                if (done.isEmpty) {
                  return const EmptyView(
                      title: 'No images yet.',
                      hint: 'Queue a render above.',
                      icon: Icons.image_outlined);
                }
                return GridView.builder(
                  padding: const EdgeInsets.all(8),
                  gridDelegate:
                      const SliverGridDelegateWithFixedCrossAxisCount(
                          crossAxisCount: 2,
                          mainAxisSpacing: 8,
                          crossAxisSpacing: 8),
                  itemCount: done.length,
                  itemBuilder: (_, i) {
                    final mid =
                        (done[i]['result_ids'] as List).first.toString();
                    return ClipRRect(
                      borderRadius: BorderRadius.circular(10),
                      child: Image.network(
                        gw!.mediaUrl(mid),
                        headers: gw.mediaHeaders,
                        fit: BoxFit.cover,
                        errorBuilder: (_, _, _) => Container(
                            color: t.slate1,
                            child: Icon(Icons.broken_image_outlined,
                                color: t.slate4)),
                      ),
                    );
                  },
                );
              },
            ),
          ),
        ],
      ),
    );
  }
}
