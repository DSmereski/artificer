import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:http/http.dart' as http;
import 'package:web_socket_channel/web_socket_channel.dart';

import '../models/board_stats.dart';
import '../models/crew_task.dart';
import '../models/escalation.dart';
import '../models/digest.dart';

class GatewayException implements Exception {
  GatewayException(this.message, {this.status});
  final String message;
  final int? status;
  @override
  String toString() => 'GatewayException(${status ?? ''}): $message';
}

/// Result from POST /v1/stt. [text] is the transcript; [durationSeconds]
/// is the detected audio duration (may be null if the server omits it).
class SttResult {
  const SttResult({required this.text, this.durationSeconds});
  final String text;
  final double? durationSeconds;

  factory SttResult.fromJson(Map<String, dynamic> j) => SttResult(
        text: (j['text'] ?? '').toString(),
        durationSeconds: j['duration_s'] != null
            ? (j['duration_s'] as num).toDouble()
            : null,
      );
}

/// v2 typed REST + WS client for the ai-team gateway. Focused on the
/// surfaces v2's core ships (board, digest, escalations, the /v1/events
/// spine); feature screens extend it. Central 401 handling via
/// [onAuthFailed]; WS connects swallow the `ready` rejection so a down
/// gateway degrades offline instead of crashing.
class GatewayClient {
  GatewayClient({required this.baseUrl, required this.token, http.Client? http})
      : _http = http ?? _defaultHttp();

  final String baseUrl;
  final String token;
  final http.Client _http;
  Future<void> Function()? onAuthFailed;
  bool _authFired = false;

  static http.Client _defaultHttp() => http.Client();
  static const Duration _timeout = Duration(seconds: 8);

  Map<String, String> get _headers => {
        'Authorization': 'Bearer $token',
        'Content-Type': 'application/json',
      };

  Uri _uri(String path, [Map<String, String>? q]) =>
      Uri.parse(baseUrl).replace(path: path, queryParameters: q);

  void _maybeAuthFailed(int status) {
    if (status != 401 && status != 403) return;
    if (_authFired) return;
    _authFired = true;
    onAuthFailed?.call();
  }

  T _unwrap<T>(http.Response r, T Function(dynamic) parse) {
    if (r.statusCode >= 300) {
      _maybeAuthFailed(r.statusCode);
      throw GatewayException(r.body, status: r.statusCode);
    }
    return parse(r.body.isEmpty ? null : jsonDecode(r.body));
  }

  Future<T> _get<T>(String path, T Function(dynamic) parse,
          {Map<String, String>? q}) async =>
      _unwrap(await _http.get(_uri(path, q), headers: _headers).timeout(_timeout),
          parse);

  /// Raw GET for the sync spine (adapter use only) — returns decoded JSON.
  Future<dynamic> getRaw(String path, {Map<String, String>? q}) =>
      _get(path, (j) => j, q: q);

  Future<T> _post<T>(String path, Object? body, T Function(dynamic) parse) async =>
      _unwrap(
          await _http
              .post(_uri(path), headers: _headers, body: jsonEncode(body ?? {}))
              .timeout(_timeout),
          parse);

  Future<void> _delete(String path) async {
    final r =
        await _http.delete(_uri(path), headers: _headers).timeout(_timeout);
    if (r.statusCode >= 300) {
      _maybeAuthFailed(r.statusCode);
      throw GatewayException(r.body, status: r.statusCode);
    }
  }

  /// Connect a WS swallowing the connect-failure `ready` rejection (a
  /// down gateway would otherwise throw an unhandled exception).
  WebSocketChannel _ws(String path, {Map<String, String>? q}) {
    final wsUrl = Uri.parse(baseUrl).replace(
      scheme: Uri.parse(baseUrl).scheme == 'https' ? 'wss' : 'ws',
      path: path,
      queryParameters: q,
    );
    final ch = WebSocketChannel.connect(wsUrl);
    ch.ready.catchError((Object _) {});
    return ch;
  }

  // ----------------------------------------------------------- events spine
  /// The single live event bus. Every frame is a decoded map; callers
  /// switch on `type`. Treat as LOSSY — re-fetch authoritative state on
  /// reconnect.
  Stream<Map<String, dynamic>> events() => _ws('/v1/events',
          q: {'token': token})
      .stream
      .map(_decode)
      .where((m) => m.isNotEmpty);

