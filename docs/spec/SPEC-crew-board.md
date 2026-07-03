# SPEC: Crew Board — Multi-Agent Kanban Task Board

Reimplementation-grade. Derived from `gateway/crew_board/schema.py`,
`gateway/crew_board/store.py`, `gateway/routes/board.py`. Anything not
stated here is unspecified — do not assume.

## 1. Purpose

A kanban board where autonomous coding agents ("hive") and a human owner
share a queue of tickets across multiple git projects. Tickets move
through a fixed state machine. A natural-language "goal" can be
decomposed by an LLM planner into a dependency-chained ticket set; a
completion loop verifies the result and auto-spawns bounded rework
cycles. Storage: one SQLite file, tables prefixed `crew_`.

## 2. Data Model

### 2.1 Task (`crew_tasks`)

Slug = `T-%04d` (e.g. `T-0001`), minted server-side from a single
monotonic counter — never reused, never derived from title/UUID.

| Field | Type | Default | Notes |
|---|---|---|---|
| `id` | INTEGER PK AUTOINCREMENT | — | |
| `slug` | TEXT UNIQUE NOT NULL | — | `T-%04d` |
| `title` | TEXT NOT NULL | — | |
| `body` | TEXT NOT NULL | `''` | |
| `status` | TEXT NOT NULL | `'proposed'` | §2.2 |
| `project_slug` | TEXT NOT NULL | — | FK by convention, no DB FK |
| `assignee` | TEXT NOT NULL | `'none'` | §2.3 |
| `created_by` | TEXT NOT NULL | `'owner'` | `'owner'` sets initial status, §2.5 |
| `priority` | TEXT NOT NULL | `'medium'` | `low`\|`medium`\|`high` |
| `estimate` | TEXT | `NULL` | `NULL`\|`xs`\|`s`\|`m`\|`l`\|`xl` |
| `acceptance_criteria` | JSON array NOT NULL | `'[]'` | `[{"text":str,"checked":bool}]` |
| `files_of_interest` | JSON array NOT NULL | `'[]'` | relative paths |
| `depends_on` | JSON array NOT NULL | `'[]'` | blocking slugs, §2.4 |
| `tags` | JSON array NOT NULL | `'[]'` | goal tickets carry `goal:<id>` |
| `attempt_count` | INTEGER NOT NULL | `0` | |
| `last_branch` | TEXT | `NULL` | |
| `last_pr_url` | TEXT | `NULL` | |
| `verify_results` | JSON object NOT NULL | `'{}'` | e.g. `{"smoke":{"ran":bool,"exit_code":int}}` |
| `created_at`/`updated_at` | TEXT | `datetime('now')` | UTC |
| `review_by` | TEXT | `NULL` | reviewer gate, REVIEW→DONE |
| `polish_iters` | INTEGER | `NULL` | if >2: hold auto-done until N consecutive green runs |
| `smoke_cmd` | TEXT | `NULL` | run after tests pass; nonzero exit fails the tier |
| `hive_tokens` | INTEGER NOT NULL | `0` | local-model tokens — tracked SEPARATELY |
| `claude_tokens` | INTEGER NOT NULL | `0` | escalation tokens — never summed with hive |
| `heartbeat_at` | TEXT | `NULL` | last runner heartbeat |
| `agent_turns` | INTEGER NOT NULL | `0` | |
| `parse_fails` | INTEGER NOT NULL | `0` | unparseable agent replies |
| `last_action` | TEXT | `NULL` | "turn N · tool target", ≤200 chars |
| `kind` | TEXT NOT NULL | `'code'` | `code`\|`content`\|`plan` |
| `content_spec` | JSON object NOT NULL | `'{}'` | for `kind='content'`, §4.5 |
| `goal_id` | TEXT | `NULL` | groups one decompose's subtasks, §5 |
| `board_id` | TEXT NOT NULL | `'default'` | §2.7 |
| `last_summary` | TEXT | `NULL` | last agent handoff note, ≤1200 chars |
| `last_summary_by`/`last_summary_at` | TEXT | `NULL` | author ≤80 chars / timestamp |
| `live_thoughts` | JSON array NOT NULL | `'[]'` | ring buffer cap 12: `{"t":turn,"th":thought≤300,"a":action≤120}` |
| `steer_message` | TEXT | `NULL` | one-shot owner nudge, cleared after injection |
| `plan_spec` | JSON object NOT NULL | `'{}'` | for `kind='plan'`, §4.4 |

