// Mirrors the payload from `gateway/routes/board.py::get_stats()`.
//
// Tokens are reported SEPARATELY for hive vs claude and must NEVER be
// summed. parseFailRate should sit near 0 after the P1 constrained-
// decoding upgrade.

class BoardStats {
  BoardStats({
    required this.byStatus,
    required this.byAssignee,
    required this.hiveTokens,
    required this.claudeTokens,
    required this.avgHiveTokensPerTask,
    required this.avgClaudeTokensPerTask,
    required this.avgAttempts,
    required this.smokePass,
    required this.smokeFail,
    required this.lessons,
    required this.parseFailRate,
    required this.parseFailTurns,
    required this.topProjects,
  });

  final Map<String, int> byStatus;
  final Map<String, int> byAssignee;

  // SEPARATE — never combined.
  final int hiveTokens;
  final int claudeTokens;
  final int avgHiveTokensPerTask;
  final int avgClaudeTokensPerTask;

  final double avgAttempts;
  final int smokePass;
  final int smokeFail;
  final int lessons;
  final double parseFailRate; // 0.0..1.0
  final int parseFailTurns;
  final List<BoardProjectStat> topProjects;

  static Map<String, int> _intMap(dynamic v) {
    if (v is! Map) return {};
    return v.map((k, val) => MapEntry(k.toString(), (val ?? 0) as int));
  }

  factory BoardStats.fromJson(Map<String, dynamic> j) {
    final tok = (j['tokens'] ?? const {}) as Map;
    final avg = (j['avg_tokens_per_task'] ?? const {}) as Map;
    final smoke = (j['smoke'] ?? const {}) as Map;
    final pf = (j['parse_fail'] ?? const {}) as Map;
    return BoardStats(
      byStatus: _intMap(j['by_status']),
      byAssignee: _intMap(j['by_assignee']),
      hiveTokens: (tok['hive'] ?? 0) as int,
      claudeTokens: (tok['claude'] ?? 0) as int,
      avgHiveTokensPerTask: (avg['hive'] ?? 0) as int,
      avgClaudeTokensPerTask: (avg['claude'] ?? 0) as int,
      avgAttempts: ((j['avg_attempts'] ?? 0) as num).toDouble(),
      smokePass: (smoke['pass'] ?? 0) as int,
      smokeFail: (smoke['fail'] ?? 0) as int,
      lessons: (j['lessons'] ?? 0) as int,
      parseFailRate: ((pf['rate'] ?? 0) as num).toDouble(),
      parseFailTurns: (pf['turns'] ?? 0) as int,
      topProjects: ((j['top_projects'] ?? const []) as List)
          .whereType<Map>()
          .map((e) => BoardProjectStat.fromJson(e.cast<String, dynamic>()))
          .toList(),
    );
  }
}

class BoardProjectStat {
  BoardProjectStat({
    required this.slug,
    required this.done,
    required this.active,
    required this.hiveTokens,
    required this.claudeTokens,
  });

  final String slug;
  final int done;
  final int active;
  final int hiveTokens;
  final int claudeTokens;

  factory BoardProjectStat.fromJson(Map<String, dynamic> j) => BoardProjectStat(
        slug: (j['slug'] ?? '') as String,
        done: (j['done'] ?? 0) as int,
        active: (j['active'] ?? 0) as int,
        hiveTokens: (j['hive_tokens'] ?? 0) as int,
        claudeTokens: (j['claude_tokens'] ?? 0) as int,
      );
}
