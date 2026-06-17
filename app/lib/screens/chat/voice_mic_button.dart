import 'dart:io';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:record/record.dart';

import '../../api/gateway_client.dart';
import '../../theme/hive_palette.dart';
import '../../widgets/hive_motion.dart';

/// Tap-to-talk mic for the chat composer.
///
/// Records 16 kHz mono WAV, POSTs it to POST /v1/stt for transcription,
/// then feeds the transcript into the normal chat WS via [onSend]. This
/// gives the full Hive coordinator path (planner → helpers → synthesizer →
/// vault) rather than the lobotomised /v1/voice bypass.
///
/// The [onTranscript] callback is preserved for call-site backward
/// compatibility (e.g. if a caller wants to display the transcript
/// independently), but the chat WS reply is handled by the existing
/// streaming subscription — [onAssistant] is no longer called by this
/// widget and should be treated as deprecated.
///
/// Live end-to-end testing requires a gateway restart after the /v1/stt
/// route was deployed (the running process predates it). The unit-test
/// suite mocks [GatewayClient.transcribe] via a fake client.
class VoiceMicButton extends StatefulWidget {
  const VoiceMicButton({
    super.key,
    required this.gateway,
    required this.bot,
    required this.onSend,
    this.onTranscript,
    this.onAssistant,
  });

  final GatewayClient? gateway;
  final String bot;

  /// Called with the transcript text to route it through the chat WS.
  /// Wire this to [ChatController.send] so the full Hive coordinator path
  /// is invoked and the reply streams back over the existing chat WS.
  final void Function(String) onSend;

  /// Optional: called with the transcript text for additional display
  /// purposes. No longer used for injecting the user bubble — [onSend]
  /// handles both the bubble and the WS send atomically.
  final void Function(String)? onTranscript;

  /// Deprecated — the assistant reply now arrives via the chat WS stream.
  /// Retained for API backward compatibility; not called by this widget.
  final void Function(String)? onAssistant;

  @override
  State<VoiceMicButton> createState() => _VoiceMicButtonState();
}

class _VoiceMicButtonState extends State<VoiceMicButton> {
  final _recorder = AudioRecorder();
  bool _recording = false;
  bool _busy = false;

  @override
  void dispose() {
    _recorder.dispose();
    super.dispose();
  }

  Future<void> _toggle() async {
    if (widget.gateway == null) return;
    if (_recording) {
      await _stopAndSend();
    } else {
      await _start();
    }
  }

  Future<void> _start() async {
    try {
      if (!await _recorder.hasPermission()) return;
      final dir = Directory.systemTemp.createTempSync('hive_voice');
      final path = '${dir.path}/clip.wav';
      await _recorder.start(
        const RecordConfig(encoder: AudioEncoder.wav, sampleRate: 16000),
        path: path,
      );
      setState(() => _recording = true);
    } catch (_) {
      setState(() => _recording = false);
    }
  }

  Future<void> _stopAndSend() async {
    setState(() {
      _recording = false;
      _busy = true;
    });
    try {
      final path = await _recorder.stop();
      if (path == null) return;
      final bytes = await File(path).readAsBytes();
      await _transcribeAndSend(bytes);
    } catch (_) {
      // best-effort: silently swallow network/permission errors
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  /// POST [wav] to /v1/stt, then route the transcript through the normal
  /// chat WS via [widget.onSend]. The coordinator reply streams back over
  /// the existing chat WS subscription owned by ChatController.
  Future<void> _transcribeAndSend(Uint8List wav) async {
    final result = await widget.gateway!.transcribe(wav);
    final transcript = result.text.trim();
    if (transcript.isEmpty) return;
    // Notify any secondary listener (display-only; does NOT add a bubble).
    widget.onTranscript?.call(transcript);
    // Route through the chat WS — adds the user bubble + sends to coordinator.
    widget.onSend(transcript);
  }

  @override
  Widget build(BuildContext context) {
    final disabled = widget.gateway == null || _busy;

    if (_busy) {
      return const SizedBox(
        width: 40,
        height: 40,
        child: Center(
          child: SizedBox(
            width: 18,
            height: 18,
            child: CircularProgressIndicator(
              strokeWidth: 2,
              color: HivePalette.amber1,
            ),
          ),
        ),
      );
    }

    // Amber gradient mic button; glows when recording
    Widget micBtn = GestureDetector(
      onTap: disabled ? null : _toggle,
      child: Container(
        width: 40,
        height: 40,
        decoration: BoxDecoration(
          shape: BoxShape.circle,
          gradient: const LinearGradient(
            begin: Alignment.topLeft,
            end: Alignment.bottomRight,
            colors: [HivePalette.amber2, HivePalette.amberGlow],
          ),
          boxShadow: disabled
              ? null
              : [
                  BoxShadow(
                    color: HivePalette.amber1.withValues(alpha: 0.20),
                    blurRadius: 8,
                  ),
                ],
        ),
        child: Icon(
          _recording ? Icons.stop_rounded : Icons.mic_none_rounded,
          color: _recording ? HivePalette.red : HivePalette.inkOnAmber,
          size: 20,
        ),
      ),
    );

    if (!_recording) return micBtn;

    // Live glow around the mic while recording
    return SizedBox(
      width: 40,
      height: 40,
      child: LiveGlow(
        active: _recording,
        child: micBtn,
      ),
    );
  }

}
