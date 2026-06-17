import 'dart:convert';

import 'package:drift/native.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:ai_team_app_v2/data/db/app_database.dart';
import 'package:ai_team_app_v2/data/repositories/activity_repository.dart';
import 'package:ai_team_app_v2/data/repositories/chat_repository.dart';
import 'package:ai_team_app_v2/data/repositories/escalation_repository.dart';

void main() {
  late AppDatabase db;

  setUp(() => db = AppDatabase(NativeDatabase.memory()));
  tearDown(() => db.close());

  test('escalations: upsert, watch, optimistic resolve enqueues op', () async {
    final repo = EscalationRepository(db, nowMs: () => 1);
    await repo.replaceAll([
      {'slug': 'E-1', 'resolved': false, 'created_at': '2026-06-11T00:00:00'},
    ]);
    var list = await repo.watchEscalations().first;
    expect(list.single['slug'], 'E-1');
    await repo.resolveOptimistic('E-1');
    list = await repo.watchEscalations().first;
    expect(list.single['resolved'], true);
    final op = await db.nextPending();
    expect(op!.op, 'resolve_escalation');
    expect(op.targetId, 'E-1');
  });

  test('chat: append + watch per bot, send enqueues outbox', () async {
    // nowMs=5 so the optimistic send (ts '5') sorts AFTER the appended
    // message (ts '1') — watchMessages orders by ts ascending.
    final repo = ChatRepository(db, nowMs: () => 5);
    await repo.appendMessages('terry', [
      {'id': 'm1', 'role': 'assistant', 'text': 'hi', 'ts': '1'},
    ]);
    await repo.sendOptimistic('terry', 'hello there', localId: 'local-1');
    final msgs = await repo.watchMessages('terry').first;
    expect(msgs.length, 2);
    expect(msgs.last['text'], 'hello there');
    final op = await db.nextPending();
    expect(op!.op, 'send_chat');
    expect(jsonDecode(op.payloadJson)['text'], 'hello there');
  });

  test('activity: append caps the feed at 200 rows', () async {
    final repo = ActivityRepository(db);
    for (var i = 0; i < 210; i++) {
      await repo.append('event', {'n': i}, ts: '$i');
    }
    final feed = await repo.watchFeed().first;
    expect(feed.length, 200);
    expect(feed.first['payload']['n'], 209); // newest first
  });
}
