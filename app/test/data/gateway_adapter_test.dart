import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:ai_team_app_v2/api/gateway_client.dart';
import 'package:ai_team_app_v2/data/sync/gateway_adapter.dart';

void main() {
  test('escalationsRaw unwraps the {escalations:[...]} envelope', () async {
    final mock = MockClient((req) async {
      expect(req.url.path, '/v1/escalations');
      expect(req.url.queryParameters['all'], 'true');
      return http.Response(
          jsonEncode({
            'escalations': [
              {'slug': 'E-1', 'resolved': false, 'created_at': '2026-06-11'},
            ],
            'open_count': 1,
          }),
          200,
          headers: {'content-type': 'application/json'});
    });
    final client =
        GatewayClient(baseUrl: 'http://x', token: 't', http: mock);
    final adapter = GatewayAdapter(client);
    final rows = await adapter.escalationsRaw();
    expect(rows, hasLength(1));
    expect(rows.single['slug'], 'E-1');
  });

  test('boardStateRaw returns the decoded map', () async {
    final mock = MockClient((req) async => http.Response(
        jsonEncode({'tasks': [], 'projects': []}), 200,
        headers: {'content-type': 'application/json'}));
    final client =
        GatewayClient(baseUrl: 'http://x', token: 't', http: mock);
    final rows = await GatewayAdapter(client).boardStateRaw();
    expect(rows.containsKey('tasks'), isTrue);
  });
}
