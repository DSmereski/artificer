import 'dart:convert';

import 'package:drift/drift.dart';

import '../db/app_database.dart';

class EscalationRepository {
  EscalationRepository(this.db, {required this.nowMs});

  final AppDatabase db;
  final int Function() nowMs;

  Stream<List<Map<String, dynamic>>> watchEscalations() =>
      (db.select(db.escalationRows)
            ..orderBy([(e) => OrderingTerm.desc(e.createdAt)]))
          .watch()
          .map((rows) => rows
              .map((r) =>
                  (jsonDecode(r.payloadJson) as Map).cast<String, dynamic>())
              .toList());

  Future<void> replaceAll(List<Map<String, dynamic>> escalations) async {
    final keep =
        escalations.map((j) => (j['slug'] ?? '') as String).toSet();
    await db.transaction(() async {
      await db.batch((b) {
        for (final j in escalations) {
          b.insert(
              db.escalationRows,
              EscalationRowsCompanion.insert(
                slug: (j['slug'] ?? '') as String,
                resolved: Value((j['resolved'] ?? false) as bool),
                payloadJson: jsonEncode(j),
                createdAt: (j['created_at'] ?? '') as String,
              ),
              mode: InsertMode.insertOrReplace);
        }
      });
      await (db.delete(db.escalationRows)
            ..where((e) => e.slug.isNotIn(keep.toList())))
          .go();
    });
  }

  Future<void> resolveOptimistic(String slug) async {
    final row = await (db.select(db.escalationRows)
          ..where((e) => e.slug.equals(slug)))
        .getSingleOrNull();
    if (row == null) return;
    final j = {
      ...(jsonDecode(row.payloadJson) as Map).cast<String, dynamic>(),
      'resolved': true,
    };
    await (db.update(db.escalationRows)..where((e) => e.slug.equals(slug)))
        .write(EscalationRowsCompanion(
      resolved: const Value(true),
      payloadJson: Value(jsonEncode(j)),
      dirty: const Value(true),
    ));
    await db.enqueue('resolve_escalation', slug, '{}', nowMs: nowMs());
  }
}
