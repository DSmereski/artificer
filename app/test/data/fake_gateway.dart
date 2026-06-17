import 'dart:async';

import 'package:ai_team_app_v2/data/sync/sync_gateway.dart';

/// In-memory SyncGateway. Tests mutate the public fields, push WS frames
/// via [emit], and flip [online] to simulate outages.
class FakeGateway implements SyncGateway {
  bool online = true;
  Map<String, dynamic> boardState = {'tasks': [], 'projects': []};
  List<Map<String, dynamic>> escalations = [];
  Map<String, List<Map<String, dynamic>>> chat = {};
  List<String> botList = ['terry'];

  final List<String> calls = []; // op log for assertions
  int eventsSubscriptions = 0;
  int boardStateRawCalls = 0; // tracks how many times boardStateRaw() is called
  StreamController<Map<String, dynamic>>? _events;

  void emit(Map<String, dynamic> frame) => _events?.add(frame);
  void dropConnection() => _events?.addError(StateError('ws dropped'));

  void _check() {
    if (!online) throw StateError('offline');
  }

  @override
  Future<Map<String, dynamic>> boardStateRaw() async {
    _check();
    boardStateRawCalls++;
    return boardState;
  }

  @override
  Future<List<Map<String, dynamic>>> escalationsRaw() async {
    _check();
    return escalations;
  }

  @override
  Future<List<Map<String, dynamic>>> chatMessagesRaw(String bot) async {
    _check();
    return chat[bot] ?? [];
  }

  @override
  Future<List<String>> bots() async {
    _check();
    return botList;
  }

  @override
  Stream<Map<String, dynamic>> events() {
    _check();
    eventsSubscriptions++;
    _events?.close();
    _events = StreamController<Map<String, dynamic>>();
    return _events!.stream;
  }

  Future<void> _op(String desc) async {
    _check();
    calls.add(desc);
  }

  @override
  Future<void> moveTask(String slug, String toStatus) =>
      _op('move:$slug:$toStatus');
  @override
  Future<void> assignTask(String slug, String assignee) =>
      _op('assign:$slug:$assignee');
  @override
  Future<void> addComment(String slug, String comment) =>
      _op('comment:$slug');
  @override
  Future<void> resolveEscalation(String slug) => _op('resolve:$slug');
  @override
  Future<void> sendChat(String bot, String text) => _op('chat:$bot:$text');
}
