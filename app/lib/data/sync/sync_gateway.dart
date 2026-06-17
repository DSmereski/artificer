/// The narrow gateway surface SyncService depends on. Implemented by
/// GatewayAdapter (real HTTP/WS) and FakeGateway (tests).
abstract class SyncGateway {
  /// Raw /board/state JSON: {'tasks': [...], 'projects': [...]}.
  Future<Map<String, dynamic>> boardStateRaw();

  /// Raw escalation list (all=true so resolved sync down too).
  Future<List<Map<String, dynamic>>> escalationsRaw();

  /// Recent chat messages for a bot.
  Future<List<Map<String, dynamic>>> chatMessagesRaw(String bot);

  /// Known bot names.
  Future<List<String>> bots();

  /// The single live event stream (/v1/events). Lossy — re-hydrate on
  /// reconnect. Stream errors/closure signal a dropped connection.
  Stream<Map<String, dynamic>> events();

  // Outbox op executors — one per op type.
  Future<void> moveTask(String slug, String toStatus);
  Future<void> assignTask(String slug, String assignee);
  Future<void> addComment(String slug, String comment);
  Future<void> resolveEscalation(String slug);
  Future<void> sendChat(String bot, String text);
}
