import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../models/chat_message.dart';
import '../../state/chat_state.dart';
import '../../state/session.dart';
import '../../theme/hive_palette.dart';
import '../../theme/hive_tokens.dart';
import '../../widgets/hive_motion.dart';
import '../../widgets/state_views.dart';
import 'voice_mic_button.dart';

/// Streamed chat with a bot (default Hive, legacy wire id 'terry').
/// Renders the hive-coordinator
/// trace (thought → delegate → helper_reply → synthesis) inline above the
/// assistant reply, collapsible.
class ChatScreen extends ConsumerWidget {
  const ChatScreen({super.key, required this.bot});

  final String bot;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = Theme.of(context).extension<HiveTokens>()!;
    final c = ref.watch(chatControllerProvider(bot));
    return Column(
      children: [
        Expanded(
          child: c.messages.isEmpty
              ? EmptyView(
                  title: 'Say hi to ${botDisplayName(bot)}.',
                  hint: connectedLabel(c.connected),
                  icon: Icons.chat_bubble_outline)
              : ListView.builder(
                  reverse: true,
                  padding: const EdgeInsets.fromLTRB(12, 12, 12, 8),
                  itemCount: c.messages.length,
                  itemBuilder: (_, i) {
                    final m = c.messages[c.messages.length - 1 - i];
                    return _Bubble(msg: m, tokens: t);
                  },
                ),
        ),
        if (c.error != null)
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 12),
            child: Text(c.error!,
                style: const TextStyle(
                    color: HivePalette.red, fontSize: 12)),
          ),
        _Composer(
          enabled: c.connected,
          sending: c.sending,
          onSend: c.send,
          tokens: t,
          leading: VoiceMicButton(
            gateway: ref.read(gatewayClientProvider),
            bot: bot,
            onSend: c.send,
          ),
        ),
      ],
    );
  }

  String connectedLabel(bool connected) =>
      connected ? 'Connected.' : 'Connecting...';
}

// ─────────────────────────────────────────────────────────────────────────────
// Bubble
// ─────────────────────────────────────────────────────────────────────────────

class _Bubble extends StatelessWidget {
  const _Bubble({required this.msg, required this.tokens});
  final ChatMessage msg;
  final HiveTokens tokens;

  @override
  Widget build(BuildContext context) {
    final isUser = msg.isUser;

    final radius = BorderRadius.only(
      topLeft: const Radius.circular(HiveTokens.rLg),
      topRight: const Radius.circular(HiveTokens.rLg),
      bottomLeft: isUser
          ? const Radius.circular(HiveTokens.rLg)
          : const Radius.circular(4),
      bottomRight: isUser
          ? const Radius.circular(4)
          : const Radius.circular(HiveTokens.rLg),
    );

    Widget bubble;
    if (isUser) {
      // "Me" bubble: copper→amber gradient + soft shadow, bottom-right clipped
      bubble = Container(
        padding: const EdgeInsets.symmetric(horizontal: 13, vertical: 10),
        constraints: BoxConstraints(
            maxWidth: MediaQuery.of(context).size.width * 0.80),
        decoration: BoxDecoration(
          borderRadius: radius,
          gradient: const LinearGradient(
            begin: Alignment.topLeft,
            end: Alignment.bottomRight,
            colors: [
              Color(0xFF66481A), // warm copper-amber
              Color(0xFF4D3210), // deeper copper
            ],
          ),
          boxShadow: [
            BoxShadow(
              color: HivePalette.amber2.withValues(alpha: 0.25),
              blurRadius: 12,
              offset: const Offset(0, 4),
            ),
          ],
        ),
        child: Text(
          msg.text.isEmpty && msg.pending ? '...' : msg.text,
          style: TextStyle(
            color: tokens.ink,
            fontSize: 13,
            height: 1.4,
            fontWeight: FontWeight.w500,
          ),
        ),
      );
    } else {
      // Bot bubble: card background + hairline border, bottom-left clipped
      bubble = Container(
        padding: const EdgeInsets.symmetric(horizontal: 13, vertical: 10),
        constraints: BoxConstraints(
            maxWidth: MediaQuery.of(context).size.width * 0.82),
        decoration: BoxDecoration(
          borderRadius: radius,
          color: tokens.slate2.withValues(alpha: 0.75),
          border: Border.all(
            color: tokens.slate3.withValues(alpha: 0.6),
          ),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            if (msg.traces.isNotEmpty)
              _Trace(traces: msg.traces, tokens: tokens),
            Text(
              msg.text.isEmpty && msg.pending ? '...' : msg.text,
              style: TextStyle(
                color: tokens.ink,
                fontSize: 13,
                height: 1.4,
                fontWeight: FontWeight.w500,
              ),
            ),
          ],
        ),
      );
    }

    return Align(
      alignment: isUser ? Alignment.centerRight : Alignment.centerLeft,
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 4),
        child: bubble,
      ),
    );
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Hive trace (helper-trace pulsing line)
// ─────────────────────────────────────────────────────────────────────────────

