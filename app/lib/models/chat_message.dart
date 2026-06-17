/// A chat bubble. Assistant bubbles accumulate streamed text and carry
/// the hive-coordinator trace (thought/delegate/helper_reply/synthesis)
/// that produced them.
class ChatMessage {
  ChatMessage({
    required this.role, // 'user' | 'assistant'
    this.text = '',
    List<HiveTrace>? traces,
    this.pending = false,
  }) : traces = traces ?? [];

  final String role;
  String text;
  final List<HiveTrace> traces;
  bool pending;

  bool get isUser => role == 'user';
}

class HiveTrace {
  HiveTrace({required this.kind, required this.text});
  final String kind; // thought | delegate | helper_reply | synthesis
  final String text;
}
