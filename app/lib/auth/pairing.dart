import 'dart:convert';

import 'package:http/http.dart' as http;

/// Pairing helpers (no token yet — plain http). Mirrors v1's flow.
/// QR payload format: `ai-team://pair?url=<gateway>&code=<code>`.

class PairPayload {
  const PairPayload({required this.gatewayUrl, required this.code});
  final String gatewayUrl;
  final String code;

  /// Parse a scanned QR string. Accepts the `ai-team://pair?...` form
  /// and a bare `{url, code}` JSON fallback. Returns null if unrecognised.
  static PairPayload? tryParse(String raw) {
    final s = raw.trim();
    if (s.startsWith('ai-team://pair')) {
      final u = Uri.tryParse(s);
      final url = u?.queryParameters['url'];
      final code = u?.queryParameters['code'];
      if (url != null && code != null && url.isNotEmpty && code.isNotEmpty) {
        return PairPayload(gatewayUrl: url, code: code);
      }
    }
    try {
      final j = jsonDecode(s);
      if (j is Map && j['url'] != null && j['code'] != null) {
        return PairPayload(
            gatewayUrl: j['url'].toString(), code: j['code'].toString());
      }
    } catch (_) {}
    return null;
  }
}

/// Complete pairing: POST /v1/pair with the scanned code → device token.
Future<String> completePairing({
  required String gatewayUrl,
  required String code,
  String deviceName = 'Hive v2 phone',
  String platform = 'android',
}) async {
  final r = await http
      .post(
        Uri.parse('$gatewayUrl/v1/pair'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode(
            {'code': code, 'name': deviceName, 'platform': platform}),
      )
      .timeout(const Duration(seconds: 10));
  if (r.statusCode >= 300) {
    throw Exception('pair failed (${r.statusCode}): ${r.body}');
  }
  final body = jsonDecode(r.body) as Map<String, dynamic>;
  final token = body['token'] as String?;
  if (token == null || token.isEmpty) {
    throw Exception('pair response missing token');
  }
  return token;
}