class _Trace extends StatelessWidget {
  const _Trace({required this.traces, required this.tokens});
  final List<HiveTrace> traces;
  final HiveTokens tokens;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Theme(
        data: Theme.of(context).copyWith(dividerColor: Colors.transparent),
        child: ExpansionTile(
          tilePadding: EdgeInsets.zero,
          childrenPadding: const EdgeInsets.only(bottom: 4),
          dense: true,
          title: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              const PulseDot(color: HivePalette.amber1, size: 6),
              const SizedBox(width: 6),
              Text(
                'hive trace · ${traces.length}',
                style: TextStyle(
                  color: tokens.amber1,
                  fontSize: 10.5,
                  fontWeight: FontWeight.w600,
                ),
              ),
            ],
          ),
          children: [
            for (final tr in traces) _TraceRow(trace: tr, tokens: tokens),
          ],
        ),
      ),
    );
  }
}

class _TraceRow extends StatelessWidget {
  const _TraceRow({required this.trace, required this.tokens});
  final HiveTrace trace;
  final HiveTokens tokens;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 2),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: const EdgeInsets.only(top: 3, right: 6),
            child: PulseDot(color: HivePalette.amber1, size: 5),
          ),
          Text(
            '${trace.kind}: ',
            style: TextStyle(
              color: tokens.amber1,
              fontSize: 10.5,
              fontWeight: FontWeight.w700,
            ),
          ),
          Expanded(
            child: Text(
              trace.text,
              maxLines: 3,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(
                color: tokens.inkFaint,
                fontSize: 10.5,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Composer
// ─────────────────────────────────────────────────────────────────────────────

class _Composer extends StatefulWidget {
  const _Composer({
    required this.enabled,
    required this.sending,
    required this.onSend,
    required this.tokens,
    this.leading,
  });
  final bool enabled;
  final bool sending;
  final void Function(String) onSend;
  final HiveTokens tokens;
  final Widget? leading;

  @override
  State<_Composer> createState() => _ComposerState();
}

class _ComposerState extends State<_Composer> {
  final _ctrl = TextEditingController();

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  void _send() {
    final text = _ctrl.text.trim();
    if (text.isEmpty) return;
    widget.onSend(text);
    _ctrl.clear();
  }

  @override
  Widget build(BuildContext context) {
    return SafeArea(
      top: false,
      child: Container(
        padding: const EdgeInsets.fromLTRB(12, 8, 12, 10),
        decoration: BoxDecoration(
          color: const Color(0xFF100F0C),
          border: Border(
            top: BorderSide(
              color: widget.tokens.slate3.withValues(alpha: 0.5),
            ),
          ),
        ),
        child: Row(
          children: [
            if (widget.leading != null) ...[
              widget.leading!,
              const SizedBox(width: 8),
            ],
            Expanded(
              child: TextField(
                controller: _ctrl,
                enabled: widget.enabled,
                minLines: 1,
                maxLines: 5,
                textInputAction: TextInputAction.send,
                onSubmitted: (_) => _send(),
                style: TextStyle(
                  color: widget.tokens.ink,
                  fontSize: 13,
                ),
                decoration: InputDecoration(
                  hintText: widget.enabled
                      ? 'Message...'
                      : 'Connecting...',
                  filled: true,
                  fillColor: widget.tokens.slate1,
                  contentPadding: const EdgeInsets.symmetric(
                      horizontal: 16, vertical: 10),
                  border: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(HiveTokens.rPill),
                    borderSide: BorderSide(
                        color: widget.tokens.slate3.withValues(alpha: 0.5)),
                  ),
                  enabledBorder: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(HiveTokens.rPill),
                    borderSide: BorderSide(
                        color: widget.tokens.slate3.withValues(alpha: 0.5)),
                  ),
                  focusedBorder: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(HiveTokens.rPill),
                    borderSide:
                        const BorderSide(color: HivePalette.amber2),
                  ),
                  hintStyle: TextStyle(
                    color: widget.tokens.inkFaint,
                    fontSize: 13,
                  ),
                  isDense: true,
                ),
              ),
            ),
            const SizedBox(width: 8),
            // Send button
            Container(
              width: 40,
              height: 40,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                gradient: widget.enabled && !widget.sending
                    ? const LinearGradient(
                        begin: Alignment.topLeft,
                        end: Alignment.bottomRight,
                        colors: [HivePalette.amber2, HivePalette.amberGlow],
                      )
                    : null,
                color: widget.enabled && !widget.sending
                    ? null
                    : const Color(0xFF2A2015),
              ),
              child: IconButton(
                padding: EdgeInsets.zero,
                icon: widget.sending
                    ? const SizedBox(
                        width: 18,
                        height: 18,
                        child: CircularProgressIndicator(
                          strokeWidth: 2,
                          color: HivePalette.amber1,
                        ))
                    : Icon(
                        Icons.send,
                        size: 18,
                        color: widget.enabled
                            ? HivePalette.inkOnAmber
                            : widget.tokens.inkFaint,
                      ),
                onPressed:
                    widget.enabled && !widget.sending ? _send : null,
              ),
            ),
          ],
        ),
      ),
    );
  }
}
