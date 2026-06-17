import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../state/session.dart';
import '../../theme/hive_tokens.dart';
import 'scan_pair_screen.dart';

/// Minimal connect screen for v2 core — gateway URL + token. (Full
/// QR/code pairing flow is a follow-up; this unblocks a real connection.)
class ConnectScreen extends ConsumerStatefulWidget {
  const ConnectScreen({super.key});

  @override
  ConsumerState<ConnectScreen> createState() => _ConnectScreenState();
}

class _ConnectScreenState extends ConsumerState<ConnectScreen> {
  final _url = TextEditingController(text: 'http://127.0.0.1:8766');
  final _token = TextEditingController();
  bool _busy = false;

  @override
  void dispose() {
    _url.dispose();
    _token.dispose();
    super.dispose();
  }

  Future<void> _connect() async {
    if (_url.text.trim().isEmpty || _token.text.trim().isEmpty) return;
    setState(() => _busy = true);
    try {
      await ref
          .read(sessionProvider.notifier)
          .connect(_url.text, _token.text);
    } catch (e) {
      if (!mounted) return;
      setState(() => _busy = false);
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Connect failed: $e')),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context).extension<HiveTokens>()!;
    return Scaffold(
      body: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 420),
          child: Padding(
            padding: const EdgeInsets.all(24),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Icon(Icons.hive_outlined, color: t.amber1, size: 40),
                const SizedBox(height: 12),
                Text('Hive v2',
                    textAlign: TextAlign.center,
                    style: TextStyle(
                        color: t.ink,
                        fontSize: 22,
                        fontWeight: FontWeight.w700)),
                const SizedBox(height: 24),
                TextField(
                  controller: _url,
                  decoration: const InputDecoration(
                      labelText: 'Gateway URL',
                      border: OutlineInputBorder()),
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: _token,
                  obscureText: true,
                  decoration: const InputDecoration(
                      labelText: 'Device token or pairing code',
                      helperText: 'Paste a token, or type a pairing code',
                      border: OutlineInputBorder()),
                ),
                const SizedBox(height: 20),
                FilledButton(
                  onPressed: _busy ? null : _connect,
                  child: Text(_busy ? 'Connecting…' : 'Connect'),
                ),
                const SizedBox(height: 12),
                Row(children: [
                  Expanded(child: Divider(color: t.slate3)),
                  Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 10),
                    child: Text('or', style: TextStyle(color: t.slate4)),
                  ),
                  Expanded(child: Divider(color: t.slate3)),
                ]),
                const SizedBox(height: 12),
                OutlinedButton.icon(
                  icon: const Icon(Icons.qr_code_scanner),
                  label: const Text('Scan pairing QR'),
                  onPressed: () => Navigator.of(context).push(
                    MaterialPageRoute<void>(
                        builder: (_) => const ScanPairScreen()),
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
