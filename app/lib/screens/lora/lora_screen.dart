import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../state/session.dart';
import '../../theme/hive_tokens.dart';
import '../../widgets/state_views.dart';

final _lorasProvider = FutureProvider<List<Map<String, dynamic>>>((ref) async {
  final gw = ref.watch(gatewayClientProvider);
  if (gw == null) return const [];
  return gw.loras();
});

/// LoRA catalog + paste-URL import. Import progress lands on the Home
/// feed (import_done events).
class LoraScreen extends ConsumerStatefulWidget {
  const LoraScreen({super.key});

  @override
  ConsumerState<LoraScreen> createState() => _LoraScreenState();
}

class _LoraScreenState extends ConsumerState<LoraScreen> {
  final _url = TextEditingController();
  bool _importing = false;

  @override
  void dispose() {
    _url.dispose();
    super.dispose();
  }

  Future<void> _import() async {
    final gw = ref.read(gatewayClientProvider);
    final u = _url.text.trim();
    if (gw == null || u.isEmpty) return;
    setState(() => _importing = true);
    try {
      await gw.startLoraImport(u);
      _url.clear();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(content: Text('Import queued — watch Home feed')));
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text('Import failed: $e')));
      }
    } finally {
      if (mounted) setState(() => _importing = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context).extension<HiveTokens>()!;
    final loras = ref.watch(_lorasProvider);
    return Scaffold(
      appBar: AppBar(
          title: const Text('LoRAs'),
          backgroundColor: Colors.transparent,
          elevation: 0),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(12, 8, 12, 8),
            child: Row(children: [
              Expanded(
                child: TextField(
                  controller: _url,
                  textInputAction: TextInputAction.send,
                  onSubmitted: (_) => _import(),
                  decoration: const InputDecoration(
                      hintText: 'Paste a Civitai/HF LoRA URL…',
                      border: OutlineInputBorder(),
                      isDense: true),
                ),
              ),
              const SizedBox(width: 8),
              IconButton.filled(
                icon: _importing
                    ? const SizedBox(
                        width: 18,
                        height: 18,
                        child: CircularProgressIndicator(strokeWidth: 2))
                    : const Icon(Icons.download),
                onPressed: _importing ? null : _import,
              ),
            ]),
          ),
          Expanded(
            child: loras.when(
              loading: () => const LoadingView(),
              error: (e, _) => ErrorView(
                  error: e.toString(),
                  onRetry: () => ref.invalidate(_lorasProvider)),
              data: (list) => list.isEmpty
                  ? const EmptyView(
                      title: 'No LoRAs installed.',
                      hint: 'Paste a URL above to import.',
                      icon: Icons.model_training_outlined)
                  : ListView.separated(
                      itemCount: list.length,
                      separatorBuilder: (_, _) => Divider(
                          height: 1, color: t.amber1.withValues(alpha: 0.08)),
                      itemBuilder: (_, i) {
                        final l = list[i];
                        return ListTile(
                          dense: true,
                          title: Text((l['alias'] ?? l['main_file'] ?? '')
                              as String),
                          subtitle: Text(
                              '${l['pipeline'] ?? ''} · ${l['category'] ?? ''}',
                              style:
                                  TextStyle(color: t.slate4, fontSize: 11.5)),
                          trailing: (l['nsfw'] == true)
                              ? Text('nsfw',
                                  style: TextStyle(
                                      color: const Color(0xFFE08B8B),
                                      fontSize: 10))
                              : null,
                        );
                      },
                    ),
            ),
          ),
        ],
      ),
    );
  }
}