### 2.2 Status enum + state machine

`STATUSES = proposed | backlog | ready | in_progress | qa | review | done | archived`

`ALLOWED_TRANSITIONS` — the only legal moves; anything else raises
(`ValueError` in the store, HTTP 400 at the API):

| From | Allowed To |
|---|---|
| `proposed` | `backlog`, `archived` |
| `backlog` | `ready`, `proposed`, `archived` |
| `ready` | `in_progress`, `backlog`, `archived` |
| `in_progress` | `qa`, `review`, `ready`, `archived` |
| `qa` | `review`, `ready`, `archived` |
| `review` | `done`, `in_progress`, `ready`, `archived` |
| `done` | `archived` |
| `archived` | *(terminal)* |

Notes: `in_progress → review` deliberately bypasses `qa` (park-at-max-attempts
path); normal success path is `in_progress → qa → review → done`. QA does
not re-run the verify gate — it adds new tests after verify already
passed. A task entering `review` needs `review_by` set separately (via
`set_review_by`) or the review loop never picks it up; `move_task` does
not enforce this. `archive_old_done(retention_days)` bulk-sweeps
`done → archived` for stale rows and is the only mutation that skips the
audit row (automated maintenance).

### 2.3 Assignee

`ASSIGNEES = none | hive | claude-code | owner | content` — `none` is
default. `assign_task` validates against this set (`ValueError` on
unknown) and audits `from → to`.

### 2.4 Dependencies (`depends_on`)

JSON array of blocking slugs. `set_depends_on` drops self-refs/blanks,
rejects unknown slugs, rejects a *direct* cycle (blocker already lists
`slug` in its own `depends_on`). `done_slugs()` returns `done` ∪
`archived` — an archived (not just done) blocker still unblocks
dependents. A dispatcher (out of scope here) should poll `done_slugs()`
before claiming a task with non-empty `depends_on`.

### 2.5 Creation status rule

`created_by == "owner"` → starts `backlog`. Any other value (bot/system/
planner) → starts `proposed`.

### 2.6 Audit trail (`crew_audit`)

Every mutating store call inserts `(task_slug, actor, action, detail,
metadata JSON, created_at)`. Actions: `create`, `move`, `assign`,
`set_depends_on`, `attempt`, `verify`, `comment`, `update_criteria`,
`approval_request`, `approval_resolve`. No row-versioning of `crew_tasks`
itself — history is reconstructed entirely from this table.

### 2.7 Project (`crew_projects`)

| Field | Type | Default |
|---|---|---|
| `id` | INTEGER PK | |
| `slug` | TEXT UNIQUE NOT NULL | |
| `path` | TEXT NOT NULL | absolute git working-tree path |
| `name` | TEXT NOT NULL | |
| `enabled`/`push_allowed` | INTEGER bool NOT NULL | `0` |
| `test_cmd` | TEXT | `NULL` |
| `parallel` | INTEGER bool NOT NULL | `0` — one git worktree per task, allows >1 concurrent |
| `modified_at` | TEXT | `NULL` — max(git HEAD time, dir mtime); distinct from `updated_at` (last scan) |
| `created_at`/`updated_at` | TEXT | `datetime('now')` |

`upsert_project` = insert-or-update by `slug`. `delete_project` does NOT
cascade to tasks — the API enforces a check/force gate (§4.3).

### 2.8 Board (`crew_boards`) — multi-board registry

`board_id` PK, `name`, `description` (default `''`), `created_at`. A
`'default'` board is auto-seeded; `crew_tasks.board_id` defaults to it.
Task queries take an optional `board_id` filter — omitted means all boards.

### 2.9 Approval / Lesson / Counter

- **`crew_approvals`**: generic pending-decision queue, independent of
  the task FSM — `id, task_slug, requested_by, kind, summary,
  payload(JSON), status('pending'→'approved'|'denied'), created_at,
  resolved_at`.
- **`crew_lessons`**: post-mortem notes from an escalation rescue, fed
  into future task briefs. `relevant_lessons(project, title, body)` ranks
  by 4+-char keyword overlap (regex `[a-z]{4,}`), recency as tiebreaker.