  Map<String, dynamic> _decode(dynamic raw) {
    try {
      final d = jsonDecode(raw as String);
      return d is Map ? d.cast<String, dynamic>() : <String, dynamic>{};
    } catch (_) {
      return <String, dynamic>{};
    }
  }

  // ----------------------------------------------------------- digest / home
  Future<Digest> digest({int? sinceEpoch}) => _get(
        '/v1/digest',
        (j) => Digest.fromJson((j as Map).cast<String, dynamic>()),
        q: sinceEpoch != null ? {'sinceEpoch': '$sinceEpoch'} : null,
      );

  // ----------------------------------------------------------- crew board
  Future<BoardState> boardState() => _get('/board/state',
      (j) => BoardState.fromJson((j as Map).cast<String, dynamic>()));

  Future<BoardStats> boardStats() => _get('/board/stats',
      (j) => BoardStats.fromJson((j as Map).cast<String, dynamic>()));

  Future<CrewTask> moveCrewTask(String slug, String toStatus) => _post(
      '/board/tasks/${Uri.encodeComponent(slug)}/move', {'status': toStatus},
      (j) => CrewTask.fromJson((j as Map).cast<String, dynamic>()));

  Future<CrewTask> assignCrewTask(String slug, String assignee) => _post(
      '/board/tasks/${Uri.encodeComponent(slug)}/assign', {'assignee': assignee},
      (j) => CrewTask.fromJson((j as Map).cast<String, dynamic>()));

  Future<void> addCrewComment(String slug, String comment) => _post(
      '/board/tasks/${Uri.encodeComponent(slug)}/comment',
      {'text': comment, 'actor': 'owner'}, (_) {});

  /// Hard-delete a task and all its child rows (audit, approvals, lessons).
  /// Sends DELETE /board/tasks/{slug} with Bearer auth.
  /// Throws [GatewayException] with status 404 if the slug does not exist.
  Future<void> deleteCrewTask(String slug) =>
      _delete('/board/tasks/${Uri.encodeComponent(slug)}');

  /// Pause or resume the dispatcher. When paused, no NEW hive work starts;
  /// in-flight tasks finish and the reaper keeps running.
  Future<void> setBoardPaused(bool paused) =>
      _post(paused ? '/board/pause' : '/board/resume', const {}, (_) {});

  Future<List<Map<String, dynamic>>> crewAudit(String slug) => _get(
      '/board/tasks/${Uri.encodeComponent(slug)}/audit',
      (j) => (j as List).cast<Map<String, dynamic>>());

  Stream<Map<String, dynamic>> boardEvents() =>
      _ws('/board/events').stream.map(_decode).where((m) => m.isNotEmpty);

  /// Resolve a board review-gate approval (approve/reject).
  Future<void> resolveApproval(int approvalId, {required bool approved}) =>
      _post('/board/approvals/$approvalId/resolve', {'approved': approved},
          (_) {});

  // ----------------------------------------------------------- escalations
  Future<List<Escalation>> escalations({bool includeResolved = false}) => _get(
        '/v1/escalations${includeResolved ? "?all=true" : ""}',
        (j) => ((j as Map)['escalations'] as List)
            .cast<Map<String, dynamic>>()
            .map(Escalation.fromJson)
            .toList(),
      );

  Future<int> openEscalationCount() => _get('/v1/escalations/count',
      (j) => ((j as Map)['open_count'] ?? 0) as int);

  Future<void> resolveEscalation(String slug) => _post(
      '/v1/escalations/${Uri.encodeComponent(slug)}/resolve', const {}, (_) {});

  /// Reopen a previously-resolved escalation (v1 never surfaced this).
  Future<void> reopenEscalation(String slug) => _post(
      '/v1/escalations/${Uri.encodeComponent(slug)}/reopen', const {}, (_) {});

  // ----------------------------------------------------------- studio
  Future<List<Map<String, dynamic>>> recentImages({int limit = 40}) => _get(
      '/v1/images/recent', (j) => (j as List).cast<Map<String, dynamic>>(),
      q: {'limit': '$limit'});

