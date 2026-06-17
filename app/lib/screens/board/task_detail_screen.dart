import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../models/crew_task.dart';
import '../../state/board_state.dart';
import '../../state/session.dart';
import '../../theme/hive_tokens.dart';

/// Allowed forward transitions per status, mirroring
/// `gateway/crew_board/schema.py::ALLOWED_TRANSITIONS`. Only the moves
/// an owner would drive from the app are surfaced as buttons.
const Map<String, List<String>> _kOwnerMoves = {
  'proposed': ['backlog', 'archived'],
  'backlog': ['ready', 'archived'],
  'ready': ['in_progress', 'backlog', 'archived'],
  'in_progress': ['qa', 'review', 'ready', 'archived'],
  'qa': ['review', 'ready', 'archived'],
  'review': ['done', 'in_progress', 'archived'],
  'done': ['archived'],
};

const List<String> _kAssignees = ['none', 'hive', 'claude-code', 'owner'];

/// Detail + owner actions for a single crew task. Assign a worker, drive
/// it through the state machine (approve a review → done, reject → back
/// to in_progress), comment, and inspect acceptance criteria + the last
/// verifier results. Token usage shows SEPARATELY (hive vs claude).
class TaskDetailScreen extends ConsumerStatefulWidget {
  const TaskDetailScreen({super.key, required this.task});

  final CrewTask task;

  @override
  ConsumerState<TaskDetailScreen> createState() => _TaskDetailScreenState();
}

class _TaskDetailScreenState extends ConsumerState<TaskDetailScreen> {
  late CrewTask _task = widget.task;
  bool _busy = false;
  String? _error;
  List<Map<String, dynamic>> _audit = const [];

  @override
  void initState() {
    super.initState();
    _loadAudit();
  }

  Future<void> _loadAudit() async {
    final gw = ref.read(gatewayClientProvider);
    if (gw == null) return;
    try {
      final a = await gw.crewAudit(_task.slug);
      if (mounted) setState(() => _audit = a);
    } catch (_) {
      // Best-effort — the timeline is supplementary.
    }
  }

