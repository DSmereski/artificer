import 'dart:convert';

import 'package:drift/drift.dart';

import '../db/app_database.dart';

class ChatRepository {
  ChatRepository(this.db, {required this.nowMs});

  final AppDatabase db;
  final int Function() nowMs;

  Stream<List<Map<String, dynamic>>> watchMessages(String bot) =>
      (db.select(db.chatMessageRows)
            ..where((m) => m.bot.equals(bot))
            ..orderBy([(m) => OrderingTerm.asc(m.ts)]))
          .watch()
          .map((rows) => rows
              .map((r) =>
                  (jsonDecode(r.payloadJson) as Map).cast<String, dynamic>())
              .toList());

  /// Authoritative messages from gateway (hydrate or WS frame).
  Future<void> appendMessages(
          String bot, List<Map<String, dynamic>> messages) =>
      db.batch((b) {
        for (final j in messages) {
          b.insert(
              db.chatMessageRows,
              ChatMessageRowsCompanion.insert(
                id: '$bot:${j['id'] ?? j['ts'] ?? ''}',
                bot: bot,
                role: (j['role'] ?? 'assistant') as String,
                payloadJson: jsonEncode(j),
                ts: '${j['ts'] ?? ''}',
              ),
              mode: InsertMode.insertOrReplace);
        }
      });

  /// Optimistic local send: visible immediately, replayed via outbox.
  Future<void> sendOptimistic(String bot, String text,
      {required String localId}) async {
    final ts = '${nowMs()}';
    final j = <String, dynamic>{
      'id': localId,
      'role': 'user',
      'text': text,
      'ts': ts,
      'pending': true,
    };
    await db.into(db.chatMessageRows).insert(
        ChatMessageRowsCompanion.insert(
          id: '$bot:$localId',
          bot: bot,
          role: 'user',
          payloadJson: jsonEncode(j),
          ts: ts,
          dirty: const Value(true),
        ),
        mode: InsertMode.insertOrReplace);
    await db.enqueue('send_chat', bot, jsonEncode({'text': text}),
        nowMs: nowMs());
  }
}
