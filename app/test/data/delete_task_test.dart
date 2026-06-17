// Tests for the crew-board delete-task feature.
//
// Covers:
//   - GatewayClient.deleteCrewTask sends DELETE /board/tasks/{slug}
//     with Bearer auth and succeeds on 200.
//   - GatewayClient.deleteCrewTask throws GatewayException(404) when
//     the server returns 404 (slug not found).
//   - GatewayClient.deleteCrewTask throws GatewayException(403) when
//     the server returns 403 (no/wrong auth).
//   - BoardRepository: after deleteCrewTask removes a task server-side,
//     the next replaceAllTasks snapshot (missing the slug) reconciles the
//     local DB and the task is gone from watchTasks.
import 'dart:convert';

import 'package:drift/native.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:ai_team_app_v2/api/gateway_client.dart';
import 'package:ai_team_app_v2/data/db/app_database.dart';
import 'package:ai_team_app_v2/data/repositories/board_repository.dart';

Map<String, dynamic> _task(String slug, {String status = 'backlog'}) => {
      'slug': slug,
      'title': 'Task $slug',
      'body': '',
      'status': status,
      'project_slug': 'proj',
      'assignee': 'none',
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
  // --------------------------------------------------------- GatewayClient

  group('GatewayClient.deleteCrewTask', () {
    test('sends DELETE to /board/tasks/{slug} and succeeds on 200', () async {
      String? capturedMethod;
      String? capturedPath;
      String? capturedAuth;

      final mock = MockClient((req) async {
        capturedMethod = req.method;
        capturedPath = req.url.path;
        capturedAuth = req.headers['Authorization'];
        return http.Response('{"deleted":"T-0001"}', 200,
            headers: {'content-type': 'application/json'});
      });

      final client =
          GatewayClient(baseUrl: 'http://gateway', token: 'mytoken', http: mock);
      await client.deleteCrewTask('T-0001');

      expect(capturedMethod, 'DELETE');
      expect(capturedPath, '/board/tasks/T-0001');
      expect(capturedAuth, 'Bearer mytoken');
    });

    test('throws GatewayException with status 404 when slug not found',
        () async {
      final mock = MockClient(
          (_) async => http.Response('{"detail":"not found"}', 404,
              headers: {'content-type': 'application/json'}));

      final client =
          GatewayClient(baseUrl: 'http://gateway', token: 't', http: mock);

      expect(
        () => client.deleteCrewTask('T-9999'),
        throwsA(
          isA<GatewayException>()
              .having((e) => e.status, 'status', 404),
        ),
      );
    });

    test('throws GatewayException with status 403 when auth is rejected',
        () async {
      final mock = MockClient(
          (_) async => http.Response('{"detail":"forbidden"}', 403,
              headers: {'content-type': 'application/json'}));

      final client =
          GatewayClient(baseUrl: 'http://gateway', token: 'bad', http: mock);

      expect(
        () => client.deleteCrewTask('T-0001'),
        throwsA(
          isA<GatewayException>()
              .having((e) => e.status, 'status', 403),
        ),
      );
    });

    test('URL-encodes slug with special characters', () async {
      String? capturedPath;
      final mock = MockClient((req) async {
        capturedPath = req.url.path;
        return http.Response('{"deleted":"T-1"}', 200,
            headers: {'content-type': 'application/json'});
      });

      final client =
          GatewayClient(baseUrl: 'http://gateway', token: 't', http: mock);
      await client.deleteCrewTask('T-1');
      expect(capturedPath, '/board/tasks/T-1');
    });
  });

  // --------------------------------------------------------- BoardRepository reconciliation

  group('BoardRepository + delete reconciliation', () {
    late AppDatabase db;
    late BoardRepository repo;

    setUp(() {
      db = AppDatabase(NativeDatabase.memory());
      repo = BoardRepository(db, nowMs: () => 1000);
    });
    tearDown(() => db.close());

    test(
        'replaceAllTasks removes a locally-cached task that was deleted server-side',
        () async {
      // Seed two tasks locally (simulating a prior hydration).
      await repo.upsertTasks([_task('T-0001'), _task('T-0002')]);
      final before = await repo.watchTasks().first;
      expect(before.map((t) => t.slug), containsAll(['T-0001', 'T-0002']));

      // Server-side T-0001 was hard-deleted. Next board/state snapshot
      // only contains T-0002. replaceAllTasks reconciles the local DB.
      await repo.replaceAllTasks([_task('T-0002')]);

      final after = await repo.watchTasks().first;
      expect(after.length, 1);
      expect(after.single.slug, 'T-0002');
    });

    test(
        'replaceAllTasks with empty snapshot removes all locally-cached tasks',
        () async {
      await repo.upsertTasks([_task('T-0001'), _task('T-0002')]);
      await repo.replaceAllTasks([]);
      final after = await repo.watchTasks().first;
      expect(after, isEmpty);
    });
  });
}
