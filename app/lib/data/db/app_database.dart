// Offline-first spine: local SQLite is the single read source.
// Gateway stays authoritative — rows here are cache + optimistic overlay.
// Typed columns cover what the UI queries; `payloadJson` carries the full
// gateway JSON so models rehydrate via their existing fromJson factories.
import 'package:drift/drift.dart';

part 'app_database.g.dart';

class TaskRows extends Table {
  TextColumn get slug => text()();
  TextColumn get title => text()();
  TextColumn get status => text()();
  TextColumn get projectSlug => text()();
  TextColumn get assignee => text()();
  TextColumn get priority => text()();
  TextColumn get payloadJson => text()();
  TextColumn get updatedAt => text()(); // gateway ISO string, lexicographic-sortable
  BoolColumn get dirty => boolean().withDefault(const Constant(false))();
  BoolColumn get pendingDelete =>
      boolean().withDefault(const Constant(false))();

  @override
  Set<Column> get primaryKey => {slug};
}

class ProjectRows extends Table {
  TextColumn get slug => text()();
  TextColumn get name => text()();
  TextColumn get payloadJson => text()();

  @override
  Set<Column> get primaryKey => {slug};
}

class EscalationRows extends Table {
  TextColumn get slug => text()();
  BoolColumn get resolved => boolean().withDefault(const Constant(false))();
  TextColumn get payloadJson => text()();
  TextColumn get createdAt => text()();
  BoolColumn get dirty => boolean().withDefault(const Constant(false))();

  @override
  Set<Column> get primaryKey => {slug};
}

class ChatMessageRows extends Table {
  TextColumn get id => text()(); // "<bot>:<gateway id or local uuid>"
  TextColumn get bot => text()();
  TextColumn get role => text()();
  TextColumn get payloadJson => text()();
  TextColumn get ts => text()();
  BoolColumn get dirty => boolean().withDefault(const Constant(false))();

  @override
  Set<Column> get primaryKey => {id};
}

class ActivityRows extends Table {
  IntColumn get id => integer().autoIncrement()();
  TextColumn get kind => text()();
  TextColumn get payloadJson => text()();
  TextColumn get ts => text()();
}

class SyncStateRows extends Table {
  // Singleton row, id always 0.
  IntColumn get id => integer()();
  TextColumn get lastPullJson =>
      text().withDefault(const Constant('{}'))(); // {domain: iso_ts}
  TextColumn get lastSeenJson =>
      text().withDefault(const Constant('{}'))(); // notification diff anchors

  @override
  Set<Column> get primaryKey => {id};
}

class OutboxRows extends Table {
  IntColumn get id => integer().autoIncrement()();
  TextColumn get op => text()();
  TextColumn get targetId => text()();
  TextColumn get payloadJson => text()();
  IntColumn get createdAt => integer()();
  IntColumn get attempts => integer().withDefault(const Constant(0))();
  TextColumn get lastError => text().nullable()();
  TextColumn get status =>
      text().withDefault(const Constant('pending'))(); // pending|inflight|failed
}

@DriftDatabase(tables: [
  TaskRows,
  ProjectRows,
  EscalationRows,
  ChatMessageRows,
  ActivityRows,
  SyncStateRows,
  OutboxRows,
])
class AppDatabase extends _$AppDatabase {
  AppDatabase(super.e);

  @override
  int get schemaVersion => 1;

  // ------------------------------------------------------------- outbox
  static const int maxAttempts = 5;

  Future<int> enqueue(String op, String targetId, String payloadJson,
          {required int nowMs}) =>
      into(outboxRows).insert(OutboxRowsCompanion.insert(
        op: op,
        targetId: targetId,
        payloadJson: payloadJson,
        createdAt: nowMs,
      ));

  /// Oldest pending row, or null. FIFO order = insertion id.
  Future<OutboxRow?> nextPending() => (select(outboxRows)
        ..where((o) => o.status.equals('pending'))
        ..orderBy([(o) => OrderingTerm.asc(o.id)])
        ..limit(1))
      .getSingleOrNull();

  Future<void> markInflight(int id) => (update(outboxRows)
        ..where((o) => o.id.equals(id)))
      .write(const OutboxRowsCompanion(status: Value('inflight')));

  Future<void> completeOutbox(int id) =>
      (delete(outboxRows)..where((o) => o.id.equals(id))).go();

  /// Failure: bump attempts; back to pending, or `failed` past maxAttempts.
  Future<void> failOutbox(int id, String error) async {
    final row = await (select(outboxRows)..where((o) => o.id.equals(id)))
        .getSingleOrNull();
    if (row == null) return;
    final attempts = row.attempts + 1;
    await (update(outboxRows)..where((o) => o.id.equals(id))).write(
      OutboxRowsCompanion(
        attempts: Value(attempts),
        lastError: Value(error),
        status: Value(attempts >= maxAttempts ? 'failed' : 'pending'),
      ),
    );
  }

  Future<void> retryOutbox(int id) => (update(outboxRows)
        ..where((o) => o.id.equals(id)))
      .write(const OutboxRowsCompanion(
          status: Value('pending'), attempts: Value(0)));

  Future<void> discardOutbox(int id) => completeOutbox(id);

  Stream<List<OutboxRow>> watchOutbox() => (select(outboxRows)
        ..orderBy([(o) => OrderingTerm.asc(o.id)]))
      .watch();

  /// Every outbox row is queued work (pending, inflight, or failed).
  Stream<int> watchQueuedCount() {
    final c = outboxRows.id.count();
    final q = selectOnly(outboxRows)..addColumns([c]);
    return q.watch().map((rows) => rows.first.read(c) ?? 0);
  }

  // --------------------------------------------------------- sync_state
  Future<SyncStateRow> syncState() async {
    // Idempotent: insertOnConflictUpdate makes concurrent cold-start callers
    // safe (no PK-conflict race on row 0).
    // Only `id` is present in the companion, so the DO UPDATE SET clause only
    // touches `id = excluded.id` — existing lastPullJson/lastSeenJson are
    // preserved on conflict.
    await into(syncStateRows).insertOnConflictUpdate(
        SyncStateRowsCompanion.insert(id: const Value(0)));
    return (select(syncStateRows)..where((s) => s.id.equals(0))).getSingle();
  }

  Future<void> saveSyncState(
          {String? lastPullJson, String? lastSeenJson}) async =>
      (update(syncStateRows)..where((s) => s.id.equals(0))).write(
        SyncStateRowsCompanion(
          lastPullJson:
              lastPullJson != null ? Value(lastPullJson) : const Value.absent(),
          lastSeenJson:
              lastSeenJson != null ? Value(lastSeenJson) : const Value.absent(),
        ),
      );
}
