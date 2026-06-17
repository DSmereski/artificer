// Connection owner for the offline-first spine.
//
//   disconnected --connect ok--> hydrating --pull done--> live
//        ^                            |                     | WS drop/error
//        +-------- backoff retry <----+---------------------+
//
// Hydrate = parallel REST pulls upserted into the DB. Live = WS frames
// patch the DB (board frames re-pull /board/state — frames are lossy
// signals, not deltas we trust blindly). Outbox drains on entering live
// and after every successful op, FIFO, ONE inflight at a time so action
// order is preserved (approve-then-reject must not reorder).
import 'dart:async';
import 'dart:convert';

import '../db/app_database.dart';
import '../repositories/activity_repository.dart';
import '../repositories/board_repository.dart';
import '../repositories/chat_repository.dart';
import '../repositories/escalation_repository.dart';
import 'sync_gateway.dart';

enum SyncPhase { disconnected, hydrating, live }

const List<Duration> kBackoffSteps = [
  Duration(seconds: 1),
  Duration(seconds: 2),
  Duration(seconds: 5),
  Duration(seconds: 15),
  Duration(seconds: 30),
];

class SyncService {
  SyncService({
    required this.db,
    required this.gateway,
    required this.board,
    EscalationRepository? escalations,
    ChatRepository? chat,
    ActivityRepository? activity,
  })  : escalations = escalations ?? EscalationRepository(db, nowMs: _now),
        chat = chat ?? ChatRepository(db, nowMs: _now),
        activity = activity ?? ActivityRepository(db);

  static int _now() => DateTime.now().millisecondsSinceEpoch;

  final AppDatabase db;
  final SyncGateway gateway;
  final BoardRepository board;
  final EscalationRepository escalations;
  final ChatRepository chat;
  final ActivityRepository activity;

  final _phaseCtrl = StreamController<SyncPhase>.broadcast();
  SyncPhase _phase = SyncPhase.disconnected;
  SyncPhase get phase => _phase;
  Stream<SyncPhase> get phaseStream => _phaseCtrl.stream;

  // Broadcast of every raw WS frame. activityFeedProvider taps this instead
  // of opening its own second connection to /v1/events.
  final _frameCtrl = StreamController<Map<String, dynamic>>.broadcast();
  Stream<Map<String, dynamic>> get frameStream => _frameCtrl.stream;

  // Board-paused flag from /board/state. Not a DB row — the offline-first
  // boardStateProvider rebuilds BoardState from drift rows and drops this
  // top-level field, so SyncService carries it on its own broadcast.
  final _pausedCtrl = StreamController<bool>.broadcast();
  bool _paused = false;
  bool get paused => _paused;
  Stream<bool> get pausedStream => _pausedCtrl.stream;
  void _setPaused(Map<String, dynamic> boardJson) {
    final p = boardJson['paused'] == true;
    if (p == _paused) return;
    _paused = p;
    if (!_pausedCtrl.isClosed) _pausedCtrl.add(p);
  }

  StreamSubscription<Map<String, dynamic>>? _ws;
  Timer? _retryTimer;
  Timer? _heartbeat; // periodic live-check; cancelled on drop/dispose
  Completer<void>? _wake; // resolved by _onDrop to wake the supervisor
  int _backoffIdx = 0;
  int _gen = 0; // bumped per connect attempt; stale attempts bail out
  bool _looping = false; // only one supervisor loop ever runs
  bool _draining = false;
  bool _repulling = false; // guard: collapse concurrent re-pull signals into one
  bool _disposed = false;

  void _setPhase(SyncPhase p) {
    if (_disposed || p == _phase) return;
    _phase = p;
    _phaseCtrl.add(p);
  }

  // ------------------------------------------------------------ lifecycle
  /// Production entry: a SINGLE supervisor loop owns (re)connection for the
  /// life of the service. Idempotent — calling start() twice is a no-op.
  void start() => unawaited(_loop());