- **`crew_task_counter`**: single row `('task', next_n)` — the only slug
  source; read-increment on every `create_task`.

## 3. SQLite Schema (DDL)

Same DB file as the host app's other tables, isolated by the `crew_`
prefix. Every `CREATE` uses `IF NOT EXISTS`; new columns are added via
best-effort `ALTER TABLE ... ADD COLUMN`, swallowing "duplicate column" —
`apply(conn)` is safe to call on every process start at any DB version.

```sql
CREATE TABLE IF NOT EXISTS crew_projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL UNIQUE, path TEXT NOT NULL, name TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0, push_allowed INTEGER NOT NULL DEFAULT 0,
    test_cmd TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    -- migrated: parallel INTEGER DEFAULT 0, modified_at TEXT
);

CREATE TABLE IF NOT EXISTS crew_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL UNIQUE, title TEXT NOT NULL, body TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'proposed', project_slug TEXT NOT NULL,
    assignee TEXT NOT NULL DEFAULT 'none', created_by TEXT NOT NULL DEFAULT 'owner',
    priority TEXT NOT NULL DEFAULT 'medium', estimate TEXT,
    acceptance_criteria TEXT NOT NULL DEFAULT '[]', files_of_interest TEXT NOT NULL DEFAULT '[]',
    depends_on TEXT NOT NULL DEFAULT '[]', tags TEXT NOT NULL DEFAULT '[]',
    attempt_count INTEGER NOT NULL DEFAULT 0, last_branch TEXT, last_pr_url TEXT,
    verify_results TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    -- migrated: review_by TEXT, polish_iters INTEGER, smoke_cmd TEXT,
    --   hive_tokens INTEGER DEFAULT 0, claude_tokens INTEGER DEFAULT 0,
    --   heartbeat_at TEXT, agent_turns INTEGER DEFAULT 0, parse_fails INTEGER DEFAULT 0,
    --   last_action TEXT, kind TEXT DEFAULT 'code', content_spec TEXT DEFAULT '{}',
    --   goal_id TEXT, board_id TEXT DEFAULT 'default',
    --   last_summary TEXT, last_summary_by TEXT, last_summary_at TEXT,
    --   live_thoughts TEXT DEFAULT '[]', steer_message TEXT, plan_spec TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS crew_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT, task_slug TEXT NOT NULL, actor TEXT NOT NULL,
    action TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '', metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS crew_approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT, task_slug TEXT NOT NULL, requested_by TEXT NOT NULL,
    kind TEXT NOT NULL, summary TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL DEFAULT (datetime('now')), resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS crew_task_counter (scope TEXT PRIMARY KEY, next_n INTEGER NOT NULL);
-- seeded: INSERT OR IGNORE INTO crew_task_counter VALUES ('task', 1);

CREATE TABLE IF NOT EXISTS crew_lessons (
    id INTEGER PRIMARY KEY AUTOINCREMENT, project_slug TEXT NOT NULL,
    task_slug TEXT NOT NULL DEFAULT '', tags TEXT NOT NULL DEFAULT '[]', body TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS crew_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS crew_boards (
    board_id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
-- seeded: INSERT OR IGNORE INTO crew_boards VALUES ('default','Default','Default board',...);

CREATE INDEX IF NOT EXISTS idx_crew_tasks_status     ON crew_tasks(status);
CREATE INDEX IF NOT EXISTS idx_crew_tasks_project    ON crew_tasks(project_slug);
CREATE INDEX IF NOT EXISTS idx_crew_tasks_assignee   ON crew_tasks(assignee);
CREATE INDEX IF NOT EXISTS idx_crew_audit_task       ON crew_audit(task_slug);
CREATE INDEX IF NOT EXISTS idx_crew_approvals_task   ON crew_approvals(task_slug);
CREATE INDEX IF NOT EXISTS idx_crew_approvals_status ON crew_approvals(status);
CREATE INDEX IF NOT EXISTS idx_crew_lessons_project  ON crew_lessons(project_slug);
CREATE INDEX IF NOT EXISTS idx_crew_tasks_board_id   ON crew_tasks(board_id); -- after board_id migration
```

Pragmas: `journal_mode=WAL` (lets a second writer process coexist without
`SQLITE_BUSY`), `busy_timeout=5000`. All store methods serialize through
one `threading.RLock` around a single shared connection
(`check_same_thread=False`); the lock is reentrant so internal helpers
can call each other without deadlock.

