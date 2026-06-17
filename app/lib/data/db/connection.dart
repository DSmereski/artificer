import 'package:drift/drift.dart';
import 'package:drift_flutter/drift_flutter.dart';

/// Runtime (device) database. Tests use NativeDatabase.memory() instead.
QueryExecutor openAppConnection() =>
    driftDatabase(name: 'ai_team_v2');