  Future<void> _run(Future<CrewTask> Function() action) async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final updated = await action();
      if (!mounted) return;
      setState(() => _task = updated);
      ref.invalidate(boardStateProvider);
      _loadAudit();
    } catch (e) {
      if (!mounted) return;
      setState(() => _error = e.toString());
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _confirmDelete(BuildContext context) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text('Delete ${_task.slug} permanently?'),
        content: const Text(
          'This removes the task and all its audit history. '
          'This cannot be undone.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('Cancel'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text(
              'Delete',
              style: TextStyle(color: Color(0xFFE08B8B)),
            ),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted) return;
    final gw = ref.read(gatewayClientProvider);
    if (gw == null) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await gw.deleteCrewTask(_task.slug);
      if (!mounted) return;
      ref.invalidate(boardStateProvider);
      Navigator.of(context).pop(); // pop back to the board
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Delete failed: $e')),
      );
      setState(() => _error = e.toString());
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context).extension<HiveTokens>()!;
    final gw = ref.read(gatewayClientProvider);
    final moves = _kOwnerMoves[_task.status] ?? const <String>[];
    return Scaffold(
      backgroundColor: Colors.transparent,
      appBar: AppBar(
        backgroundColor: Colors.transparent,
        elevation: 0,
        title: Text(_task.slug),
        actions: [
          Builder(
            builder: (ctx) => IconButton(
              icon: const Icon(Icons.delete_outline),
              tooltip: 'Delete task permanently',
              color: const Color(0xFFE08B8B),
              onPressed: _busy ? null : () => _confirmDelete(ctx),
            ),
          ),
        ],
      ),
      body: gw == null
          ? Center(
              child: Text('Gateway offline',
                  style: TextStyle(color: t.slate4)))
          : ListView(
              padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
              children: [
                Text(_task.title,
                    style: const TextStyle(
                        fontSize: 18, fontWeight: FontWeight.w600)),
                const SizedBox(height: 6),
                Row(children: [
                  _pill(_task.status.replaceAll('_', ' '), t.amber2, t),
                  const SizedBox(width: 8),
                  if (_task.assignee != 'none')
                    _pill(_task.assignee, t.cyan, t),
                  const Spacer(),
                  if (_task.hiveTokens > 0)
                    _pill('H ${_task.hiveTokens}', const Color(0xFF8FD19E), t),
                  if (_task.claudeTokens > 0) ...[
                    const SizedBox(width: 6),
                    _pill('C ${_task.claudeTokens}',
                        const Color(0xFFC9A0FF), t),
                  ],
                ]),
                if (_task.body.isNotEmpty) ...[
                  const SizedBox(height: 14),
                  Text(_task.body, style: TextStyle(color: t.ink, height: 1.35)),
                ],
                if (_task.acceptanceCriteria.isNotEmpty) ...[
                  const SizedBox(height: 16),
                  _heading('ACCEPTANCE CRITERIA', t),
                  for (final c in _task.acceptanceCriteria)
                    Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                      Icon(
                        (c['checked'] == true)
                            ? Icons.check_box
                            : Icons.check_box_outline_blank,
                        size: 18,
                        color: (c['checked'] == true)
                            ? const Color(0xFF8FD19E)
                            : t.slate4,
                      ),
                      const SizedBox(width: 6),
                      Expanded(
                        child: Padding(
                          padding: const EdgeInsets.symmetric(vertical: 2),
                          child: Text('${c['text'] ?? ''}',
                              style: TextStyle(color: t.ink, fontSize: 13)),
                        ),
                      ),
                    ]),
                ],
                const SizedBox(height: 18),
                _heading('ASSIGN', t),
                Wrap(spacing: 8, children: [
                  for (final a in _kAssignees)
                    ChoiceChip(
                      label: Text(a),
                      selected: _task.assignee == a,
                      onSelected: _busy
                          ? null
                          : (_) => _run(() => gw.assignCrewTask(_task.slug, a)),
                    ),
                ]),
                const SizedBox(height: 18),
                _heading('MOVE', t),
                Wrap(spacing: 8, runSpacing: 4, children: [
                  for (final m in moves)
                    OutlinedButton(
                      onPressed: _busy
                          ? null
                          : () => _run(() => gw.moveCrewTask(_task.slug, m)),
                      child: Text(_moveLabel(_task.status, m)),
                    ),
                ]),
                const SizedBox(height: 18),
                _heading('COMMENT', t),
                Builder(builder: (innerCtx) {
                  return _CommentBox(
                    enabled: !_busy,
                    onSubmit: (text) async {
                      final messenger = ScaffoldMessenger.of(innerCtx);
                      await gw.addCrewComment(_task.slug, text);
                      await _loadAudit();
                      messenger.showSnackBar(
                        const SnackBar(content: Text('Comment added')),
                      );
                    },
                  );
                }),
                if (_audit.isNotEmpty) ...[
                  const SizedBox(height: 18),
                  _heading('HISTORY', t),
                  for (final e in _audit.take(40))
                    Padding(
                      padding: const EdgeInsets.symmetric(vertical: 3),
                      child: Row(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          SizedBox(
                            width: 96,
                            child: Text(
                              '${e['actor'] ?? ''} · ${e['action'] ?? ''}',
                              style: TextStyle(
                                  color: t.slate4, fontSize: 10.5),
                            ),
                          ),
                          const SizedBox(width: 8),
                          Expanded(
                            child: Text(
                              '${e['detail'] ?? ''}',
                              style: TextStyle(color: t.ink, fontSize: 12),
                            ),
                          ),
                        ],
                      ),
                    ),
                ],
                if (_error != null) ...[
                  const SizedBox(height: 14),
                  Text(_error!,
                      style: const TextStyle(color: Color(0xFFE08B8B))),
                ],
              ],
            ),
    );
  }

  /// Friendlier labels for the review approve/reject moves.
  String _moveLabel(String from, String to) {
    if (from == 'review' && to == 'done') return 'Approve → done';
    if (from == 'review' && to == 'in_progress') return 'Reject → rework';
    return to.replaceAll('_', ' ');
  }

  Widget _heading(String s, HiveTokens t) => Padding(
        padding: const EdgeInsets.only(bottom: 8),
        child: Text(s,
            style: TextStyle(
                color: t.slate4,
                fontSize: 11,
                fontWeight: FontWeight.w700,
                letterSpacing: 0.8)),
      );

  Widget _pill(String s, Color c, HiveTokens t) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
        decoration: BoxDecoration(
            color: c.withValues(alpha: 0.16),
            borderRadius: BorderRadius.circular(6)),
        child: Text(s,
            style: TextStyle(
                color: c, fontSize: 11, fontWeight: FontWeight.w600)),
      );
}

class _CommentBox extends StatefulWidget {
  const _CommentBox({required this.onSubmit, required this.enabled});

  final Future<void> Function(String) onSubmit;
  final bool enabled;

  @override
  State<_CommentBox> createState() => _CommentBoxState();
}

class _CommentBoxState extends State<_CommentBox> {
  final _controller = TextEditingController();
  bool _sending = false;

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  Future<void> _send() async {
    final text = _controller.text.trim();
    if (text.isEmpty || _sending) return;
    setState(() => _sending = true);
    try {
      await widget.onSubmit(text);
      _controller.clear();
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Comment failed: $e')),
        );
      }
    } finally {
      if (mounted) setState(() => _sending = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Row(children: [
      Expanded(
        child: TextField(
          controller: _controller,
          enabled: widget.enabled && !_sending,
          minLines: 1,
          maxLines: 3,
          decoration: const InputDecoration(
            hintText: 'Add a comment…',
            border: OutlineInputBorder(),
            isDense: true,
          ),
        ),
      ),
      const SizedBox(width: 8),
      IconButton(
        icon: const Icon(Icons.send),
        onPressed: widget.enabled && !_sending ? _send : null,
      ),
    ]);
  }
}
