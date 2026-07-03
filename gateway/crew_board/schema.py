"""Schema for the Crew Board tables. Lives in the same SQLite file as
the vault index (`.vault-writer/vault.db`) but in distinct tables
prefixed `crew_*` so a vault audit won't trip over them.

Idempotent: every CREATE uses IF NOT EXISTS so re-running on an
existing DB is a no-op. Indexes ditto.
"""

from __future__ import annotations

import sqlite3

# Columns are declared in the order that's most useful for SELECT *
# debugging (identity → state → metadata → audit fields).
_TABLES = (
    """
    CREATE TABLE IF NOT EXISTS crew_projects (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        slug          TEXT NOT NULL UNIQUE,
        path          TEXT NOT NULL,
        name          TEXT NOT NULL,
        enabled       INTEGER NOT NULL DEFAULT 0,
        push_allowed  INTEGER NOT NULL DEFAULT 0,
        test_cmd      TEXT,
        created_at    TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS crew_tasks (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        slug                TEXT NOT NULL UNIQUE,
        title               TEXT NOT NULL,
        body                TEXT NOT NULL DEFAULT '',
        status              TEXT NOT NULL DEFAULT 'proposed',
        project_slug        TEXT NOT NULL,
        assignee            TEXT NOT NULL DEFAULT 'none',
        created_by          TEXT NOT NULL DEFAULT 'owner',
        priority            TEXT NOT NULL DEFAULT 'medium',
        estimate            TEXT,
        acceptance_criteria TEXT NOT NULL DEFAULT '[]',
        files_of_interest   TEXT NOT NULL DEFAULT '[]',
        depends_on          TEXT NOT NULL DEFAULT '[]',
        tags                TEXT NOT NULL DEFAULT '[]',
        attempt_count       INTEGER NOT NULL DEFAULT 0,
        last_branch         TEXT,
        last_pr_url         TEXT,
        verify_results      TEXT NOT NULL DEFAULT '{}',
        created_at          TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS crew_audit (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        task_slug   TEXT NOT NULL,
        actor       TEXT NOT NULL,
        action      TEXT NOT NULL,
        detail      TEXT NOT NULL DEFAULT '',
        metadata    TEXT NOT NULL DEFAULT '{}',
        created_at  TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS crew_approvals (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        task_slug    TEXT NOT NULL,
        requested_by TEXT NOT NULL,
        kind         TEXT NOT NULL,
        summary      TEXT NOT NULL,
        payload      TEXT NOT NULL DEFAULT '{}',
        status       TEXT NOT NULL DEFAULT 'pending',
        created_at   TEXT NOT NULL DEFAULT (datetime('now')),
        resolved_at  TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS crew_task_counter (
        scope  TEXT PRIMARY KEY,
        next_n INTEGER NOT NULL
    )
    """,
    # Lesson notes distilled after an escalation rescue. Seeded into
    # future task briefs for the same project so the next attempt does
    # not repeat the mistake claude just fixed.
    """
    CREATE TABLE IF NOT EXISTS crew_lessons (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        project_slug  TEXT NOT NULL,
        task_slug     TEXT NOT NULL DEFAULT '',
        tags          TEXT NOT NULL DEFAULT '[]',
        body          TEXT NOT NULL,
        created_at    TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
)

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_crew_tasks_status   ON crew_tasks(status)",
    "CREATE INDEX IF NOT EXISTS idx_crew_tasks_project  ON crew_tasks(project_slug)",
    "CREATE INDEX IF NOT EXISTS idx_crew_tasks_assignee ON crew_tasks(assignee)",
    "CREATE INDEX IF NOT EXISTS idx_crew_audit_task     ON crew_audit(task_slug)",
    "CREATE INDEX IF NOT EXISTS idx_crew_approvals_task ON crew_approvals(task_slug)",
    "CREATE INDEX IF NOT EXISTS idx_crew_approvals_status ON crew_approvals(status)",
    "CREATE INDEX IF NOT EXISTS idx_crew_lessons_project ON crew_lessons(project_slug)",
)


def apply(conn: sqlite3.Connection) -> None:
    """Create every Crew Board table + index. Safe to call repeatedly.

    Seeds the task slug counter at 1 on first run; subsequent calls
    leave it untouched.
    """
    for stmt in _TABLES:
        conn.execute(stmt)
    for stmt in _INDEXES:
        conn.execute(stmt)
    _apply_migrations(conn)
    conn.execute(
        "INSERT OR IGNORE INTO crew_task_counter (scope, next_n) VALUES ('task', 1)"
    )
    conn.commit()


def _apply_migrations(conn: sqlite3.Connection) -> None:
    """Idempotent ALTER TABLE adds for fields added after first ship.
    sqlite ALTER TABLE only supports ADD COLUMN; safe to no-op on
    existing schemas via try/except on 'duplicate column'."""
    # crew_meta: generic key-value store for board-level settings (e.g. the
    # pause flag). Created here so it survives on any existing DB.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS crew_meta (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    # P2 v-Next: board registry table
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS crew_boards (
            board_id    TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            created_at  TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    # Seed the default board so list_boards() always returns at least one entry.
    conn.execute(
        "INSERT OR IGNORE INTO crew_boards (board_id, name, description) "
        "VALUES ('default', 'Default', 'Default board')"
    )

    new_columns = (
        # (table, column, type, default)
        ("crew_tasks", "review_by", "TEXT", "NULL"),
        ("crew_tasks", "polish_iters", "INTEGER", "NULL"),
        # Optional shell command run by the verifier AFTER pytest. The
        # cmd runs in the project dir; non-zero exit fails the tier.
        # Used to catch "tests pass but the live binary is broken"
        # bugs like an all-black-fog render screen on a generated game demo.
        ("crew_tasks", "smoke_cmd", "TEXT", "NULL"),
        # Token usage, tracked SEPARATELY per worker — never summed.
        # hive_tokens = Ollama eval tokens (qwen3.6 etc.);
        # claude_tokens = Claude CLI usage. A ticket worked by both
        # (hive then escalated to claude) shows two distinct numbers.
        ("crew_tasks", "hive_tokens", "INTEGER", "0"),
        ("crew_tasks", "claude_tokens", "INTEGER", "0"),
        # Heartbeat: runner bumps this each turn so the reaper can tell
        # a live long-running task from a crash-orphaned one.
        ("crew_tasks", "heartbeat_at", "TEXT", "NULL"),
        # Opt-in parallel lanes: when 1, the dispatcher runs each task in
        # its own git worktree (.crew-worktrees/<slug>) and allows more
        # than one concurrent task for the assignee, capped by lane count.
        ("crew_projects", "parallel", "INTEGER", "0"),
        # Real "last worked on" timestamp (ISO-8601 UTC), computed by the
        # scanner as max(git HEAD commit time, dir mtime). Distinct from
        # updated_at, which only marks the last DB upsert (≈ last scan) and
        # is therefore useless for sorting projects by recency.
        ("crew_projects", "modified_at", "TEXT", "NULL"),
        # Agent-turn telemetry, accumulated by the hive loop. Lets the
        # stats endpoint compute the parse-fail rate from a cheap SUM
        # instead of scanning transcript files on every poll.
        ("crew_tasks", "agent_turns", "INTEGER", "0"),
        ("crew_tasks", "parse_fails", "INTEGER", "0"),
        # Live "what the agent is doing right now" — the latest turn's
        # tool action (e.g. "turn 12 · write_file game.py"). Surfaced on
        # the board so you can watch the hive work in real time.
        ("crew_tasks", "last_action", "TEXT", "NULL"),
        # Content requests: kind='content' tasks are produced by the Image/
        # Video shims instead of a code runner. content_spec is JSON holding
        # the request + results: {type, prompt, count, width, height, loras,
        # seed_media_id, result_media_ids}.
        ("crew_tasks", "kind", "TEXT", "'code'"),
        ("crew_tasks", "content_spec", "TEXT", "'{}'"),
        # P6 goal-completion loop: the goal_id this subtask belongs to.
        # Also stored as a 'goal:<id>' tag but having it as a column lets
        # the dispatcher query siblings without scanning tags JSON.
        # NULL for tasks not created via /board/decompose with a goal.
        ("crew_tasks", "goal_id", "TEXT", "NULL"),
        # P2 v-Next: which board this task belongs to. Defaults to 'default'
        # for all existing rows (back-compat). The board_id must exist in
        # crew_boards. A separate index (added below) makes filtered reads fast.
        ("crew_tasks", "board_id", "TEXT", "'default'"),
        # #198: short handoff summary from the LAST agent that worked the
        # task ("what I did + current state + next step") so opening a ticket
        # shows where it was left without reading the transcript. Overwritten
        # on every agent touch; _by/_at record which model/agent and when.
        ("crew_tasks", "last_summary", "TEXT", "NULL"),
        ("crew_tasks", "last_summary_by", "TEXT", "NULL"),
        ("crew_tasks", "last_summary_at", "TEXT", "NULL"),
        # CP1: live agent reasoning shown inside the ticket. live_thoughts is a
        # capped JSON array of {t:turn, th:thought, a:action}; steer_message is a
        # pending owner nudge the loop injects on its next turn then clears.
        ("crew_tasks", "live_thoughts", "TEXT", "'[]'"),
        ("crew_tasks", "steer_message", "TEXT", "NULL"),
        # CP2: master-plan spec for kind='plan' tickets in proposed —
        # {goal, assumptions[], open_questions[], steps[{title,why,verify,criteria[]}]}.
        ("crew_tasks", "plan_spec", "TEXT", "'{}'"),
    )
    for table, col, typ, default in new_columns:
        try:
            conn.execute(
                f"ALTER TABLE {table} ADD COLUMN {col} {typ} DEFAULT {default}"
            )
        except sqlite3.OperationalError as e:
            if "duplicate column" in str(e).lower():
                continue
            raise

    # P2 v-Next: index on board_id — must be created AFTER the column migration
    # above, not in _INDEXES (which runs before _apply_migrations).
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_crew_tasks_board_id ON crew_tasks(board_id)"
    )


# Status state machine. Values used in code, never look up by ordinal.
STATUS_PROPOSED = "proposed"
STATUS_BACKLOG = "backlog"
STATUS_READY = "ready"
STATUS_IN_PROGRESS = "in_progress"
# QA sits between build and review: claude writes automated tests covering
# the acceptance criteria, runs them, then either promotes to review (pass)
# or bounces back to ready (fail, builder must fix). Verify already passed
# (tests+files+smoke) before the task reaches QA — QA ADDS new tests, it
# does not re-run existing ones in isolation.
STATUS_QA = "qa"
STATUS_REVIEW = "review"
STATUS_DONE = "done"
STATUS_ARCHIVED = "archived"

ALL_STATUSES = (
    STATUS_PROPOSED,
    STATUS_BACKLOG,
    STATUS_READY,
    STATUS_IN_PROGRESS,
    STATUS_QA,
    STATUS_REVIEW,
    STATUS_DONE,
    STATUS_ARCHIVED,
)

# Allowed transitions. Keys are "from" status; values are sets of
# "to" status. Any move outside these is rejected by the store.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    STATUS_PROPOSED: frozenset({STATUS_BACKLOG, STATUS_ARCHIVED}),
    STATUS_BACKLOG: frozenset({STATUS_READY, STATUS_PROPOSED, STATUS_ARCHIVED}),
    STATUS_READY: frozenset({STATUS_IN_PROGRESS, STATUS_BACKLOG, STATUS_ARCHIVED}),
    # REVIEW kept in in_progress transitions so the max-attempts park path
    # (hard cap) can still bypass QA and land directly in review for owner
    # triage. Normal build-success path goes in_progress→qa instead.
    STATUS_IN_PROGRESS: frozenset({STATUS_QA, STATUS_REVIEW, STATUS_READY, STATUS_ARCHIVED}),
    STATUS_QA: frozenset({STATUS_REVIEW, STATUS_READY, STATUS_ARCHIVED}),
    STATUS_REVIEW: frozenset({STATUS_DONE, STATUS_IN_PROGRESS, STATUS_READY, STATUS_ARCHIVED}),
    STATUS_DONE: frozenset({STATUS_ARCHIVED}),
    STATUS_ARCHIVED: frozenset(),
}

# Roster of valid assignees. "none" = no one yet (default for new tasks).
ASSIGNEES = frozenset({"none", "hive", "claude-code", "owner", "content"})

# Priorities (sortable by ordinal).
PRIORITIES = ("low", "medium", "high")
ESTIMATES = (None, "xs", "s", "m", "l", "xl")
