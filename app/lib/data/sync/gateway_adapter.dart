import '../../api/gateway_client.dart';
import 'sync_gateway.dart';

/// SyncGateway backed by the real GatewayClient. Paths verified against
/// gateway/routes (board.py, escalations.py, chat.py, bots.py).
class GatewayAdapter implements SyncGateway {
  GatewayAdapter(this.client);

  final GatewayClient client;

  @override
  Future<Map<String, dynamic>> boardStateRaw() async =>
      ((await client.getRaw('/board/state')) as Map).cast<String, dynamic>();

  @override
  Future<List<Map<String, dynamic>>> escalationsRaw() async {
    final j = await client.getRaw('/v1/escalations', q: {'all': 'true'});
    // /v1/escalations returns an envelope {"escalations":[...]}; the typed
    // GatewayClient.escalations() unwraps the same key.
    final list = (j as Map)['escalations'] as List;
    return list.map((e) => (e as Map).cast<String, dynamic>()).toList();
  }

  @override
  Future<List<Map<String, dynamic>>> chatMessagesRaw(String bot) =>
      client.chatMessages(bot);

  @override
  Future<List<String>> bots() => client.bots();

  @override
  Stream<Map<String, dynamic>> events() => client.events();

  // moveCrewTask / assignCrewTask return Future<CrewTask>, not Future<void>.
  // Adapted with .then((_){}) to satisfy the void contract.
  @override
  Future<void> moveTask(String slug, String toStatus) =>
      client.moveCrewTask(slug, toStatus).then((_) {});

  @override
  Future<void> assignTask(String slug, String assignee) =>
      client.assignCrewTask(slug, assignee).then((_) {});

  @override
  Future<void> addComment(String slug, String comment) =>
      client.addCrewComment(slug, comment);

  @override
  Future<void> resolveEscalation(String slug) =>
      client.resolveEscalation(slug);

  /// Chat send is WS-only: open a transient ChatChannel, send one user
  /// frame, flush, close. (Optimistic chat send is dormant until Phase 2
  /// wires it to the UI; this keeps the outbox contract real.)
  @override
  Future<void> sendChat(String bot, String text) async {
    final ch = client.openChat(bot);
    ch.sendText(text);
    await Future<void>.delayed(const Duration(milliseconds: 200));
    ch.close();
  }
}
