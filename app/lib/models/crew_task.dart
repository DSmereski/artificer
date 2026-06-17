// Mirrors `gateway/routes/board.py::_task_to_dict()` + `_project_to_dict()`.
//
// The Crew Board is the kanban that drives the hive coding pipeline
// (proposed → backlog → ready → in_progress → review → done → archived).
// Tasks carry per-worker token usage that is tracked SEPARATELY and must
// NEVER be summed: `hiveTokens` (Ollama) vs `claudeTokens` (Claude CLI).

/// Canonical board columns, in flow order. Mirrors
/// `gateway/crew_board/schema.py::ALL_STATUSES`.
const List<String> kCrewStatuses = [
  'proposed',
  'backlog',
  'ready',
  'in_progress',
  'qa',
  'review',
  'done',
  'archived',
];

class CrewTask {
  CrewTask({
    required this.slug,
    required this.title,
    required this.body,
    required this.status,
    required this.projectSlug,
    required this.assignee,
    required this.createdBy,
    required this.priority,
    required this.acceptanceCriteria,
    required this.filesOfInterest,
    required this.attemptCount,
    required this.hiveTokens,
    required this.claudeTokens,
    required this.reviewBy,
    required this.smokeOk,
    required this.verifyResults,
    required this.createdAt,
    required this.updatedAt,
  });

  final String slug;
  final String title;
  final String body;
  final String status;
  final String projectSlug;
  final String assignee; // none | hive | claude-code | owner
  final String createdBy;
  final String priority; // low | medium | high

  /// Each entry: `{text, checked}`. Owner ticks them during review.
  final List<Map<String, dynamic>> acceptanceCriteria;
  final List<String> filesOfInterest;
  final int attemptCount;

  /// Token usage — tracked SEPARATELY, NEVER combined into one number.
  final int hiveTokens; // Ollama eval tokens (qwen3.6 etc.)
  final int claudeTokens; // Claude CLI tokens

  final String? reviewBy; // reviewer agent, or null = owner reviews
  final bool? smokeOk; // null = no smoke gate ran
  final Map<String, dynamic> verifyResults;
  final String createdAt;
  final String updatedAt;

  bool get isArchived => status == 'archived';

  factory CrewTask.fromJson(Map<String, dynamic> j) => CrewTask(
        slug: (j['slug'] ?? '') as String,
        title: (j['title'] ?? '') as String,
        body: (j['body'] ?? '') as String,
        status: (j['status'] ?? 'proposed') as String,
        projectSlug: (j['project_slug'] ?? '') as String,
        assignee: (j['assignee'] ?? 'none') as String,
        createdBy: (j['created_by'] ?? 'owner') as String,
        priority: (j['priority'] ?? 'medium') as String,
        acceptanceCriteria: ((j['acceptance_criteria'] ?? const []) as List)
            .whereType<Map>()
            .map((e) => e.cast<String, dynamic>())
            .toList(),
        filesOfInterest: ((j['files_of_interest'] ?? const []) as List)
            .map((e) => e.toString())
            .toList(),
        attemptCount: (j['attempt_count'] ?? 0) as int,
        hiveTokens: (j['hive_tokens'] ?? 0) as int,
        claudeTokens: (j['claude_tokens'] ?? 0) as int,
        reviewBy: j['review_by'] as String?,
        smokeOk: j['smoke_ok'] as bool?,
        verifyResults: ((j['verify_results'] ?? const {}) as Map)
            .cast<String, dynamic>(),
        createdAt: (j['created_at'] ?? '') as String,
        updatedAt: (j['updated_at'] ?? '') as String,
      );
}

class CrewProject {
  CrewProject({
    required this.slug,
    required this.name,
    required this.enabled,
    required this.pushAllowed,
    required this.parallel,
  });

  final String slug;
  final String name;
  final bool enabled;
  final bool pushAllowed;
  final bool parallel;

  factory CrewProject.fromJson(Map<String, dynamic> j) => CrewProject(
        slug: (j['slug'] ?? '') as String,
        name: (j['name'] ?? '') as String,
        enabled: (j['enabled'] ?? false) as bool,
        pushAllowed: (j['push_allowed'] ?? false) as bool,
        parallel: (j['parallel'] ?? false) as bool,
      );
}

/// Snapshot returned by `GET /board/state`.
class BoardState {
  BoardState({
    required this.tasks,
    required this.projects,
    this.paused = false,
  });

  final List<CrewTask> tasks;
  final List<CrewProject> projects;

  /// True when the dispatcher is paused — no new work will be started.
  /// Persisted on the gateway; survives restart.
  final bool paused;

  /// Live (non-archived) tasks grouped by status, in column order.
  Map<String, List<CrewTask>> get byColumn {
    final out = <String, List<CrewTask>>{
      for (final s in kCrewStatuses) s: <CrewTask>[],
    };
    for (final t in tasks) {
      (out[t.status] ??= <CrewTask>[]).add(t);
    }
    return out;
  }

  factory BoardState.fromJson(Map<String, dynamic> j) => BoardState(
        tasks: ((j['tasks'] ?? const []) as List)
            .cast<Map<String, dynamic>>()
            .map(CrewTask.fromJson)
            .toList(),
        projects: ((j['projects'] ?? const []) as List)
            .cast<Map<String, dynamic>>()
            .map(CrewProject.fromJson)
            .toList(),
        paused: (j['paused'] ?? false) as bool,
      );
}
