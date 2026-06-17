import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:mobile_scanner/mobile_scanner.dart';

import '../../auth/pairing.dart';
import '../../state/session.dart';

/// Scan the PC's pairing QR (`ai-team://pair?url=&code=`) → complete
/// pairing → store the session. Mirrors v1's QR pairing.
class ScanPairScreen extends ConsumerStatefulWidget {
  const ScanPairScreen({super.key});

  @override
  ConsumerState<ScanPairScreen> createState() => _ScanPairScreenState();
}

class _ScanPairScreenState extends ConsumerState<ScanPairScreen> {
  final _controller = MobileScannerController();
  bool _handling = false;
  String? _error;

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  Future<void> _onDetect(BarcodeCapture cap) async {
    if (_handling) return;
    final raw = cap.barcodes
        .map((b) => b.rawValue)
        .firstWhere((v) => v != null && v.isNotEmpty, orElse: () => null);
    if (raw == null) return;
    final payload = PairPayload.tryParse(raw);
    if (payload == null) {
      setState(() => _error = 'Not a Hive pairing QR');
      return;
    }
    setState(() {
      _handling = true;
      _error = null;
    });
    try {
      final token = await completePairing(
          gatewayUrl: payload.gatewayUrl, code: payload.code);
      await ref
          .read(sessionProvider.notifier)
          .connect(payload.gatewayUrl, token);
      // session change routes to AppShell automatically.
    } catch (e) {
      if (mounted) {
        setState(() {
          _handling = false;
          _error = e.toString();
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Scan pairing QR')),
      body: Stack(
        children: [
          MobileScanner(controller: _controller, onDetect: _onDetect),
          // Reticle.
          Center(
            child: Container(
              width: 240,
              height: 240,
              decoration: BoxDecoration(
                border: Border.all(color: Colors.white70, width: 2),
                borderRadius: BorderRadius.circular(16),
              ),
            ),
          ),
          Positioned(
            left: 0,
            right: 0,
            bottom: 32,
            child: Column(
              children: [
                if (_handling)
                  const CircularProgressIndicator()
                else
                  const Text('Point at the pairing QR on your PC',
                      style: TextStyle(color: Colors.white)),
                if (_error != null)
                  Padding(
                    padding: const EdgeInsets.all(8),
                    child: Text(_error!,
                        style: const TextStyle(color: Color(0xFFE08B8B))),
                  ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
