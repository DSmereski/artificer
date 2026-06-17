// Mirrors gateway/escalation_store.py::Escalation.to_json().
// A Hive-flagged bug/feature request awaiting the dev (Claude Code).

class Escalation {
  Escalation({
    required this.slug,
    required this.title,
    required this.severity,
    required this.reportedAt,
    required this.summary,
    required this.context,
    required this.userMsg,
    required this.resolved,
  });

  final String slug;
  final String title;
  final String severity; // low | medium | high
  final String reportedAt;
  final String summary;
  final String context;
  final String userMsg;
  final bool resolved;

  factory Escalation.fromJson(Map<String, dynamic> j) => Escalation(
        slug: (j['slug'] ?? '') as String,
        title: (j['title'] ?? '') as String,
        severity: (j['severity'] ?? 'medium') as String,
        reportedAt: (j['reported_at'] ?? '') as String,
        summary: (j['summary'] ?? '') as String,
        context: (j['context'] ?? '') as String,
        userMsg: (j['user_msg'] ?? '') as String,
        resolved: (j['resolved'] ?? false) as bool,
      );
}