  Future<void> _loop() async {
    if (_looping) return; // guard: never two supervisors
    _looping = true;
    try {
      while (!_disposed) {
        final ok = await connectOnce();
        if (_disposed) break;
        if (ok) {
          _backoffIdx = 0; // was live — reset backoff
          await _waitForWake(); // block until a drop/refresh wakes us
        } else {
          final d =
              kBackoffSteps[_backoffIdx.clamp(0, kBackoffSteps.length - 1)];
          _backoffIdx++;
          await _waitForWake(timeout: d); // backoff OR an early refresh wake
        }
      }
    } finally {
      _looping = false;
    }
  }

  /// Park the supervisor until [_wake] completes, or [timeout] elapses.
  /// Unifies the live-wait (no timeout) and the backoff-wait (timeout).
  Future<void> _waitForWake({Duration? timeout}) async {
    final c = Completer<void>();
    _wake = c;
    if (timeout != null) {
      _retryTimer = Timer(timeout, () {
        if (!c.isCompleted) c.complete();
      });
    }
    await c.future;
    _retryTimer?.cancel();
    _retryTimer = null;
    _wake = null;
  }

  /// One connect attempt. Hydrates BEFORE subscribing to the WS so an
  /// in-flight frame can't race the hydrate writes, and a generation token
  /// makes a superseded attempt bail without clobbering newer state.
  Future<bool> connectOnce() async {
    final myGen = ++_gen;
    StreamSubscription<Map<String, dynamic>>? sub;
    try {
      _setPhase(SyncPhase.hydrating);
      await hydrate();
      if (_disposed || myGen != _gen) return false; // superseded
      sub = gateway.events().listen(_onFrame,
          onError: (_) => _onDrop(), onDone: _onDrop, cancelOnError: false);
      _ws = sub;
      _setPhase(SyncPhase.live);
      await drainOutbox();
      // Heartbeat: re-pull every 30 s. If the server is unreachable
      // (NAT/WiFi→LTE silent drop with no close frame) the pull throws
      // → _onDrop() fires and the supervisor reconnects.
      _heartbeat?.cancel();
      _heartbeat = Timer.periodic(const Duration(seconds: 30), (_) {
        unawaited(_repullBoard());
      });
      return true;
    } catch (_) {
      await sub?.cancel();
      if (myGen == _gen) {
        _ws = null;
        _setPhase(SyncPhase.disconnected);
      }
      return false;
    }
  }

  /// WS error/done OR a failed live re-pull. Idempotent: collapses a burst
  /// of failures into ONE disconnect+wake (no duplicate loops).
  void _onDrop() {
    if (_disposed || _phase == SyncPhase.disconnected) return;
    _heartbeat?.cancel();
    _heartbeat = null;
    unawaited(_ws?.cancel());
    _ws = null;
    _setPhase(SyncPhase.disconnected);
    // Wake the supervisor (if running) to reconnect; if no supervisor is
    // running (e.g. a test called connectOnce directly), this is a no-op.
    final w = _wake;
    _wake = null;
    if (w != null && !w.isCompleted) w.complete();
  }

  /// Force a re-pull (app resume / connectivity regained). Safe in any phase.
  Future<void> refresh() async {
    if (_phase == SyncPhase.disconnected) {
      if (_looping) {
        // Supervisor owns reconnection — wake it (from live-wait or backoff).
        final w = _wake;
        if (w != null && !w.isCompleted) w.complete();
        return; // if mid-connect (_wake null), a connect is already in flight
      }
      await connectOnce(); // no supervisor (e.g. direct test use)
      return;
    }
    try {
      await hydrate();
      await drainOutbox();
    } catch (_) {
      _onDrop();
    }
  }

  void dispose() {
    _disposed = true;
    _heartbeat?.cancel();
    _heartbeat = null;
    _retryTimer?.cancel();
    _ws?.cancel();
    final w = _wake;
    _wake = null;
    if (w != null && !w.isCompleted) w.complete();
    _phaseCtrl.close();
    _pausedCtrl.close();
    _frameCtrl.close();
  }

