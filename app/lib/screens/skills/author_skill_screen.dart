import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../state/session.dart';
import '../../theme/hive_tokens.dart';

const _template = '''---
name: my-skill
description: One line on what this skill does.
audience: [owner]
---

# my-skill

When to use this and what it does. Steps, constraints, examples.
''';

/// Author a new skill (POST /v1/skills). Body must be a full markdown
/// file with `---` frontmatter; the server enforces a 100-char minimum.
class AuthorSkillScreen extends ConsumerStatefulWidget {
  const AuthorSkillScreen({super.key});

  @override
  ConsumerState<AuthorSkillScreen> createState() => _AuthorSkillScreenState();
}

class _AuthorSkillScreenState extends ConsumerState<AuthorSkillScreen> {
  final _name = TextEditingController();
  final _body = TextEditingController(text: _template);
  bool _busy = false;
  String? _error;

  @override
  void dispose() {
    _name.dispose();
    _body.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    final gw = ref.read(gatewayClientProvider);
    final name = _name.text.trim();
    final body = _body.text;
    if (gw == null) return;
    if (name.isEmpty) {
      setState(() => _error = 'Name required');
      return;
    }
    if (!body.contains('---')) {
      setState(() => _error = 'Body needs --- frontmatter');
      return;
    }
    if (body.length < 100) {
      setState(() => _error = 'Body too short (min 100 chars)');
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await gw.createSkill(name, body);
      if (mounted) Navigator.of(context).pop(true);
    } catch (e) {
      if (mounted) setState(() => _error = e.toString());
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context).extension<HiveTokens>()!;
    return Scaffold(
      appBar: AppBar(
        title: const Text('New skill'),
        backgroundColor: Colors.transparent,
        elevation: 0,
        actions: [
          TextButton(
            onPressed: _busy ? null : _save,
            child: Text(_busy ? 'Saving…' : 'Save'),
          ),
        ],
      ),
      body: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            TextField(
              controller: _name,
              decoration: const InputDecoration(
                  labelText: 'Skill name (kebab-case)',
                  border: OutlineInputBorder(),
                  isDense: true),
            ),
            const SizedBox(height: 10),
            Expanded(
              child: TextField(
                controller: _body,
                expands: true,
                maxLines: null,
                textAlignVertical: TextAlignVertical.top,
                style: const TextStyle(fontFamily: 'monospace', fontSize: 13),
                decoration: const InputDecoration(
                    labelText: 'Markdown (with --- frontmatter)',
                    border: OutlineInputBorder(),
                    alignLabelWithHint: true),
              ),
            ),
            if (_error != null) ...[
              const SizedBox(height: 8),
              Text(_error!,
                  style: const TextStyle(color: Color(0xFFE08B8B), fontSize: 12)),
            ],
          ],
        ),
      ),
    );
  }
}
