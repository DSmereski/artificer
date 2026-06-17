import 'dart:convert';

import 'package:drift/native.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:ai_team_app_v2/data/db/app_database.dart';
import 'package:ai_team_app_v2/data/repositories/board_repository.dart';

Map<String, dynamic> taskJson(String slug,
        {String status = 'backlog', String title = 'Title'}) =>
    {
      'slug': slug,
      'title': title,
      'body': 'b',
      'status': status,
      'project_slug': 'proj',
      'assignee': 'hive',
      'created_by': 'owner',
      'priority': 'medium',
      'acceptance_criteria': [],
      'files_of_interest': [],
      'attempt_count': 0,
      'hive_tokens': 0,
      'claude_tokens': 0,
      'review_by': null,
      'smoke_ok': null,
      'verify_results': {},
      'created_at': '2026-06-11T00:00:00',
      'updated_at': '2026-06-11T00:00:00',
    };

void main() {
  late AppDatabase db;
  late BoardRepository repo;

  setUp(() {
    db = AppDatabase(NativeDatabase.memory());
    repo = BoardRepository(db, nowMs: () => 1000);
  });
  tearDown(() => db.close());

  test('upsertTasks inserts then watchTasks streams typed models', () async {
    await repo.upsertTasks([taskJson('T-1'), taskJson('T-2', status: 'done')]);
    final tasks = await repo.watchTasks().first;
    expect(tasks.length, 2);
    expect(tasks.map((t) => t.slug), containsAll(['T-1', 'T-2']));
    expect(tasks.firstWhere((t) => t.slug == 'T-2').status, 'done');
  });

  test('gateway upsert overwrites optimistic dirty row (gateway wins)',
      () async {
    await repo.upsertTasks([taskJson('T-1', status: 'backlog')]);
    await repo.moveTaskOptimistic('T-1', 'ready'); // dirty local guess
    var tasks = await repo.watchTasks().first;
    expect(tasks.single.status, 'ready');
    // Authoritative value arrives — replaces the guess, clears dirty.
    await repo.upsertTasks([taskJson('T-1', status: 'in_progress')]);
    tasks = await repo.watchTasks().first;
    expect(tasks.single.status, 'in_progress');
    final raw = await db.select(db.taskRows).get();
    expect(raw.single.dirty, isFalse);
  });

  test('replaceAllTasks drops rows missing from the snapshot', () async {
    await repo.upsertTasks([taskJson('T-1'), taskJson('T-2')]);
    await repo.replaceAllTasks([taskJson('T-2')]);
    final tasks = await repo.watchTasks().first;
    expect(tasks.single.slug, 'T-2');
  });

  test('moveTaskOptimistic enqueues a move op in the outbox', () async {
    await repo.upsertTasks([taskJson('T-1')]);
    await repo.moveTaskOptimistic('T-1', 'ready');
    final pending = await db.nextPending();
    expect(pending!.op, 'move');
    expect(pending.targetId, 'T-1');
    expect(jsonDecode(pending.payloadJson)['to_status'], 'ready');
  });
}
