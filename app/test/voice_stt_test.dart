import 'dart:async';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:ai_team_app_v2/api/gateway_client.dart';
import 'package:ai_team_app_v2/state/chat_state.dart';

// ─────────────────────────────────────────────────────────────────────────────
// Minimal fake GatewayClient — only the surfaces under test.
// The real GatewayClient requires real HTTP + WS transports; we only need
// to exercise the SttResult parse path and the rewired voice→chat flow.
// ─────────────────────────────────────────────────────────────────────────────

class _FakeGatewayClient extends GatewayClient {
  _FakeGatewayClient({
    required this.sttResponse,
    this.sttError,
  }) : super(baseUrl: 'http://localhost', token: 'test');

  final SttResult sttResponse;
  final Object? sttError;
  final List<Uint8List> transcribeCalls = [];

  @override
  Future<SttResult> transcribe(Uint8List bytes) async {
    transcribeCalls.add(bytes);
    if (sttError != null) throw sttError!;
    return sttResponse;
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Fake ChatConnection (mirrors _FakeConnection in chat_controller_test.dart)
// ─────────────────────────────────────────────────────────────────────────────

class _FakeChatConnection implements ChatConnection {
  final _ctrl = StreamController<Map<String, dynamic>>.broadcast();
  final List<String> sent = [];

  @override
  Stream<Map<String, dynamic>> get events => _ctrl.stream;

  @override
  void sendText(String text) => sent.add(text);

  @override
  void close() {
    if (!_ctrl.isClosed) _ctrl.close();
  }

  void emit(Map<String, dynamic> frame) => _ctrl.add(frame);
}

// ─────────────────────────────────────────────────────────────────────────────
// Tests
// ─────────────────────────────────────────────────────────────────────────────

void main() {
  group('SttResult.fromJson', () {
    test('parses text and duration_s', () {
      final r = SttResult.fromJson({'text': 'hello world', 'duration_s': 1.5});
      expect(r.text, 'hello world');
      expect(r.durationSeconds, 1.5);
    });

    test('handles missing duration_s', () {
      final r = SttResult.fromJson({'text': 'no duration'});
      expect(r.text, 'no duration');
      expect(r.durationSeconds, isNull);
    });

    test('handles missing text field as empty string', () {
      final r = SttResult.fromJson({});
      expect(r.text, '');
    });

    test('handles integer duration_s (num → double)', () {
      final r = SttResult.fromJson({'text': 'hi', 'duration_s': 2});
      expect(r.durationSeconds, 2.0);
    });
  });

  group('GatewayClient.transcribe (fake)', () {
    test('sends bytes and returns SttResult', () async {
      final fake = _FakeGatewayClient(
        sttResponse: const SttResult(text: 'launch hive', durationSeconds: 0.9),
      );
      final wav = Uint8List.fromList([0x52, 0x49, 0x46, 0x46]); // RIFF header
      final result = await fake.transcribe(wav);
      expect(result.text, 'launch hive');
      expect(fake.transcribeCalls, hasLength(1));
      expect(fake.transcribeCalls.single, wav);
    });

    test('propagates exceptions from transcribe', () async {
      final fake = _FakeGatewayClient(
        sttResponse: const SttResult(text: ''),
        sttError: GatewayException('stt unavailable', status: 503),
      );
      expect(
        () => fake.transcribe(Uint8List(0)),
        throwsA(isA<GatewayException>()),
      );
    });
  });

  group('Voice → chat WS rewire via ChatController', () {
    late _FakeChatConnection conn;
    late ChatController controller;

    setUp(() {
      conn = _FakeChatConnection();
      controller = ChatController(
        null,
        'terry',
        connectionFactory: (_) => conn,
      );
      addTearDown(controller.dispose);
    });

    test('send() adds user bubble and routes text over the chat WS', () {
      controller.send('status check');
      expect(controller.messages, hasLength(1));
      expect(controller.messages.single.role, 'user');
      expect(controller.messages.single.text, 'status check');
      expect(conn.sent, ['status check']);
    });

    test('coordinator reply streams back and lands in assistant bubble', () async {
      controller.send('plan the sprint');
      conn.emit({'type': 'thought', 'text': 'thinking about plan'});
      conn.emit({'type': 'assistant', 'text': 'Here is the plan.'});
      conn.emit({'type': 'done'});
      await pumpEventQueue();
      // messages: [user, assistant]
      expect(controller.messages, hasLength(2));
      final assistant = controller.messages.last;
      expect(assistant.role, 'assistant');
      expect(assistant.text, 'Here is the plan.');
      expect(assistant.traces, hasLength(1));
      expect(assistant.traces.single.kind, 'thought');
      expect(controller.sending, isFalse);
    });

    test('voice send via onSend=c.send adds user bubble exactly once', () {
      // Simulate what VoiceMicButton._transcribeAndSend does:
      // calls widget.onSend(transcript) which is wired to c.send.
      // Must NOT add an extra bubble from onTranscript.
      controller.send('what is the task queue');
      expect(controller.messages.where((m) => m.isUser), hasLength(1));
      expect(conn.sent, hasLength(1));
    });

    test('empty transcript is ignored by ChatController.send', () {
      controller.send('   ');
      expect(controller.messages, isEmpty);
      expect(conn.sent, isEmpty);
    });

    test('transcript whitespace is trimmed before send', () {
      controller.send('  deploy now  ');
      expect(controller.messages.single.text, 'deploy now');
      expect(conn.sent.single, 'deploy now'); // ChatController.send trims before passing to WS
    });
  });

  group('Backward-compat: addVoiceTranscript / addVoiceReply still work', () {
    test('addVoiceTranscript appends a user bubble without touching WS', () {
      final conn = _FakeChatConnection();
      final controller = ChatController(
        null,
        'terry',
        connectionFactory: (_) => conn,
      );
      addTearDown(controller.dispose);

      controller.addVoiceTranscript('legacy voice text');
      expect(controller.messages.single.role, 'user');
      expect(controller.messages.single.text, 'legacy voice text');
      expect(conn.sent, isEmpty); // no WS send
    });

    test('addVoiceReply appends an assistant bubble without touching WS', () {
      final conn = _FakeChatConnection();
      final controller = ChatController(
        null,
        'terry',
        connectionFactory: (_) => conn,
      );
      addTearDown(controller.dispose);

      controller.addVoiceReply('legacy assistant reply');
      expect(controller.messages.single.role, 'assistant');
      expect(conn.sent, isEmpty);
    });
  });
}
