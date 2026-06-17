import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../state/session.dart';
import '../db/app_database.dart';
import '../db/connection.dart';
import '../repositories/activity_repository.dart';
import '../repositories/board_repository.dart';
import '../repositories/chat_repository.dart';
import '../repositories/escalation_repository.dart';
import 'gateway_adapter.dart';
import 'sync_service.dart';

int _now() => DateTime.now().millisecondsSinceEpoch;

final appDatabaseProvider = Provider<AppDatabase>((ref) {
  final db = AppDatabase(openAppConnection());
  ref.onDispose(db.close);
  return db;
});

final boardRepositoryProvider = Provider<BoardRepository>(
    (ref) => BoardRepository(ref.watch(appDatabaseProvider), nowMs: _now));

final escalationRepositoryProvider = Provider<EscalationRepository>((ref) =>
    EscalationRepository(ref.watch(appDatabaseProvider), nowMs: _now));

final chatRepositoryProvider = Provider<ChatRepository>(
    (ref) => ChatRepository(ref.watch(appDatabaseProvider), nowMs: _now));

final activityRepositoryProvider = Provider<ActivityRepository>(
    (ref) => ActivityRepository(ref.watch(appDatabaseProvider)));

/// Null until a session (gateway client) exists. Recreated on re-pair.
final syncServiceProvider = Provider<SyncService?>((ref) {
  // Sync must keep running while paired even if no screen is currently
  // watching the board — otherwise navigating away would GC the loop.
  ref.keepAlive();
  final gw = ref.watch(gatewayClientProvider);
  if (gw == null) return null;
  final sync = SyncService(
    db: ref.watch(appDatabaseProvider),
    gateway: GatewayAdapter(gw),
    board: ref.watch(boardRepositoryProvider),
    escalations: ref.watch(escalationRepositoryProvider),
    chat: ref.watch(chatRepositoryProvider),
    activity: ref.watch(activityRepositoryProvider),
  );
  sync.start();
  ref.onDispose(sync.dispose);
  return sync;
});

/// Live connection phase for the status chip. Grey when no session.
final syncPhaseProvider = StreamProvider<SyncPhase>((ref) {
  final sync = ref.watch(syncServiceProvider);
  if (sync == null) return Stream.value(SyncPhase.disconnected);
  return sync.phaseStream;
});

/// Queued outbox row count (status chip + pending sheet badge).
final queuedCountProvider = StreamProvider<int>(
    (ref) => ref.watch(appDatabaseProvider).watchQueuedCount());
