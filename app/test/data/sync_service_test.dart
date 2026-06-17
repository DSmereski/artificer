import 'package:drift/native.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:ai_team_app_v2/data/db/app_database.dart';
import 'package:ai_team_app_v2/data/repositories/board_repository.dart';
import 'package:ai_team_app_v2/data/sync/sync_service.dart';

import 'board_repository_test.dart' show taskJson;
import 'fake_gateway.dart';

void main() {
  late AppDatabase db;
  late FakeGateway gw;
  late BoardRepository board;
  late SyncService sync;

  setUp(() {
    db = AppDatabase(NativeDatabase.memory());
    gw = FakeGateway();
    board = BoardRepository(db, nowMs: () => 1000);
    sync = SyncService(db: db, gateway: gw, board: board);
  });
  tearDown(() async {
    sync.dispose();
    await db.close();
  });

  test('connectOnce: disconnected -> hydrating -> live, DB hydrated',
      () async {
    gw.boardState = {
      'tasks': [taskJson('T-1')],
      'projects': [
        {'slug': 'proj', 'name': 'Proj', 'enabled': true,
         'push_allowed': false, 'parallel': false},
      ],
    };
    final phases = <SyncPhase>[];
    sync.phaseStream.listen(phases.add);
    final ok = await sync.connectOnce();
    expect(ok, isTrue);
    expect(sync.phase, SyncPhase.live);
    expect(phases, [SyncPhase.hydrating, SyncPhase.live]);
    final tasks = await board.watchTasks().first;
    expect(tasks.single.slug, 'T-1');
  });

  test('connectOnce returns false and stays disconnected when offline',
      () async {
    gw.online = false;
    final ok = await sync.connectOnce();
    expect(ok, isFalse);
    expect(sync.phase, SyncPhase.disconnected);
  });

  test('board WS frame triggers a board re-pull', () async {
    await sync.connectOnce();
    gw.boardState = {'tasks': [taskJson('T-9')], 'projects': []};
    gw.emit({'type': 'task_progress', 'slug': 'T-9'});
    await pumpEventQueue();
    final tasks = await board.watchTasks().first;
    expect(tasks.single.slug, 'T-9');
  });

  test('WS drop moves phase to disconnected', () async {
    await sync.connectOnce();
    gw.online = false; // so the auto-reconnect attempt fails too
    gw.dropConnection();
    await pumpEventQueue();
    expect(sync.phase, SyncPhase.disconnected);
  });

  test('drainOutbox executes ops FIFO and deletes them', () async {
    await board.upsertTasks([taskJson('T-1')]);
    await board.moveTaskOptimistic('T-1', 'ready');
    await board.assignTaskOptimistic('T-1', 'claude-code');
    await sync.connectOnce();
    await sync.drainOutbox();
    expect(gw.calls, ['move:T-1:ready', 'assign:T-1:claude-code']);
    expect(await db.nextPending(), isNull);
  });

  test('drainOutbox failure keeps the row pending with the error', () async {
    // Connect FIRST (its drain runs on an empty outbox), then go offline
    // and enqueue — otherwise connectOnce would drain the op while online.
    await sync.connectOnce();
    await board.upsertTasks([taskJson('T-1')]);
    gw.online = false;
    await board.moveTaskOptimistic('T-1', 'ready');
    await sync.drainOutbox();
    final row = await db.nextPending();
    expect(row, isNotNull);
    expect(row!.attempts, 1);
    expect(row.lastError, contains('offline'));
  });

  test('unknown outbox op is failed, not retried forever', () async {
    await db.enqueue('bogus_op', 'X', '{}', nowMs: 1);
    await sync.connectOnce();
    await sync.drainOutbox();
    final rows = await db.watchOutbox().first;
    expect(rows.single.status, 'failed');
  });

  test('burst of board frames triggers exactly one boardStateRaw re-pull',
      () async {
    await sync.connectOnce();
    final callsAfterConnect = gw.boardStateRawCalls; // 1 from hydrate
    // 5 board frames arrive simultaneously; _repulling guard must collapse
    // them so only ONE boardStateRaw() HTTP call is made.
    for (var i = 0; i < 5; i++) {
      gw.emit({'type': 'task_progress', 'slug': 'T-$i'});
    }
    await pumpEventQueue();
    expect(gw.boardStateRawCalls, callsAfterConnect + 1);
  });

  test('frameStream broadcasts every WS frame to subscribers', () async {
    await sync.connectOnce();
    final received = <Map<String, dynamic>>[];
    final frameSub = sync.frameStream.listen(received.add);
    addTearDown(frameSub.cancel);

    gw.emit({'type': 'hive_turn_done', 'preview': 'hello'});
    gw.emit({'type': 'scout_alert', 'message': 'disk full'});
    await pumpEventQueue();

    expect(received.length, 2);
    expect(received[0]['type'], 'hive_turn_done');
    expect(received[1]['type'], 'scout_alert');
  });

  test('burst of failing re-pulls collapses to a single disconnect', () async {
    await sync.connectOnce();
    final subsAfterConnect = gw.eventsSubscriptions;
    gw.online = false; // re-pulls will now throw
    // 5 board frames arrive in a burst; each fires an unawaited _repullBoard
    // that will fail. Idempotent _onDrop must NOT spawn 5 reconnects.
    for (var i = 0; i < 5; i++) {
      gw.emit({'type': 'task_progress', 'slug': 'T-$i'});
    }
    await pumpEventQueue();
    expect(sync.phase, SyncPhase.disconnected);
    // No supervisor loop is running (we called connectOnce directly, not
    // start()), so there must be NO new events() subscription from a
    // spawned loop.
    expect(gw.eventsSubscriptions, subsAfterConnect);
  });

  test('heartbeat re-pull failure (simulated) triggers _onDrop', () async {
    // Simulate what the 30 s heartbeat timer does: calls _repullBoard().
    // When the gateway is unreachable while phase=live the pull throws,
    // _onDrop() fires, and the supervisor (or caller) sees disconnected.
    await sync.connectOnce();
    expect(sync.phase, SyncPhase.live);
    gw.online = false; // gateway unreachable (NAT/LTE drop)
    // Directly emit a board frame — _onFrame fires unawaited(_repullBoard())
    // which is the same code path the heartbeat timer uses.
    gw.emit({'type': 'task_progress', 'slug': 'T-hb'});
    await pumpEventQueue();
    expect(sync.phase, SyncPhase.disconnected);
  });

  test('supervisor loop reconnects exactly once after a drop', () async {
    gw.boardState = {'tasks': [], 'projects': []};
    sync.start();
    await pumpEventQueue();
    expect(sync.phase, SyncPhase.live);
    final subsWhenLive = gw.eventsSubscriptions; // 1
    gw.dropConnection(); // WS error -> _onDrop -> wake supervisor -> reconnect
    await pumpEventQueue();
    await pumpEventQueue();
    await pumpEventQueue();
    expect(sync.phase, SyncPhase.live); // back to live
    // Exactly one extra subscription (the reconnect), not several.
    expect(gw.eventsSubscriptions, subsWhenLive + 1);
  });

  test('refresh during backoff wakes the supervisor without a parallel connect',
      () async {
    gw.online = false; // first connect attempts fail -> supervisor backs off
    gw.boardState = {'tasks': [], 'projects': []};
    sync.start();
    await pumpEventQueue();
    expect(sync.phase, SyncPhase.disconnected);
    // hydrate() throws before gateway.events() is called, so no subscriptions
    // are counted during failed attempts.
    final subsWhileBackoff = gw.eventsSubscriptions; // 0
    gw.online = true; // network restored
    await sync.refresh(); // must wake the parked loop, not spawn a 2nd connect
    await pumpEventQueue();
    await pumpEventQueue();
    expect(sync.phase, SyncPhase.live);
    // Exactly one successful subscription beyond the failed-attempt count.
    expect(gw.eventsSubscriptions, subsWhileBackoff + 1);
  });
}