  // -------------------------------------------------------------- hydrate
  Future<void> hydrate() async {
    final results = await Future.wait<Object?>([
      gateway.boardStateRaw(),
      gateway.escalationsRaw(),
      gateway.bots(),
    ]);
    final boardJson = results[0] as Map<String, dynamic>;
    _setPaused(boardJson);
    await board.replaceAllTasks(((boardJson['tasks'] ?? const []) as List)
        .map((e) => (e as Map).cast<String, dynamic>())
        .toList());
    await board.replaceAllProjects(
        ((boardJson['projects'] ?? const []) as List)
            .map((e) => (e as Map).cast<String, dynamic>())
            .toList());
    await escalations.replaceAll(results[1] as List<Map<String, dynamic>>);
    for (final bot in results[2] as List<String>) {
      await chat.appendMessages(bot, await gateway.chatMessagesRaw(bot));
    }
  }

  // ------------------------------------------------------------ WS frames
  static const _boardFrameTypes = {
    'task_progress', 'task_moved', 'task_created', 'task_updated',
    'board', 'approval',
  };

  void _onFrame(Map<String, dynamic> frame) {
    final type = '${frame['type'] ?? ''}';
    // Broadcast to all subscribers (e.g. activityFeedProvider) before DB work.
    if (!_frameCtrl.isClosed) _frameCtrl.add(frame);
    unawaited(activity.append(type, frame,
        ts: '${frame['ts'] ?? DateTime.now().toIso8601String()}'));
    if (_boardFrameTypes.contains(type)) {
      // Frames are lossy signals — re-pull authoritative board state.
      unawaited(_repullBoard());
    } else if (type == 'escalation') {
      unawaited(gateway.escalationsRaw().then(escalations.replaceAll));
    } else if (type == 'chat' && frame['bot'] != null) {
      final bot = '${frame['bot']}';
      unawaited(
          gateway.chatMessagesRaw(bot).then((m) => chat.appendMessages(bot, m)));
    }
  }

  Future<void> _repullBoard() async {
    if (_repulling) return;
    _repulling = true;
    try {
      final j = await gateway.boardStateRaw();
      _setPaused(j);
      await board.replaceAllTasks(((j['tasks'] ?? const []) as List)
          .map((e) => (e as Map).cast<String, dynamic>())
          .toList());
      await board.replaceAllProjects(((j['projects'] ?? const []) as List)
          .map((e) => (e as Map).cast<String, dynamic>())
          .toList());
    } catch (_) {
      _onDrop();
    } finally {
      _repulling = false;
    }
  }

  // --------------------------------------------------------------- outbox
  /// FIFO, one inflight at a time. Re-entrant-safe.
  Future<void> drainOutbox() async {
    if (_draining) return;
    _draining = true;
    try {
      while (true) {
        final row = await db.nextPending();
        if (row == null) break;
        await db.markInflight(row.id);
        try {
          await _execute(row);
          await db.completeOutbox(row.id);
        } catch (e) {
          if (e is _UnknownOp) {
            // Never executable — straight to failed.
            for (var i = 0; i < AppDatabase.maxAttempts; i++) {
              await db.failOutbox(row.id, e.toString());
            }
          } else {
            await db.failOutbox(row.id, e.toString());
          }
          break; // stop the drain; retry on next live/drain trigger
        }
      }
    } finally {
      _draining = false;
    }
  }

  Future<void> _execute(OutboxRow row) async {
    final p = (jsonDecode(row.payloadJson) as Map).cast<String, dynamic>();
    switch (row.op) {
      case 'move':
        await gateway.moveTask(row.targetId, p['to_status'] as String);
      case 'assign':
        await gateway.assignTask(row.targetId, p['assignee'] as String);
      case 'comment':
        await gateway.addComment(row.targetId, p['comment'] as String);
      case 'resolve_escalation':
        await gateway.resolveEscalation(row.targetId);
      case 'send_chat':
        await gateway.sendChat(row.targetId, p['text'] as String);
      default:
        throw _UnknownOp(row.op);
    }
  }
}

class _UnknownOp implements Exception {
  _UnknownOp(this.op);
  final String op;
  @override
  String toString() => 'unknown outbox op: $op';
}
