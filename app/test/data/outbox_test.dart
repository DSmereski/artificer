import 'package:drift/native.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:ai_team_app_v2/data/db/app_database.dart';

void main() {
  late AppDatabase db;

  setUp(() => db = AppDatabase(NativeDatabase.memory()));
  tearDown(() => db.close());

  test('outbox is FIFO by insertion order', () async {
    await db.enqueue('approve', 'T-1', '{}', nowMs: 100);
    await db.enqueue('reject', 'T-2', '{}', nowMs: 200);
    final first = await db.nextPending();
    expect(first!.op, 'approve');
    expect(first.targetId, 'T-1');
  });

  test('inflight rows are skipped by nextPending', () async {
    final id1 = await db.enqueue('approve', 'T-1', '{}', nowMs: 100);
    await db.enqueue('reject', 'T-2', '{}', nowMs: 200);
    await db.markInflight(id1);
    final next = await db.nextPending();
    expect(next!.targetId, 'T-2');
  });

  test('complete deletes the row', () async {
    final id = await db.enqueue('approve', 'T-1', '{}', nowMs: 100);
    await db.completeOutbox(id);
    expect(await db.nextPending(), isNull);
  });

  test('fail bumps attempts and goes failed after maxAttempts', () async {
    final id = await db.enqueue('approve', 'T-1', '{}', nowMs: 100);
    for (var i = 0; i < AppDatabase.maxAttempts; i++) {
      await db.failOutbox(id, 'boom $i');
    }
    expect(await db.nextPending(), isNull); // failed, not pending
    final rows = await db.watchOutbox().first;
    expect(rows.single.status, 'failed');
    expect(rows.single.attempts, AppDatabase.maxAttempts);
    expect(rows.single.lastError, 'boom ${AppDatabase.maxAttempts - 1}');
  });

  test('retry resets a failed row to pending', () async {
    final id = await db.enqueue('approve', 'T-1', '{}', nowMs: 100);
    for (var i = 0; i < AppDatabase.maxAttempts; i++) {
      await db.failOutbox(id, 'boom');
    }
    await db.retryOutbox(id);
    final next = await db.nextPending();
    expect(next!.id, id);
    expect(next.attempts, 0);
  });

  test('sync_state singleton lazily creates row 0', () async {
    final s = await db.syncState();
    expect(s.id, 0);
    await db.saveSyncState(lastPullJson: '{"tasks":"2026-06-11T00:00:00"}');
    final s2 = await db.syncState();
    expect(s2.lastPullJson, '{"tasks":"2026-06-11T00:00:00"}');
  });

  test('syncState is idempotent and preserves prior saves', () async {
    await db.syncState();
    await db.saveSyncState(lastSeenJson: '{"escalations":"2026-06-11"}');
    final again = await db.syncState();
    expect(again.id, 0);
    expect(again.lastSeenJson, '{"escalations":"2026-06-11"}');
  });
}