  Future<String> submitRender(String prompt) => _post('/v1/render',
      {'prompt': prompt}, (j) => ((j as Map)['job_id'] ?? '').toString());

  /// Absolute URL for a rendered media id. Fetch with [mediaHeaders].
  String mediaUrl(String mediaId) =>
      Uri.parse(baseUrl).replace(path: '/v1/media/$mediaId').toString();
  Map<String, String> get mediaHeaders => {'Authorization': 'Bearer $token'};

  // ----------------------------------------------------------- calendar
  Future<List<Map<String, dynamic>>> calendarJobs() => _get(
      '/v1/calendar/jobs', (j) {
        final list = (j is Map ? (j['jobs'] ?? j['items']) : j) as List?
            ?? const [];
        return list.cast<Map<String, dynamic>>();
      });

  Future<void> createCalendarJob({
    required String title,
    required String scheduledAt,
    required String actionVerb,
    String description = '',
    String recurrence = 'none',
  }) =>
      _post('/v1/calendar/jobs', {
        'title': title,
        'scheduled_at': scheduledAt,
        'action_verb': actionVerb,
        'description': description,
        'recurrence': recurrence,
      }, (_) {});

  Future<void> deleteCalendarJob(String id) =>
      _delete('/v1/calendar/jobs/${Uri.encodeComponent(id)}');

  // ----------------------------------------------------------- loras
  Future<List<Map<String, dynamic>>> loras() => _get('/v1/loras', (j) {
        final list = (j is Map ? j['loras'] : j) as List? ?? const [];
        return list.cast<Map<String, dynamic>>();
      });

  Future<List<Map<String, dynamic>>> loraImports() =>
      _get('/v1/loras/imports', (j) {
        final list = (j is Map ? j['jobs'] : j) as List? ?? const [];
        return list.cast<Map<String, dynamic>>();
      });

  Future<String> startLoraImport(String url) => _post('/v1/loras/import',
      {'url': url}, (j) => ((j as Map)['job_id'] ?? (j)['id'] ?? '').toString());

  // ----------------------------------------------------------- telemetry
  Future<Map<String, dynamic>> concurrency() => _get(
      '/v1/system/concurrency', (j) => (j as Map).cast<String, dynamic>());

  Future<Map<String, dynamic>> lastTurn() => _get(
      '/v1/telemetry/last_turn', (j) => (j as Map).cast<String, dynamic>());

  // ----------------------------------------------------------- scout
  Future<Map<String, dynamic>> scoutStatus() => _get(
      '/v1/scout/status', (j) => (j as Map).cast<String, dynamic>());

  // ----------------------------------------------------------- vault
  Future<List<Map<String, dynamic>>> vaultSearch(String q,
          {int limit = 20}) =>
      _get('/v1/vault/search',
          (j) => (j as List).cast<Map<String, dynamic>>(),
          q: {'q': q, 'limit': '$limit'});

  Future<Map<String, dynamic>> vaultNote(String path) => _get(
      '/v1/vault/note', (j) => (j as Map).cast<String, dynamic>(),
      q: {'path': path});

  // ----------------------------------------------------------- skills
  Future<List<Map<String, dynamic>>> skills() => _get('/v1/skills', (j) {
        final list = (j is Map ? j['skills'] : j) as List? ?? const [];
        return list.cast<Map<String, dynamic>>();
      });

  Future<Map<String, dynamic>> skill(String name) => _get(
      '/v1/skills/${Uri.encodeComponent(name)}',
      (j) => (j as Map).cast<String, dynamic>());

  /// Author a new skill. `body` must be a full markdown file with `---`
  /// frontmatter (min 100 chars, enforced server-side).
  Future<void> createSkill(String name, String body) => _post(
      '/v1/skills', {'name': name, 'body': body}, (_) {});

  // ----------------------------------------------------------- chat
  Future<List<String>> bots() => _get('/v1/bots', (j) {
        final list = (j is Map ? j['bots'] : j) as List? ?? const [];
        return list
            .map((b) => (b is Map ? (b['name'] ?? '') : b).toString())
            .where((s) => s.isNotEmpty)
            .toList();
      });

