// Ctrl+K command palette. Phase 0 registry = screen navigation; later
// phases push project/task/action entries into [extraActionsProvider].
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../theme/hive_tokens.dart';

class PaletteAction {
  const PaletteAction({
    required this.label,
    required this.icon,
    required this.run,
    this.subtitle,
  });
  final String label;
  final String? subtitle;
  final IconData icon;
  final void Function() run;
}

const _kScreens = [
  'Home', 'Chat', 'Board', 'Alerts', 'Vault', 'Skills', 'Scout',
  'Studio', 'Calendar', 'LoRA', 'Telemetry',
];

const _kScreenIcons = <String, IconData>{
  'Home': Icons.home_outlined,
  'Chat': Icons.chat_bubble_outline,
  'Board': Icons.dashboard_outlined,
  'Alerts': Icons.priority_high_outlined,
  'Vault': Icons.book_outlined,
  'Skills': Icons.school_outlined,
  'Scout': Icons.radar_outlined,
  'Studio': Icons.brush_outlined,
  'Calendar': Icons.calendar_month_outlined,
  'LoRA': Icons.auto_awesome_outlined,
  'Telemetry': Icons.monitor_heart_outlined,
};

/// Wraps the shell; owns the Ctrl+K shortcut and the overlay.
class CommandPaletteScope extends StatefulWidget {
  const CommandPaletteScope(
      {super.key, required this.goTo, required this.child});

  final void Function(String label) goTo;
  final Widget child;

  @override
  State<CommandPaletteScope> createState() => CommandPaletteScopeState();

  static CommandPaletteScopeState? of(BuildContext context) =>
      context.findAncestorStateOfType<CommandPaletteScopeState>();
}

class CommandPaletteScopeState extends State<CommandPaletteScope> {
  bool _open = false;

  List<PaletteAction> _actions() => [
        for (final s in _kScreens)
          PaletteAction(
            label: 'Go to $s',
            icon: _kScreenIcons[s] ?? Icons.circle_outlined,
            run: () => widget.goTo(s),
          ),
      ];

  void openPalette() {
    if (_open) return;
    _open = true;
    showDialog<void>(
      context: context,
      barrierColor: Colors.black54,
      builder: (_) => _PaletteDialog(actions: _actions()),
    ).whenComplete(() => _open = false);
  }

  @override
  Widget build(BuildContext context) {
    return Shortcuts(
      shortcuts: {
        LogicalKeySet(LogicalKeyboardKey.control, LogicalKeyboardKey.keyK):
            const _OpenPaletteIntent(),
      },
      child: Actions(
        actions: {
          _OpenPaletteIntent:
              CallbackAction<_OpenPaletteIntent>(onInvoke: (_) {
            openPalette();
            return null;
          }),
        },
        child: Focus(autofocus: true, child: widget.child),
      ),
    );
  }
}

class _OpenPaletteIntent extends Intent {
  const _OpenPaletteIntent();
}

class _PaletteDialog extends StatefulWidget {
  const _PaletteDialog({required this.actions});
  final List<PaletteAction> actions;

  @override
  State<_PaletteDialog> createState() => _PaletteDialogState();
}

class _PaletteDialogState extends State<_PaletteDialog> {
  String _query = '';

  List<PaletteAction> get _filtered {
    if (_query.isEmpty) return widget.actions;
    final q = _query.toLowerCase();
    return widget.actions
        .where((a) => a.label.toLowerCase().contains(q))
        .toList();
  }

  void _runFirst() {
    final list = _filtered;
    if (list.isEmpty) return;
    Navigator.of(context).pop();
    list.first.run();
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context).extension<HiveTokens>();
    final list = _filtered;
    return CallbackShortcuts(
      bindings: {
        const SingleActivator(LogicalKeyboardKey.escape): () =>
            Navigator.of(context).pop(),
      },
      child: Dialog(
        alignment: const Alignment(0, -0.6),
        backgroundColor: t?.slate1,
        shape: RoundedRectangleBorder(
            borderRadius:
                BorderRadius.circular(t != null ? HiveTokens.rLg : 16)),
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 480, maxHeight: 420),
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            Padding(
              padding: const EdgeInsets.all(HiveTokens.s3),
              child: TextField(
                autofocus: true,
                onChanged: (v) => setState(() => _query = v),
                onSubmitted: (_) => _runFirst(),
                style: t != null ? TextStyle(color: t.ink) : null,
                decoration: InputDecoration(
                  hintText: 'Jump to…',
                  hintStyle:
                      t != null ? TextStyle(color: t.inkFaint) : null,
                  prefixIcon: Icon(Icons.search,
                      color: t?.slate4),
                  border: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(HiveTokens.rMd),
                    borderSide:
                        t != null ? BorderSide(color: t.slate2) : const BorderSide(),
                  ),
                ),
              ),
            ),
            Flexible(
              child: ListView.builder(
                shrinkWrap: true,
                itemCount: list.length,
                itemBuilder: (context, i) {
                  final a = list[i];
                  return ListTile(
                    dense: true,
                    leading: Icon(a.icon,
                        color: t?.amber2, size: 20),
                    title: Text(a.label,
                        style: t != null ? TextStyle(color: t.ink) : null),
                    subtitle: a.subtitle != null
                        ? Text(a.subtitle!,
                            style: t != null
                                ? TextStyle(
                                    color: t.inkFaint, fontSize: 12)
                                : null)
                        : null,
                    onTap: () {
                      Navigator.of(context).pop();
                      a.run();
                    },
                  );
                },
              ),
            ),
          ]),
        ),
      ),
    );
  }
}