## 4. HTTP API

Router prefix `/board`. Auth layers:
- **Board auth** (`_require_board_auth`, most mutations): per-process
  random token via `X-Board-Token` header (issued only to loopback
  callers by `GET /board/session-token`), OR a valid device Bearer token.
- **Admin auth** (`_require_board_admin`, pause/resume only): board auth
  OR any loopback caller.
- **Read auth** (`require_device_or_loopback`; `state`/`stats`/`models`/
  `diff`): loopback OR device Bearer.
- Some GET routes (`transcript`, `audit`, `lessons`, `tokens-by-day`,
  `list` boards) have **no** auth dependency in the reference — reproduce
  that distinction deliberately.

### 4.1 Task CRUD

| Route | Body | Response |
|---|---|---|
| `POST /tasks` | `{title, project_slug, body?, created_by?, priority?, estimate?, acceptance_criteria?, files_of_interest?, depends_on?, tags?, board_id?}` | task dict. `title`+`project_slug` required; `project_slug` must match `[a-z0-9][a-z0-9._-]{0,63}`. Broadcasts `task_created`. |
| `POST /tasks/{slug}/move` | `{status, actor?}` | task dict; validated against ALLOWED_TRANSITIONS (400 if illegal). Broadcasts `task_moved`. |
| `POST /tasks/{slug}/assign` | `{assignee, actor?}` | task dict; validated against ASSIGNEES. |
| `POST /tasks/{slug}/depends` | `{depends_on:[slug,...], actor?}` | task dict; §2.4 rules. |
| `POST /tasks/{slug}/criteria` | `{acceptance_criteria:[...]}` | task dict; full replace. |
| `POST /tasks/{slug}/comment` | `{actor?, text}` | audit-entry dict; `text` required. |
| `DELETE /tasks/{slug}` or `POST /tasks/{slug}/delete` | — | `{deleted: slug}`. Hard-deletes the task + its audit/approval/lesson rows in one transaction; 404 if unknown. Deleting an in-progress task is safe — the worker's next `get_task()` returns `None`. |
| `GET /tasks/{slug}/audit` | — | full audit history array |
| `GET /tasks/{slug}/transcript` | — | last 200 compact turns from `<vault>/.crew_transcripts/<slug>.json` (4MB size guard) |
| `GET /tasks/{slug}/diff` | — | `{sha, diff}` via `git log --grep <slug>` then `git show --stat --patch` (diff capped 60,000 chars) |
| `POST /tasks/{slug}/steer` | `{message}` | `{ok, slug}` — one-shot nudge for the agent's next turn |
| `POST /tasks/{slug}/unstuck` | — | `{status:"unsticking", slug}` (async) — walks a parked task to `in_progress`/`claude-code` via legal FSM hops, runs a background diagnose-fix, posts the result as a comment, lands in `review` |

### 4.2 State / stats (read)

| Route | Query | Response |
|---|---|---|
| `GET /state` | `?board=` | `{tasks, projects, pending_approvals, paused, lane_models:{in_progress}}` |
| `GET /stats` | `?board=` | `by_status, by_assignee, tokens:{hive,claude}` (never summed), `avg_tokens_per_task, avg_attempts, smoke:{pass,fail}, cost_usd` (claude-only, $6/1M blended), `lessons, parse_fail:{turns,fails,rate}, paused, top_projects`(top 12), `bench_scores, loop_decisions, goal_cycles`. Cached 15s when `board` omitted. |
| `GET /tokens-by-day` | `?days=30` (1–365) | zero-filled ascending `[{date,hive,claude,total}]` |
| `GET /lessons` | `?limit=50` | `[{project, body, task}]` |
| `GET /models` | — | `{models:[...]}` installed Ollama model names |

### 4.3 Projects

| Route | Body | Response |
|---|---|---|
| `POST /projects/create` | `{name, path?}` | project dict; `mkdir`+`git init`, `enabled=true`. Path must resolve under the allowed project root or 400. |
| `POST /projects/{slug}/enable` \| `/disable` | — | project dict |
| `POST /projects/{slug}/push_allowed` | `{allowed}` | project dict |
| `POST /projects/{slug}/delete` | `{force?}` | `{deleted, slug, had_tasks}`; 409 if it still owns tasks and not forced |
| `POST /projects/{slug}/evolve/suggest` | — | `{slug, candidates:[...]}` ranked next-work ideas, persisted to `crew_meta` |
| `POST /projects/{slug}/evolve/go` | `{force?}` | decompose-shaped response + `evolved_from`; feeds top candidate through decompose; 409 if active tasks remain and not forced |

