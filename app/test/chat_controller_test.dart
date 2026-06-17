import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:ai_team_app_v2/state/chat_state.dart';

/// Fake ChatConnection backed by a broadcast StreamController.
class _FakeConnection implements ChatConnection {
  final _ctrl = StreamController<Map<String, dynamic>>.broadcast();
  bool closed = false;

  @override
  Stream<Map<String, dynamic>> get events => _ctrl.stream;

  @override
  void sendText(String text) {} // no-op for tests

  @override
  void close() {
    closed = true;
    if (!_ctrl.isClosed) _ctrl.close();
  }

  void drop() {
    if (!_ctrl.isClosed) _ctrl.addError(StateError('ws dropped'));
  }
}

void main() {
  group('ChatController reconnect', () {
    test('reconnects after WS drop using backoff', () async {
      int connectCount = 0;
      _FakeConnection? lastConn;

      final c = ChatController(
        null,
        'terry',
        connectionFactory: (_) {
          connectCount++;
          lastConn = _FakeConnection();
          return lastConn!;
        },
      );
      addTearDown(c.dispose);

      // Initial connect should have happened
      expect(connectCount, 1);
      expect(c.connected, isTrue);

      // Simulate a WS drop — triggers _onDrop → scheduleReconnect
      lastConn!.drop();
      await pumpEventQueue();
      expect(c.connected, isFalse);

      // Advance past the 1s backoff step
      await Future<void>.delayed(const Duration(milliseconds: 1100));
      expect(connectCount, 2);
      expect(c.connected, isTrue);
    });

    test('dispose cancels pending reconnect timer, no second connect', () async {
      int connectCount = 0;
      _FakeConnection? lastConn;

      final c = ChatController(
        null,
        'terry',
        connectionFactory: (_) {
          connectCount++;
          lastConn = _FakeConnection();
          return lastConn!;
        },
      );

      // Drop to schedule a reconnect timer
      lastConn!.drop();
      await pumpEventQueue();
      expect(c.connected, isFalse);

      // Dispose before the timer fires — must NOT reconnect
      c.dispose();
      await Future<void>.delayed(const Duration(milliseconds: 1200));
      expect(connectCount, 1);
    });
  });

  group('ChatController event handling', () {
    test('streamed assistant chunks accumulate into one bubble', () {
      final c = ChatController(null, 'terry'); // null gw → no WS
      c.debugHandleEvent({'type': 'assistant', 'text': 'Hel'});
      c.debugHandleEvent({'type': 'assistant', 'text': 'lo'});
      c.debugHandleEvent({'type': 'done'});
      expect(c.messages.length, 1);
      expect(c.messages.single.role, 'assistant');
      expect(c.messages.single.text, 'Hello');
      expect(c.messages.single.pending, isFalse);
      expect(c.sending, isFalse);
    });

    test('hive trace frames attach to the assistant bubble', () {
      final c = ChatController(null, 'terry');
      c.debugHandleEvent({'type': 'thought', 'text': 'thinking'});
      c.debugHandleEvent({'type': 'delegate', 'text': 'to coder'});
      c.debugHandleEvent({'type': 'assistant', 'text': 'done'});
      expect(c.messages.single.traces.length, 2);
      expect(c.messages.single.traces.first.kind, 'thought');
      expect(c.messages.single.text, 'done');
    });

    test('error frame sets error + clears sending', () {
      final c = ChatController(null, 'terry');
      c.debugHandleEvent({'type': 'error', 'message': 'boom'});
      expect(c.error, 'boom');
      expect(c.sending, isFalse);
    });
  });
}