  Future<List<Map<String, dynamic>>> chatMessages(String bot) => _get(
        '/v1/chat/${Uri.encodeComponent(bot)}/messages',
        (j) {
          final list = (j is Map ? (j['messages'] ?? j['items']) : j) as List?
              ?? const [];
          return list.cast<Map<String, dynamic>>();
        },
      );

  /// Open the streaming chat WS for a bot. Returns a [ChatChannel]
  /// wrapping send + the event stream.
  ChatChannel openChat(String bot, {String? threadId}) {
    final ch = _ws('/v1/chat/${Uri.encodeComponent(bot)}', q: {
      'token': token,
      if (threadId != null) 'thread_id': threadId,
    });
    return ChatChannel(ch);
  }

  // ----------------------------------------------------------- voice / STT

  /// Transcribe raw WAV [bytes] via POST /v1/stt. Sends the bytes with
  /// Content-Type audio/wav; returns the transcript + optional duration.
  ///
  /// The /v1/stt route was deployed but the running gateway process may
  /// predate it — a live test will fail until the gateway is restarted.
  /// Unit tests mock this method.
  Future<SttResult> transcribe(Uint8List bytes) async {
    final uri = _uri('/v1/stt');
    final request = http.Request('POST', uri)
      ..headers['Authorization'] = 'Bearer $token'
      ..headers['Content-Type'] = 'audio/wav'
      ..bodyBytes = bytes;
    final streamed =
        await _http.send(request).timeout(const Duration(seconds: 30));
    final response = await http.Response.fromStream(streamed);
    if (response.statusCode >= 300) {
      _maybeAuthFailed(response.statusCode);
      throw GatewayException(response.body, status: response.statusCode);
    }
    final j = jsonDecode(response.body) as Map<String, dynamic>;
    return SttResult.fromJson(j);
  }

  /// Open the voice WS for a bot. Push WAV bytes via [VoiceChannel.sendWav];
  /// receive transcript/assistant text frames + a binary WAV reply + done.
  ///
  /// DEPRECATED for phone UI: the mic button now routes through
  /// [transcribe] + the chat WS for full Hive coordinator support.
  /// Kept for backward compatibility (e.g. G2 glasses integration tests).
  VoiceChannel openVoice(String bot) {
    final ch = _ws('/v1/voice/${Uri.encodeComponent(bot)}', q: {'token': token});
    return VoiceChannel(ch);
  }

  void close() => _http.close();
}

/// Voice WS wrapper. The server stream interleaves JSON text frames
/// ({transcript|assistant|done|error}) with one binary WAV reply.
class VoiceChannel {
  VoiceChannel(this._ch);
  final WebSocketChannel _ch;

  void sendWav(List<int> bytes) => _ch.sink.add(bytes);

  /// Yields either a decoded JSON map (text frame) or raw bytes
  /// (List<int>, the audio reply).
  Stream<Object> get events => _ch.stream.map((raw) {
        if (raw is String) {
          try {
            final d = jsonDecode(raw);
            return d is Map ? d.cast<String, dynamic>() : <String, dynamic>{};
          } catch (_) {
            return <String, dynamic>{};
          }
        }
        return raw as List<int>; // binary WAV
      });

  void close() {
    try {
      _ch.sink.close();
    } catch (_) {}
  }
}

/// Thin wrapper over the chat WS. Send `user` frames; receive streamed
/// `assistant` chunks, hive-trace frames (thought/delegate/helper_reply/
/// synthesis), `done`, and `error`.
class ChatChannel {
  ChatChannel(this._ch);
  final WebSocketChannel _ch;

  Stream<Map<String, dynamic>> get events => _ch.stream.map((raw) {
        try {
          final d = jsonDecode(raw as String);
          return d is Map ? d.cast<String, dynamic>() : <String, dynamic>{};
        } catch (_) {
          return <String, dynamic>{};
        }
      });

  void sendText(String text) {
    _ch.sink.add(jsonEncode({'type': 'user', 'text': text, 'user_id': 0}));
  }

  void close() {
    try {
      _ch.sink.close();
    } catch (_) {}
  }
}
