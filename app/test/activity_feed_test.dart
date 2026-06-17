import 'package:flutter_test/flutter_test.dart';
import 'package:ai_team_app_v2/state/activity_feed.dart';
import 'package:ai_team_app_v2/models/digest.dart';
import 'package:ai_team_app_v2/models/escalation.dart';

void main() {
  group('ActivityEvent.fromFrame', () {
    test('maps a board_event to a human row', () {
      final e = ActivityEvent.fromFrame(
          {'type': 'board_event', 'event': 'escalated', 'task': 'T-9'}, 0);
      expect(e, isNotNull);
      expect(e!.title, 'Task escalated to Claude');
      expect(e.detail, 'T-9');
    });

    test('maps hive_turn_done preview', () {
      final e = ActivityEvent.fromFrame(
          {'type': 'hive_turn_done', 'preview': 'hello there'}, 1);
      expect(e!.title, 'Hive replied');
      expect(e.detail, 'hello there');
    });

    test('ignores plumbing frames', () {
      expect(ActivityEvent.fromFrame({'type': 'queued'}, 0), isNull);
      expect(ActivityEvent.fromFrame({'type': ''}, 0), isNull);
    });
  });

  group('Digest', () {
    test('parses counts + total', () {
      final d = Digest.fromJson({
        'since': 100,
        'new_images': 2,
        'new_escalations': 1,
        'new_pinned_turns': 0,
        'completed_calendar_fires': 3,
      });
      expect(d.total, 6);
      expect(d.hasNews, isTrue);
      expect(d.newEscalations, 1);
    });

    test('hasNews false when empty', () {
      expect(Digest.fromJson({'since': 0}).hasNews, isFalse);
    });
  });

  group('Escalation', () {
    test('parses + defaults', () {
      final e = Escalation.fromJson({
        'slug': 'esc-1',
        'title': 'broke',
        'severity': 'high',
        'resolved': false,
      });
      expect(e.slug, 'esc-1');
      expect(e.severity, 'high');
      expect(e.resolved, isFalse);
    });
  });
}
