# Crew Board — design

A single-board kanban for dev tasks across all projects under
`<project-root>/`. Agents propose, claim, and execute tasks; the operator has
the final review. Built into the gateway; backed by the vault_writer
SQLite database; surfaced as a web UI (htmx), a markdown mirror in
the vault for Obsidian, and natural-language chat ("show me the
board", "add a task to refactor X").

## Locked decisions (2026-06-05 interview)

| Question | Choice |
|---|---|
| Storage | SQLite in vault_writer (`.vault-writer/vault.db`). New tables, isolated from notes. Mirror selected fields to `vault/tasks/<id>.md` for Obsidian view. |
| Scope | One board. `project` is a tag. Projects auto-detected from `<project-root>/`. |
| Columns | Proposed → Backlog → Ready → In Progress → Review → Done |
| Bot gating | Bot-created tasks land in **Proposed**. The operator promotes to Backlog. |
| Agent runtime | Hive (planner-qwen) first. If 2 attempts fail acceptance check → escalate to Claude Code subprocess. |
| Project onboarding | Auto-detect git repos under `<project-root>/`. The operator can toggle per-project agent access. |
| Assignment | Manual. The operator picks `assignee` from roster {`hive`, `claude-code`, `none`}. |
| Task fields | title, body, acceptance_criteria[], files_of_interest[], depends_on[], estimate, priority, tags[], project, assignee, created_by, status, audit_log[]. |
| External services | Block, prompt the operator via web UI for approve/deny. Cost = always $0 default. |
| Web UI | Gateway, new route group `/board/*`. htmx + Tailwind in single page. Same device-auth as chat WS. |
| Notifications | Web badge + sound on new pending. ntfy.sh push. Chat-WS `board_event` frame. Flutter app receives via same WS. |
| Git remote | Each project's configured origin. Per-project allowlist for whether agent may push. |
| Acceptance check | Owner manual ✓ in Review **and** auto-run project test cmd **and** hive verification pass **and** diff visible. All four. |
| Default agent on new task | Unassigned. Owner picks per-task. |
| MVP cut | Full vertical slice on day one. |

## Out of scope (initial cut)

- Recurring tasks, time-tracking, sprint cycles, burndown charts.
- Multi-tenant / multi-user (single operator).
- Discord integration (Discord bot decommissioned).
- Mobile-native UI (Flutter app uses the same WS / web view).

## Architecture

```
                            ┌──────────────────────────┐
            web UI (htmx) ──┤  /board  (FastAPI route) │
            chat WS  ───────┤  bot/owner intent        │
            Flutter app ────┤                          │
                            └────────┬─────────────────┘
                                     │
                            ┌────────▼─────────────────┐
                            │  CrewBoardStore           │  ← SQLite tables in
                            │  - tasks                  │    vault_writer DB
                            │  - audit_log              │
                            │  - projects               │
                            │  - approvals (external)   │
                            └────────┬─────────────────┘
                                     │
                  ┌──────────────────┼────────────────────┐
                  │                  │                    │
         ┌────────▼────────┐  ┌──────▼───────────┐  ┌─────▼────────┐
         │ project_scanner │  │ task_dispatcher   │  │ markdown_     │
         │ (5-min poll)    │  │ (Ready→InProgress)│  │ mirror writer │
         │ auto-detect repo│  │ picks assignee fn │  │ on every diff │
         └─────────────────┘  └──────┬───────────┘  └──────────────┘
                                     │
                          ┌──────────┴──────────┐
                          │                     │
                  ┌───────▼──────┐    ┌─────────▼────────┐
                  │ hive_runner  │    │ claude_code_     │
                  │ (planner-qwen) │    │ runner (subprocess)│
                  │ 2 attempts   │    │ on escalation    │
                  └──────────────┘    └──────────────────┘
                          │                     │
                          └──────────┬──────────┘
                                     │
                          ┌──────────▼──────────┐
                          │ verifier            │
                          │ - run test cmd      │
                          │ - hive verify pass  │
                          │ - move to Review    │
                          └─────────────────────┘
```

## Schema (SQLite, in `vault.db`)

```sql
CREATE TABLE IF NOT EXISTS crew_projects (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    slug          TEXT NOT NULL UNIQUE,          -- e.g. "example-project"
    path          TEXT NOT NULL,                  -- e.g. "<project-root>/example-project"
    name          TEXT NOT NULL,
    enabled       INTEGER NOT NULL DEFAULT 0,    -- owner toggle
    push_allowed  INTEGER NOT NULL DEFAULT 0,    -- agent may git push
    test_cmd      TEXT,                          -- e.g. "npm test"
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS crew_tasks (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    slug                TEXT NOT NULL UNIQUE,    -- e.g. "T-0042"
    title               TEXT NOT NULL,
    body                TEXT NOT NULL DEFAULT '',
    status              TEXT NOT NULL,           -- proposed|backlog|ready|in_progress|review|done|archived
    project_slug        TEXT NOT NULL,
    assignee            TEXT NOT NULL DEFAULT 'none',  -- hive|claude-code|none
    created_by          TEXT NOT NULL,           -- owner|hive|claude-code
    priority            TEXT NOT NULL DEFAULT 'medium', -- low|medium|high
    estimate            TEXT,                    -- t-shirt: xs|s|m|l|xl
    acceptance_criteria TEXT NOT NULL DEFAULT '[]',  -- JSON: [{text, checked}]
    files_of_interest   TEXT NOT NULL DEFAULT '[]',  -- JSON: [glob, ...]
    depends_on          TEXT NOT NULL DEFAULT '[]',  -- JSON: [task_slug, ...]
    tags                TEXT NOT NULL DEFAULT '[]',  -- JSON
    attempt_count       INTEGER NOT NULL DEFAULT 0,
    last_branch         TEXT,                    -- agent's working branch
    last_pr_url         TEXT,
    verify_results      TEXT NOT NULL DEFAULT '{}',  -- JSON: {tests, hive_verdict, diff_path}
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    FOREIGN KEY (project_slug) REFERENCES crew_projects(slug)
);

CREATE INDEX IF NOT EXISTS idx_crew_tasks_status ON crew_tasks(status);
CREATE INDEX IF NOT EXISTS idx_crew_tasks_project ON crew_tasks(project_slug);
CREATE INDEX IF NOT EXISTS idx_crew_tasks_assignee ON crew_tasks(assignee);

CREATE TABLE IF NOT EXISTS crew_audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_slug   TEXT NOT NULL,
    actor       TEXT NOT NULL,                   -- owner|hive|claude-code|system
    action      TEXT NOT NULL,                   -- create|move|comment|verify_pass|verify_fail|...
    detail      TEXT NOT NULL DEFAULT '',        -- free text
    metadata    TEXT NOT NULL DEFAULT '{}',      -- JSON
    created_at  TEXT NOT NULL,
    FOREIGN KEY (task_slug) REFERENCES crew_tasks(slug)
);
CREATE INDEX IF NOT EXISTS idx_crew_audit_task ON crew_audit(task_slug);

CREATE TABLE IF NOT EXISTS crew_approvals (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    task_slug    TEXT NOT NULL,
    requested_by TEXT NOT NULL,                  -- hive|claude-code
    kind         TEXT NOT NULL,                  -- external_service|cost|push|destructive
    summary      TEXT NOT NULL,
    payload      TEXT NOT NULL DEFAULT '{}',     -- JSON: what would happen
    status       TEXT NOT NULL DEFAULT 'pending',-- pending|approved|denied|expired
    created_at   TEXT NOT NULL,
    resolved_at  TEXT,
    FOREIGN KEY (task_slug) REFERENCES crew_tasks(slug)
);
```

## State machine

```
proposed ──(owner promote)──> backlog ──(owner set ready)──> ready
                                                                │
                              ┌──── owner manual claim ─────────┘
                              ▼
                         in_progress ──(agent finish)──> review ──(owner ✓)──> done
                              ▲                            │
                              └────(verify fail × 2)───────┘
                              │
                              └────(escalate to claude)─── claude_code_runner
```

## Verification flow (Review gate)

Four signals, ALL must pass for Done:

1. **Owner manual checklist tick** — each `acceptance_criteria[*]` is a checkbox.
2. **Auto-run test cmd** — `crew_projects.test_cmd` runs in project dir; output stored. Hard fail blocks Review entry.
3. **Hive verification pass** — synth-tier helper reviews the diff against acceptance criteria, returns `verdict: pass|fail` + reasoning. Attached for the operator's context.
4. **Diff + checklist side-by-side** — UI presents changed files (git diff vs main) next to checkboxes for the operator to step through.

## Escalation logic

```python
if task.attempt_count >= 2 and task.assignee == "hive":
    audit.add(task, "system", "escalate", detail="hive failed twice")
    task.assignee = "claude-code"
    task.attempt_count = 0
    notify_owner("Task X escalated to Claude Code after 2 hive failures")
```

## Notifications

- **Web badge + sound**: SSE stream `/board/events`. Client polls minimum once per minute as a fallback.
- **ntfy.sh push**: reuses existing `ntfy_push` action. Sent on (a) proposed-by-bot needs operator promote, (b) review ready, (c) external approval needed, (d) escalation.
- **Chat WS frame**: `{"type": "board_event", "event": "review_ready", "task": "T-0042"}` so an open chat tab can show a banner.
- **Flutter app**: same WS as chat.

## File layout

```
gateway/
  crew_board/
    __init__.py
    store.py          # CrewBoardStore (SQLite wrapper)
    schema.py         # CREATE TABLE strings; migration
    project_scanner.py # auto-detects <project-root>/*
    dispatcher.py     # picks tasks, calls runners
    hive_runner.py    # runs a task with planner-qwen
    claude_runner.py  # spawns claude-code subprocess
    verifier.py       # test cmd + hive verify + diff
    markdown_mirror.py# writes vault/tasks/<slug>.md
    notifications.py  # ntfy + WS broadcast
    intents.py        # natural-language → CrewBoardCommand
  routes/
    board.py          # FastAPI /board/* (HTML + JSON + SSE)
gateway/tests/
  test_crew_board_store.py
  test_crew_board_dispatcher.py
  test_crew_board_verifier.py
  test_crew_board_routes.py
docs/
  crew-board-design.md (this file)
vault_writer/
  schema.py            # add crew_* tables to existing CREATE
```

## MVP cut (vertical slice, single-task end-to-end)

1. **Schema** — add `crew_*` tables to `vault_writer` schema; migration runs at startup.
2. **Store** — minimal CRUD + state moves.
3. **Project scanner** — one-shot scan on startup; populates `crew_projects` with `enabled=0`. Owner toggles.
4. **Route /board** — HTML page (six columns, drag-drop via htmx + sortable-js), plus JSON endpoints `GET /board/tasks`, `POST /board/tasks`, `POST /board/tasks/<id>/move`.
5. **Hive runner** — given a task, builds a planner prompt from task + project context, runs hive coordinator, captures stdout/diff to `verify_results`.
6. **Verifier** — runs `test_cmd` via subprocess; hive-verify via a synth call; stores results.
7. **Operator Review UI** — shows checklist + diff + verifier output; operator ticks ✓; clicks Done.
8. **Chat-WS intent shim** — adds derives to `hive_coordinator`: "add a task to X", "show me the board", "claim task T-0042".
9. **Markdown mirror** — daemon watches DB, writes `vault/tasks/<slug>.md` per change.
10. **Notifications** — ntfy + WS event on (proposed-by-bot, review-ready, external-approval-pending, escalation).
11. **Escalation** — when `attempt_count == 2`, move to claude-code; claude runner spawns `claude-code --task=<id>` subprocess with task body + acceptance criteria.

## Implementation order

Each step is its own commit so we can roll back cleanly.

| # | Commit | Status |
|---|---|---|
| 1 | Schema + Store CRUD + tests | tbd |
| 2 | Project scanner + tests | tbd |
| 3 | /board routes (JSON only) + tests | tbd |
| 4 | /board HTML page (htmx) | tbd |
| 5 | Hive runner + dispatcher polling loop | tbd |
| 6 | Verifier (test cmd + hive verify) | tbd |
| 7 | Markdown mirror daemon | tbd |
| 8 | Notifications (ntfy + WS broadcast) | tbd |
| 9 | Chat-WS intent derives | tbd |
| 10 | Claude Code runner + escalation | tbd |
| 11 | Approvals (external service gate) | tbd |
| 12 | End-to-end vertical-slice eval script | tbd |

## Security boundaries

- **Audience**: every task carries `audience: ["owner"]` implicit. Claude-code device (paired with audience=["all"]) sees tasks; non-operator bots don't.
- **Path traversal**: `crew_projects.path` validated against the configured project root prefix on insert and every read. Agents cannot escape this root.
- **Git push gate**: even on `push_allowed=1` projects, the runner refuses pushes to `main` / `master`. Always pushes to a feature branch.
- **External services**: any HTTP outbound from a runner is intercepted by an HTTP egress policy. Cost > 0 OR external-FQDN match → blocked, approval row inserted, runner pauses until approved.
- **Destructive ops** (drop tables, force-push, mass-delete): pause + approval. Always.
- **Operator identity**: device with `audience` containing `owner`. Settings: existing device auth model (one trusted device = operator). Multi-operator not in MVP.

## Open questions to resolve during MVP build

- **Test-cmd default per project**: auto-detect from manifest? Yes — `package.json` → `npm test`; `pyproject.toml` → `pytest`; etc. Editable per project.
- **Hive verify prompt format**: keep terse; pass diff + acceptance criteria, ask `{verdict, reasoning}`. Iterate.
- **Markdown mirror conflict**: if the operator edits the mirror file in Obsidian, who wins? DB is canonical; mirror is overwritten. Note in mirror frontmatter: `READ ONLY; edit via /board`.
- **Task slug format**: `T-NNNN` zero-padded, monotonic. Reset never.
- **Audit log retention**: keep forever until DB grows past N MB; then archive oldest to a side table.

## Naming

- Feature: **Crew Board**.
- Endpoint root: `/board`.
- DB table prefix: `crew_*`.
- Module: `gateway.crew_board.*`.
- Markdown mirror path: `vault/tasks/<slug>.md`.
- Notification topic: `crew-board`.

---

## Agentic-loop upgrades (2026-06, P1–P8)

After the board shipped real software (***REMOVED***: 34 tasks, 222
tests), deep research into mature OSS agentic-dev tools (Aider,
OpenHands/CodeAct, SWE-agent ACI, AutoCodeRover, Cline, Goose) plus a
parallel side project drove eight reliability/strategy upgrades to
`hive_agent_loop.py` and the dispatcher. Default model unchanged
(`qwen3.6:27b-Q4`, bench-proven; needs both 5060 Ti GPUs).

### Loop tools (one JSON tool call per turn)

| Tool | Purpose |
|---|---|
| `list_dir` | List a directory (skips `.git`). |
| `read_file` | Read a file (truncates huge files). |
| `write_file` | Create / full-rewrite a file (parents auto-create). |
| `replace_in_file` | **(P2)** Exact-substring edit — `search`/`replace`, must match exactly once. Cheaper than re-sending the file, less drift. Prefer for edits. |
| `find_symbol` | **(P7)** Resolve a class/def name → `file:line` + signature. |
| `run_cmd` | Whitelisted shell (python/pytest/git/…). |
| `done` | Finish with a summary. |

### Gates & guards

- **P1 constrained JSON decoding** — every `OllamaInvoker.chat` passes a
  `_TOOL_CALL_SCHEMA` via Ollama's `format` field, so a quantized model
  cannot emit an unparseable tool call. `_extract_json` stays as a 400-
  fallback. Parse-fail turns drop to ~0 (tracked on `/board/stats`).
- **P3 lint-on-write auto-revert** — after any `*.py` write/replace, the
  file is `py_compile`d; on `SyntaxError` the edit is reverted (prior
  bytes restored, or the new file deleted) and the compile error is
  returned. Broken Python never lands or wastes a pytest run.
- **P4 self-critique on first green** — on the FIRST green pytest the
  loop injects one reflection observation (re-read acceptance criteria,
  verify intent not just green tests) instead of fast-tracking; the 2nd
  green auto-dones. Catches "tests pass but intent unmet".
- Pre-existing: force-pytest after N writes, repeat-action loop guard,
  stuck-rewrite hard-abort, heartbeat-per-turn (reaper), separate hive
  token accounting.

### Memory — lesson notes (P5)

`crew_lessons` table (`store.add_lesson` / `recent_lessons` /
`count_lessons`). After a successful **claude escalation**, the
dispatcher calls `claude_runner.distill_lesson` to capture a one-
paragraph generalizable lesson; the hive loop seeds each task brief with
the project's 3 most recent lessons so the next attempt avoids the same
mistake. Count shown on `/board/stats`.

### Repo-map / symbol index (P7)

`repomap.py` walks the project's Python with stdlib `ast` into a
`path → [class/def signatures]` index, rendered token-budgeted (~1.5k)
into the loop preamble (cached; rebuilt only after a successful
write/replace). Replaces blind `list_dir`/`read_file` exploration for a
small local model. `find_symbol` is the on-demand lookup.

### Worktree-per-task parallel lanes (P6, opt-in)

`worktree.py` (`ensure_worktree`/`remove_worktree`) runs each task of a
`parallel=True` project (`crew_projects.parallel` column) in its own git
worktree under `<repo>/.crew-worktrees/<slug>` on branch `crew/<slug>`.
The dispatcher then locks **per-slug** instead of per-assignee and gates
concurrency to `PARALLEL_LANE_CAP`. `run_hive_agent_loop` / `run_claude`
/ `verify` take a `project` override so the worktree checkout is used end
to end; git commit/rollback is already per-tree. Worktree removed on task
exit, branch kept for merge.

- **Default `PARALLEL_LANE_CAP = 1`**: the bench model needs both GPUs,
  so loading it twice would thrash. Even at 1 lane, parallel projects
  gain branch-per-task isolation (clean main checkout, per-tree
  rollback — structurally prevents the cross-task poisoning that broke
  14 tests in the SC build). Raise the cap **only** after wiring a one-
  card model (Q3_K_M / IQ4_XS) so two lanes can each hold a model.

### Board stats surfacing (P8)

`/board/stats` adds: lessons-learned count, parse-fail rate (newest-50
transcript scan; should sit ~0 after P1), and avg-tokens-per-task (hive
and claude **separate, never combined**), alongside the existing status/
token/smoke panels.

### Tests

`test_crew_board_hive_loop.py` (replace_in_file, lint-revert, self-
critique), `test_crew_board_store.py` (lessons, parallel),
`test_crew_board_worktree.py` (worktree add/remove/isolation),
`test_crew_board_repomap.py` (signatures, budget, find_symbol). One
commit per phase; full crew suite green throughout.