### 4.4 Plans (draft → approve)

| Route | Body | Response |
|---|---|---|
| `POST /plans/propose` | `{project_slug, goal}` | `{slug, steps, spec}` — drafts `{goal, assumptions[], open_questions[], steps:[{title,why,verify,criteria[]}]}`, creates one `kind='plan'` ticket holding it in `plan_spec`, left `proposed`. |
| `POST /plans/{slug}/approve` | — | `{approved, created:[slug,...]}` — one child task per step (`acceptance_criteria` = step's `criteria`, capped 5, `tags:["from-plan:<slug>"]`), then plan ticket → `archived`. |
| `POST /plans/{slug}/reject` | — | `{rejected}` — archives, no children. |
| `POST /plans/{slug}/request-changes` | `{feedback}` | `{slug, steps, spec}` — re-drafts from feedback; stays `proposed`. |

### 4.5 Content requests

`POST /content` — `{type: image\|video\|avatar, prompt, count(1-4), width, height, negative_prompt?, seed_media_id?, image_media_id?, voice?, avatar_name?, preprocess?, still?, project_slug?}` → creates `kind='content'` task, `content_spec` = `{...request, state:"queued", result_media_ids:[]}`, auto-assigns `content`, moves straight to `ready`. Response `{slug, type}`.

### 4.6 Boards / Approvals / Operational

- `GET /list` → all registered boards (≥1, `default`). `POST /boards`
  `{board_id, name, description?}` → board dict; 409 if id exists.
- `POST /approvals/{approval_id}/resolve` `{approved}` → `{ok, approved}`;
  sets `approved`/`denied` + `resolved_at`; 404 if unknown id.
- `POST /pause` / `POST /resume` (admin auth) — persists `crew_meta
  ["board.paused"]`; paused = dispatcher starts no new work, in-flight
  finishes.
- `POST /lane-model` `{status, model}` — sets `crew_meta
  ["lane_model:<status>"]` per-lane Ollama override.
- `POST /self-improve` — mines the board (parse-fail rate >15% over ≥50
  turns; tasks parked `review` with ≥5 attempts; >500k claude tokens
  burned twice on one project) → up to 8 `proposed` tickets for triage.
- `WS /events` — broadcasts `{event,...}` on every mutation:
  `task_created`, `task_moved`, `task_deleted`, `board_paused`,
  `board_resumed`, `content_requested`, `project_created`,
  `project_deleted`, `task_unstuck_done`.
- `GET /board` — HTML page; `?embed=1` drops the CSP `frame-ancestors`
  directive so a `file://`-origin host can frame it (loopback-only).

## 5. Goal-Decompose Behavior (`POST /board/decompose`)

Body `{goal, project_slug?}` — `project_slug`: existing slug, `""`
(always scaffold new), or `"auto"` (classify: reuse an existing project
or go greenfield).

1. **Resolve target.** `auto` → LLM-classifies against existing projects.
   Existing target → detect its real stack/test-runner, force the
   planner onto it. No target → detect intended stack from goal text
   (`_greenfield_stack`) so a new project gets ONE coherent stack, not a
   hallucinated mix.
2. **Draft the plan.** One LLM call (temp 0.3) against a fixed JSON
   schema: `{project_name, checklist:[str, 2-5 items], tickets:[{title,
   body, criteria[], files[], depends_on:[int]}, 4-9 items]}`. Each
   ticket: single-concern, 2-4 machine-testable criteria, `depends_on` as
   0-based indexes into the ticket array. Greenfield: a mixed-stack guard
   re-drafts once on wrong-stack files, then HARD REFUSES (422) rather
   than create an unverifiable chain.
3. **Scaffold (greenfield only).** `mkdir`+`git init`+minimal runnable
   skeleton (README + stack marker file, e.g. `pubspec.yaml` for
   Flutter) + initial commit. Missing marker file → fail loudly (500)
   rather than register an empty, wedged project. Registered
   `enabled=true, push_allowed=true`.
4. **Pre-flight baseline.** Runs the project's existing test suite once;
   stores already-failing test ids at `crew_meta
   ["preflight:failing:<project>"]` so verify only fails a ticket for
   NEW failures.
5. **Create the goal record.** `create_goal(text, project_slug,
   checklist_items, cycle=0)` → `GoalRecord` (`goal_id` = random 8-hex
   unless supplied) persisted as JSON at `crew_meta["goal:<goal_id>"]`:
   `{goal_id, text, project_slug, checklist:[{item,met:false}], cycle:0,
   status:"active", verify_spawned:false}`.
6. **Create tickets.** One `create_task` per planner ticket (max 12),
   `created_by="owner"` (→ `backlog`, §2.5), `review_by="claude-code"`,
   `tags:["nl-decompose","goal:<goal_id>"]`, `goal_id` column set. Second
   pass wires `depends_on`: LLM's explicit in-range index list if valid,
   else fallback to a strict linear chain (ticket *i* depends on *i-1*).
   Every ticket then assigned `hive` and moved → `ready`.
7. **Response:** `{project_slug, scaffolded, created, titles, goal_id, checklist}`.

**Completion loop** (`goal_loop.py`, driven by a dispatcher outside this
file's scope calling `maybe_spawn_verify` after each task completion):
- No-op unless goal `status="active"`, `verify_spawned==false`, and every
  non-verify subtask tagged with that `goal_id` is `done`/`archived`.
  Then: sets `verify_spawned=true` (idempotency guard), creates a
  `[goal-verify]` ticket (`tags:["goal-verify","goal:<id>"]`,
  `acceptance_criteria` = one per checklist item), assigns `hive`, walks
  `proposed → backlog → ready`.
- Verify run judges each checklist item met/unmet. All met → `status =
  "complete"`. Any unmet AND `cycle < GOAL_MAX_CYCLES` (hard constant 3,
  never LLM-overridable) → new `GoalRecord` (`cycle+1`, `goal_id =
  "<id>-c<cycle+1>"`) with a re-goal ticket for the unmet items only;
  `status` stays `"active"`. Any unmet AND `cycle >= GOAL_MAX_CYCLES` →
  `status = "needs_you"`, a `[needs-you]` escalation ticket is created,
  and NO further cycle is ever spawned for that chain.

## 6. Verifiable Checkpoints

1. **Illegal transition rejected.** Create a task with `created_by="owner"`
   (→ `backlog`). Move `backlog → done` directly: expect no status
   change and an error containing `"transition 'backlog' -> 'done' not
   allowed"`. Then walk `backlog → ready → in_progress → qa → review →
   done`: every hop succeeds and writes a `crew_audit` row.

2. **Slugs are sequential, never reused.** Create N tasks in a fresh DB;
   assert slugs are exactly `T-0001 … T-000N` in order. Delete `T-0002`
   mid-sequence; the next created task must NOT reuse `T-0002`.

3. **`created_by` drives initial status.** `created_by="owner"` →
   `status=="backlog"`. Any other `created_by` (e.g. `"hive"`) →
   `status=="proposed"`.

4. **Dependency validation.** A depends on nothing; `set_depends_on(B,[A])`
   succeeds. `set_depends_on(A,[B])` must raise ("cycle: B already
   depends on A"). `set_depends_on(A,["T-9999"])` must raise ("unknown
   blocker").

5. **Goal-decompose produces a consistent chained plan.** Call
   `/board/decompose` against an existing project. Assert: every created
   ticket carries tag `goal:<goal_id>` and column `goal_id`; `depends_on`
   values are real prior slugs (no leaked raw indexes); every ticket is
   `assignee="hive"`, `status="ready"`; `crew_meta["goal:<goal_id>"]`
   parses to `status="active", cycle=0, verify_spawned=false`, non-empty
   `checklist`. Mark all non-verify subtasks `done`; exactly one
   `[goal-verify]` ticket appears; calling the spawn check again creates
   no second one.

6. **Plan approve fan-out.** `POST /plans/propose` then `/plans/{slug}/approve`
   on a K-step plan: K new child tickets exist, each tagged
   `from-plan:<slug>`, `acceptance_criteria` matches that step's
   `criteria` (capped 5), and the plan ticket is now `status="archived"`.
