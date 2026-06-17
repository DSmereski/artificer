// The ONLY board read/write surface the UI uses. Reads stream from the
// local drift DB; writes patch the DB optimistically (dirty=1) and
// enqueue an outbox op the SyncService replays. Gateway-wins: any
// authoritative upsert overwrites local guesses and clears dirty.
import 'dart:convert';

import 'package:drift/drift.dart';

import '../../models/crew_task.dart';
import '../db/app_database.dart';

class BoardRepository {
  BoardRepository(this.db, {required this.nowMs});

  final AppDatabase db;
  final int Function() nowMs;

  // ----------------------------------------------------------- reads
  Stream<List<CrewTask>> watchTasks() => (db.select(db.taskRows)
        ..where((t) => t.pendingDelete.equals(false))
        ..orderBy([(t) => OrderingTerm.desc(t.updatedAt)]))
      .watch()
      .map((rows) => rows
          .map((r) => CrewTask.fromJson(
              (jsonDecode(r.payloadJson) as Map).cast<String, dynamic>()))
          .toList());

  Stream<List<CrewProject>> watchProjects() =>
      db.select(db.projectRows).watch().map((rows) => rows
          .map((r) => CrewProject.fromJson(
              (jsonDecode(r.payloadJson) as Map).cast<String, dynamic>()))
          .toList());

  // -------------------------------------------- authoritative upserts
  TaskRowsCompanion _taskCompanion(Map<String, dynamic> j) =>
      TaskRowsCompanion.insert(
        slug: (j['slug'] ?? '') as String,
        title: (j['title'] ?? '') as String,
        status: (j['status'] ?? 'proposed') as String,
        projectSlug: (j['project_slug'] ?? '') as String,
        assignee: (j['assignee'] ?? 'none') as String,
        priority: (j['priority'] ?? 'medium') as String,
        payloadJson: jsonEncode(j),
        updatedAt: (j['updated_at'] ?? '') as String,
        dirty: const Value(false),
        pendingDelete: const Value(false),
      );

  Future<void> upsertTasks(List<Map<String, dynamic>> tasks) =>
      db.batch((b) {
        for (final j in tasks) {
          b.insert(db.taskRows, _taskCompanion(j),
              mode: InsertMode.insertOrReplace);
        }
      });

  /// Full-snapshot hydrate: upsert everything, delete rows absent from
  /// the snapshot (handles tasks archived/deleted server-side).
  Future<void> replaceAllTasks(List<Map<String, dynamic>> tasks) async {
    final keep = tasks.map((j) => j['slug'] as String).toSet();
    await db.transaction(() async {
      await upsertTasks(tasks);
      await (db.delete(db.taskRows)
            ..where((t) => t.slug.isNotIn(keep.toList())))
          .go();
    });
  }

  Future<void> replaceAllProjects(List<Map<String, dynamic>> projects) async {
    final keep = projects.map((j) => j['slug'] as String).toSet();
    await db.transaction(() async {
      await db.batch((b) {
        for (final j in projects) {
          b.insert(
              db.projectRows,
              ProjectRowsCompanion.insert(
                slug: (j['slug'] ?? '') as String,
                name: (j['name'] ?? '') as String,
                payloadJson: jsonEncode(j),
              ),
              mode: InsertMode.insertOrReplace);
        }
      });
      await (db.delete(db.projectRows)
            ..where((p) => p.slug.isNotIn(keep.toList())))
          .go();
    });
  }

  // ------------------------------------------------ optimistic writes
  Future<void> _patchTask(
      String slug, Map<String, dynamic> Function(Map<String, dynamic>) fn)
      async {
    final row = await (db.select(db.taskRows)
          ..where((t) => t.slug.equals(slug)))
        .getSingleOrNull();
    if (row == null) return;
    final j = fn((jsonDecode(row.payloadJson) as Map).cast<String, dynamic>());
    await (db.update(db.taskRows)..where((t) => t.slug.equals(slug))).write(
      TaskRowsCompanion(
        status: Value((j['status'] ?? row.status) as String),
        assignee: Value((j['assignee'] ?? row.assignee) as String),
        payloadJson: Value(jsonEncode(j)),
        dirty: const Value(true),
      ),
    );
  }

  Future<void> moveTaskOptimistic(String slug, String toStatus) async {
    await _patchTask(slug, (j) => {...j, 'status': toStatus});
    await db.enqueue(
        'move', slug, jsonEncode({'to_status': toStatus}), nowMs: nowMs());
  }

  Future<void> assignTaskOptimistic(String slug, String assignee) async {
    await _patchTask(slug, (j) => {...j, 'assignee': assignee});
    await db.enqueue(
        'assign', slug, jsonEncode({'assignee': assignee}), nowMs: nowMs());
  }

  Future<void> addCommentQueued(String slug, String comment) =>
      db.enqueue('comment', slug, jsonEncode({'comment': comment}),
          nowMs: nowMs());
}
