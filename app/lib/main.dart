import 'dart:async';

import 'package:connectivity_plus/connectivity_plus.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'data/sync/sync_providers.dart';
import 'notifications/local_notifier.dart';
import 'screens/connect/connect_screen.dart';
import 'screens/shell/app_shell.dart';
import 'state/session.dart';
import 'theme/hive_theme.dart';

void main() {
  runApp(const ProviderScope(child: HiveV2App()));
}

class HiveV2App extends ConsumerWidget {
  const HiveV2App({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final session = ref.watch(sessionProvider);
    return MaterialApp(
      title: 'Hive v2',
      debugShowCheckedModeBanner: false,
      theme: buildHiveTheme(),
      home: session.when(
        loading: () =>
            const Scaffold(body: Center(child: CircularProgressIndicator())),
        error: (_, _) => const ConnectScreen(),
        data: (s) => s.isConnected
            ? const _LifecycleSync(child: AppShell())
            : const ConnectScreen(),
      ),
    );
  }
}

/// Owns app-resume + connectivity-driven sync refresh and the poll-on-open
/// local-notification diff. Mounted only while connected.
class _LifecycleSync extends ConsumerStatefulWidget {
  const _LifecycleSync({required this.child});
  final Widget child;

  @override
  ConsumerState<_LifecycleSync> createState() => _LifecycleSyncState();
}

class _LifecycleSyncState extends ConsumerState<_LifecycleSync>
    with WidgetsBindingObserver {
  LocalNotifier? _notifier;
  StreamSubscription<List<ConnectivityResult>>? _connSub;
  bool _anchored = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _notifier = LocalNotifier(ref.read(appDatabaseProvider));
    _notifier!.init().then((_) async {
      if (!_anchored) {
        await _notifier!.anchorWithoutNotifying();
        _anchored = true;
      }
    });
    _connSub = Connectivity().onConnectivityChanged.listen((results) {
      if (results.any((r) => r != ConnectivityResult.none)) {
        ref.read(syncServiceProvider)?.refresh();
      }
    });
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _connSub?.cancel();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state != AppLifecycleState.resumed) return;
    final sync = ref.read(syncServiceProvider);
    if (sync == null) return;
    sync.refresh().then((_) => _notifier?.diffAndNotify());
  }

  @override
  Widget build(BuildContext context) => widget.child;
}
