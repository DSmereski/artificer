// Poll-on-open notifications (spec: no FCM). On app resume we force a
// SyncService.refresh(); this module diffs the refreshed DB against
// last-seen anchors stored in sync_state and fires LOCAL notifications
// for: new unresolved escalations, tasks newly in `review` or `done`.
import 'dart:convert';

import 'package:flutter_local_notifications/flutter_local_notifications.dart';

import '../data/db/app_database.dart';

class LocalNotifier {
  LocalNotifier(this.db, {FlutterLocalNotificationsPlugin? plugin})
      : _plugin = plugin ?? FlutterLocalNotificationsPlugin();

  final AppDatabase db;
  final FlutterLocalNotificationsPlugin _plugin;
  bool _ready = false;

  // Android needs a registered channel + (API 33+) runtime permission, else
  // show() silently no-ops. One channel for all poll-on-open events.
  static const _androidChannelId = 'aiteam_events';
  static const _details = NotificationDetails(
    android: AndroidNotificationDetails(
      _androidChannelId,
      'AI Team events',
      channelDescription: 'Escalations and finished tasks',
      importance: Importance.high,
      priority: Priority.high,
    ),
  );

  Future<void> init() async {
    if (_ready) return;
    const android = AndroidInitializationSettings('@mipmap/ic_launcher');
    await _plugin.initialize(const InitializationSettings(
        android: android));
    // Request POST_NOTIFICATIONS on Android 13+ (no-op on older/other OSes).
    await _plugin
        .resolvePlatformSpecificImplementation<
            AndroidFlutterLocalNotificationsPlugin>()
        ?.requestNotificationsPermission();
    _ready = true;
  }

  /// Diff DB vs last-seen anchors; notify; advance anchors.
  Future<void> diffAndNotify() async {
    if (!_ready) return;
    final state = await db.syncState();
    final seen =
        (jsonDecode(state.lastSeenJson) as Map).cast<String, dynamic>();
    final seenEsc = (seen['escalations'] ?? '') as String;
    final seenDone = (seen['tasks_done'] ?? '') as String;

    var id = 0;
    String maxEsc = seenEsc, maxDone = seenDone;

    final esc = await (db.select(db.escalationRows)
          ..where((e) => e.resolved.equals(false)))
        .get();
    for (final e in esc) {
      if (e.createdAt.compareTo(seenEsc) > 0) {
        await _plugin.show(id++, 'Escalation needs you', e.slug, _details);
        if (e.createdAt.compareTo(maxEsc) > 0) maxEsc = e.createdAt;
      }
    }

    final finished = await (db.select(db.taskRows)
          ..where((t) => t.status.isIn(['qa', 'review', 'done'])))
        .get();
    for (final t in finished) {
      if (t.updatedAt.compareTo(seenDone) > 0) {
        final verb = t.status == 'qa'
            ? 'in QA'
            : t.status == 'review'
                ? 'ready for review'
                : 'done';
        await _plugin.show(id++, 'Task $verb', '${t.slug} · ${t.title}', _details);
        if (t.updatedAt.compareTo(maxDone) > 0) maxDone = t.updatedAt;
      }
    }

    await db.saveSyncState(
        lastSeenJson:
            jsonEncode({'escalations': maxEsc, 'tasks_done': maxDone}));
  }

  /// First run / re-pair: set anchors to "now" without notifying, so the
  /// user is not spammed with history.
  Future<void> anchorWithoutNotifying() async {
    final esc = await db.select(db.escalationRows).get();
    final tasks = await db.select(db.taskRows).get();
    String maxEsc = '', maxDone = '';
    for (final e in esc) {
      if (e.createdAt.compareTo(maxEsc) > 0) maxEsc = e.createdAt;
    }
    for (final t in tasks) {
      if (t.updatedAt.compareTo(maxDone) > 0) maxDone = t.updatedAt;
    }
    await db.saveSyncState(
        lastSeenJson:
            jsonEncode({'escalations': maxEsc, 'tasks_done': maxDone}));
  }
}
