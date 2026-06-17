import 'dart:convert';

import 'package:drift/drift.dart';

import '../db/app_database.dart';

const int _kFeedCap = 200;

class ActivityRepository {
  ActivityRepository(this.db);

  final AppDatabase db;

  /// Newest-first feed, capped at [_kFeedCap] rows.
  Stream<List<Map<String, dynamic>>> watchFeed() =>
      (db.select(db.activityRows)
            ..orderBy([(a) => OrderingTerm.desc(a.id)])
            ..limit(_kFeedCap))
          .watch()
          .map((rows) => rows
              .map((r) => <String, dynamic>{
                    'kind': r.kind,
                    'ts': r.ts,
                    'payload': (jsonDecode(r.payloadJson) as Map)
                        .cast<String, dynamic>(),
                  })
              .toList());

  Future<void> append(String kind, Map<String, dynamic> payload,
      {required String ts}) async {
    await db.into(db.activityRows).insert(ActivityRowsCompanion.insert(
        kind: kind, payloadJson: jsonEncode(payload), ts: ts));
    // Trim beyond cap: find the id at position (cap) and delete everything
    // older (lower autoincrement id).
    final ids = await (db.selectOnly(db.activityRows)
          ..addColumns([db.activityRows.id])
          ..orderBy([OrderingTerm.desc(db.activityRows.id)])
          ..limit(1, offset: _kFeedCap - 1))
        .get();
    if (ids.isEmpty) return;
    final cutoff = ids.first.read(db.activityRows.id)!;
    await (db.delete(db.activityRows)
          ..where((a) => a.id.isSmallerThanValue(cutoff)))
        .go();
  }
}
