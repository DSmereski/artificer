# SPEC: Autonomous Build Loop, Verifier, and Dispatcher

Reimplementation-grade spec for a self-driving coding pipeline: a small
local LLM ("the hive") works crew-board tickets turn-by-turn inside a
sandboxed tool loop, a rule-based verifier gates whether the work is real,
and a polling dispatcher owns lane transitions, retries, escalation to a
stronger paid/CLI coding agent, and git safety.

Actors: the **agent loop** is a multi-turn tool-calling driver for the
cheap/local model; the **verifier** is a stateless, rule-based pass/fail
judge run after every attempt; the **dispatcher** is the only thing that
mutates ticket state (polls, claims, runs, verifies, commits/rolls back,
escalates, reaps); the **escalation runner** spawns a stronger external
coding CLI as a subprocess when the local model gets stuck.

---

## 1. Agent Loop

One task = one conversation, re-rendered from scratch every turn (no
persistent server-side state). Exactly one tool call executes per turn.

### 1.1 Tool contracts

| Tool | Args | Behavior | Returns |
|---|---|---|---|
| `list_dir` | `path` | Lists immediate children under the sandboxed root; VCS/cache/transcript entries hidden; capped ~200. | `{ok, entries:[name/"name/"]}` |
| `read_file` | `path` | UTF-8 read, errors replaced; capped ~50 KB, longer returns a truncated prefix + `total_bytes`. | `{ok, content, truncated, total_bytes}` |
| `write_file` | `path`, `content` | Full create/overwrite, parent dirs auto-create, capped ~200 KB. Prior content captured first; a Python result that fails to compile is **reverted** (restored, or deleted if new) — broken code never lands. | `{ok:true, bytes_written}` |
| `replace_in_file` | `path`, `search`, `replace`, `count`(=1) | Surgical edit: `search` must match **exactly** `count` times byte-for-byte or the edit is refused with the real match count. Cheaper than a rewrite, less drift. Same compile-check-and-revert as `write_file`. | `{ok:true, replacements, bytes_written}` |
| `find_symbol` | `name` | Looks up a class/def by name via a lightweight repo-map index. | `{ok, matches:[file:line]}` |
| `run_cmd` | `cmd` | One sandboxed shell command (§1.3), synchronous; `mkdir -p` rewritten to a native dir-create. Output capped (~2000/1000 chars stdout/stderr). Test-runner calls get extra treatment: on failure, common signatures (import/name/syntax errors, zero-collected, fixture misuse) pattern-match into up to 3 `hints`; on all-green, a `done_nudge` tells the model to stop editing and call `done`. | `{ok, exit_code, stdout_tail, stderr_tail, [hints], [done_nudge]}` |
| `done` | `summary` | Ends the loop successfully. Summary is enriched: files changed (tracked diff + untracked new, capped), current commit SHA + subject, latest pass/fail counts mined from the transcript. | Loop returns `ok=true` |

Every call also carries an optional `note` — one sentence of reasoning,
streamed live for observability, advisory only.

### 1.2 Turn structure

Assembled fresh each turn from: (1) a cached **workspace tree** (~60-entry
cap, rebuilt only after a mutating action), (2) a cached, token-budgeted
**repo map** of class/def signatures (same invalidation), (3) the **task
brief** — sent once, referenced every turn: title, body, acceptance
criteria, an ordered file-creation checklist, the test command, plus
conditionally a trimmed tail of the *previous* failing run (on a retry),
a few keyword-matched cross-task "lessons" from past escalations (fenced as
untrusted reference data, never instructions), and up to 2 matched skill
playbooks, (4) the **last 16 formatted tool results**, and (5) a one-shot
**owner steer** message if a human queued live guidance, consumed once.

The system prompt is static: one JSON tool call per turn, no prose/fences/
chain-of-thought leakage, prefer `replace_in_file` over `write_file` for
edits, prefer `write_file` over `run_cmd mkdir`, never use absolute paths
or `..`, exact command whitelist stated.

The model call uses low temperature (~0.2), a generous output budget (a
tool call can embed a multi-KB file body — undersizing truncates the
JSON), and a bounded context window. It is issued two ways at once: a
structured-output JSON-schema constraint **and** native function-calling
tool defs with identical names/args (tool-calling models otherwise emit
incompatible syntax) — either path normalizes back to `{"tool","args"}`.

### 1.3 Path and command sandboxing

- **Path sandbox** — every path arg resolves against the project root and
  is rejected if absolute (leading separator or drive letter) or if it
  resolves outside the root (strict containment check catches `..` too).
- **Command whitelist** — `run_cmd` only executes a fixed allowlist
  (interpreter, test runner, VCS, basic file ops, plus project toolchain).
  Every `&&`/`;`-segment is validated independently, not just the first.
  Pipes, redirects, backticks, `$()`/`${}`, embedded newlines, and
  bare/background `&` are refused outright before further parsing; `&&`
  chaining between whitelisted commands is allowed (needed for
  `git add -A && git commit ...`).
- **Interpreter + argument sandbox** — the interpreter may only invoke the
  test-runner module or a syntax-check module (arbitrary `-c "code"` or an
  arbitrary script is refused — either lets the model read anywhere on
  disk via the language's own file APIs), and every non-flag argument to
  any whitelisted command independently passes the same path-containment
  check, so escapes via command arguments (not just tool-call `path`
  fields) are refused too.
- Defense-in-depth, not OS isolation: a malicious in-sandbox test file
  still executes as the whitelisted test runner itself; true containment
  needs an OS-level sandbox layered on top.

### 1.4 Parsing, error recovery, and heartbeat

Raw output goes through a tolerant JSON extractor (strips fences and any
leading reasoning block, locates the first JSON object). On failure — no
object, or no string `tool` field — an observation naming the failure
(with the literal output tail) is appended and the loop continues,
incrementing consecutive- and total-parse-failure counters; a valid call
resets the consecutive counter. Exceeding either threshold aborts early
(§1.5) instead of grinding to the iteration cap.

After every model call (parse-fail or not), input+output tokens for that
turn are summed into a running per-task total and persisted with a
**heartbeat timestamp** in one write. This heartbeat is the dispatcher's
only signal a long run (up to ~200 turns) is alive — a task that stops
heartbeating past the stale-in-progress window (§3.4) is reclaimed,
independent of any single call's own timeout.

### 1.5 Loop-termination guards

| Guard | Trigger | Effect |
|---|---|---|
| Parse-fail storm | N consecutive OR M total unparseable turns | Abort `ok=false` |
| No-progress | K consecutive turns with no write/replace/run_cmd | Abort `ok=false`, "spinning on reads" |
| Repeat-action nudge | Same (tool, target) 3 turns running | Inject corrective observation (not an abort) |
| Stuck-rewrite | Same file written 8 times running | Abort `ok=false` |
| Force-verify gate | 5 consecutive writes with no intervening test run | Next write **refused**, must run tests first |
| Consecutive-green auto-done | Full suite green N times running (default N=2, per-task configurable) | 1st green + acceptance criteria present → one-shot self-critique nudge (verify criteria genuinely met, not just green) before `done` allowed. Nth green → loop force-finishes with a synthesized summary |
| Iteration cap | `max_iters` reached without `done` | Abort `ok=false` |

---

## 2. Verifier ("honest done")

Runs once after every attempt, never raises, writes a structured result
back onto the task. Six independent gates feed one boolean `ok`; a separate
`outcome_proven` flag is tracked but does **not** affect `ok`.

1. **Tests** — run the project's test command with an augmented PATH and
   the runner binary resolved through OS executable-extension rules (so a
   shim script resolves). Couldn't spawn at all (missing binary/project
   path) → **fail** (environment fault, never silently passed). No test
   command configured, or explicitly skipped → **pass**. Exit 0 → **pass**.
   Exit non-zero → **baseline-diff pass**: parse failing-test IDs
   (runner-specific patterns) and compare against a pre-flight baseline
   captured before the task chain started; pass iff every currently-failing
   test was *already* failing in the baseline (one pre-existing flaky test
   can't freeze an otherwise-good chain). Requires both a captured baseline
   and parsed failures, else fail strict.
2. **Files-of-interest** — every declared glob must match ≥1 path under the
   project; any zero-match glob fails this gate.
3. **Acceptance criteria** — reported (checked/unchecked counts) but
   **informational only**, never flips `ok`; ticked by a human reviewer,
   not inferred from the diff (too noisy; gating on human-only checkboxes
   would stall every automated run).
4. **Smoke test** — an optional task-level command run after the test
   suite; non-zero exit fails the gate, unconfigured is permissive (catches
   "unit tests green, integration broken" bugs mocked tests miss).
5. **Commit gate (false-done backstop)** — a git-backed project must show
   either a dirty working tree or an existing commit referencing the task
   id; clean tree + no matching commit → fail, "no work was produced."
   Non-git projects are permissive.
6. **Entry-point gate (boot gate)** — for project types with a known
   app-entry convention, the entry file must exist and contain a
   recognizable `main` signature (catches "tests pass, app can't launch,"
   where tests supply their own mocked main). Other project types: not
   checked.

**`ok` = tests ∧ files ∧ smoke ∧ commit ∧ entry-point** (criteria never
gates `ok`). `reason` concatenates every failing gate's explanation.

**`outcome_proven`** is `true` only when a smoke command was configured,
ran, and exited 0 — a real probe exercised behavior, not just mocks. It
does not gate promotion out of build; it gates the dispatcher's decision to
auto-approve a *timed-out human review* (§3.5), so a never-exercised
feature can't reach done just because a reviewer process hung.

---

## 3. Dispatcher

Owns every ticket-state transition; the only place the escalation policy
lives; guarantees a clean git restore point after every failed attempt.

### 3.1 Polling model

Single async loop, fixed interval (default ~5s), until stopped. Each tick,
in order: reap crash-orphans (§3.4, runs even while paused) → sweep
done→archive for tasks past a retention window (throttled to ~once/30 min)
→ pause gate (stop here if paused; log only on transition) → claim ready
tasks (§3.2) → anti-wedge detection (if every ready task is blocked on an
unmet dependency and nothing is in-progress for >~180s, log/notify once,
self-clearing) → review-lane sweep (dispatch waiting reviews, auto-resolve
expired) → QA-lane sweep (same).

### 3.2 Claiming and serialization

A ready task claims only if its assignee is a real worker, every
`depends_on` entry is already done (self-dependency ignored, not
deadlocked), and it isn't already in-flight.

- **Single-flight (default) projects**: at most **one** task per assignee
  runs at once — a per-assignee lock *plus* a per-tick claimed-set (needed
  because the lock is only held inside the async run; without the set two
  same-assignee tasks could both flip to in-progress in one tick).
- **Parallel-opt-in projects**: each task runs in its own git worktree on a
  per-task branch (no checkout collision), up to a configurable **lane
  cap** (default 1) of concurrent tasks per assignee, tracked by a live
  counter incremented at claim / decremented at finish.

**Hard attempt cap** (single chokepoint, checked at *every* claim
regardless of what re-queued the task): if `attempt_count` already reached
the cap (default **5**), the task is **not** re-run — parked directly in a
backlog/triage lane with a comment.

On claim: ready → in-progress, attempt counter increments, current git HEAD
(project or per-task worktree) captured as the rollback point *before* any
runner touches the tree.

### 3.3 Running and resolving an attempt

The assignee determines the runner (§1 loop for local-model lanes, §4
escalation runner for the paid/CLI lane). After the runner returns, the
verifier (§2) always runs regardless of the runner's own claimed success.

**Runner ok ∧ verifier ok** → commit everything (message references the
task id + "verified"); push to the remote only if the project opts in
(failed push never blocks the pipeline); for worktree tasks, merge the
task branch back into the shared base (conflict → warning, branch/worktree
kept for manual merge). Task moves in-progress → **QA**.

**Runner failed ∨ verifier rejected** → hard-reset the working tree to the
pre-attempt commit (discard all changes, including untracked) so a broken
attempt can't poison the next task's full-suite run; a missing rollback
point is logged as an explicit warning, not silently ignored. Then:
- Test **environment** itself couldn't run (spawn failure / missing
  project path) → park in review immediately, **no** attempt/ladder cost
  (retrying an unspawnable command only burns attempts, and money on the
  paid lane).
- Attempt cap now reached → park in review, labeled a cap failure.
- Next ladder rung exists **and** ≥ escalation threshold (default **2**)
  attempts burned on this rung → promote (§3.6), requeue to ready.
  Promoting to the paid lane first checks a rolling-24h spend cap;
  exceeding it parks in review instead.
- Else → simple retry: requeue to ready, same assignee.

### 3.4 Crash-orphan reaping

Any in-progress task **not** in this process's live-running set (and not
mid-claim) is checked against its heartbeat (falling back to last-updated).
Older than the stale threshold (default **10 min** — beyond the slowest
plausible turn, short of a genuinely wedged run) → bounced to ready with a
comment; if its assignee was the primary local lane and it had already
burned the escalation threshold's attempts, pre-escalate to the paid lane
right there. A live run heartbeats every turn (§1.4), so a healthy run —
however long — is never reaped.

### 3.5 Review and QA timeouts

Both lanes use the task's last-updated timestamp against their own timeout
(both default **15 min**). **QA timeout** → promote straight to review
(build already verified, just un-QA'd). **Review timeout** → auto-approve
to done **only if** `outcome_proven` is exactly boolean `true` (strict
identity check, not truthy — a corrupted record can't slip through); if
not proven, stay in review and notify a human that manual review is
needed, since no probe ever confirmed behavior.

### 3.6 Escalation ladder

An ordered assignee list. Default two rungs: primary local-model lane,
then paid/CLI lane. A three-rung variant inserts a lighter local-model
lane in between when enabled by config. "Next rung" is a pure index
lookup; the top rung has no next hop and falls through to attempt-cap
parking.

---

## 4. Escalation Runner

Spawns an external, stronger coding-CLI agent as a subprocess when the
local loop can't finish a ticket. Four modes share one pattern (spawn →
capture stdout/stderr → enforce timeout → parse verdict):

| Mode | Purpose | Permission | Timeout | Output contract |
|---|---|---|---|---|
| **Build/fix** | Finish the ticket like a normal escalated attempt | Elevated (no per-tool prompt; scoped to project dir) | ~15 min | Free-form; token usage parsed from a structured result envelope |
| **Unstick** | Diagnose a *stalled* ticket specifically | Elevated | ~8 min (kept under §3.4's reap window so the run itself is never reaped mid-flight) | Free-form summary: root cause + fix-or-explain |
| **QA** | Write tests covering acceptance criteria, run them | Elevated (creates/edits test files) | ~10 min | Final message exactly one JSON object: `{"passed":bool,"reason":str,"tests_added":[paths]}` |
| **Review** | Read-only judgment of a completed attempt | Default/restricted (no edits) | ~4 min | Final message exactly one JSON object: `{"approved":bool,"reason":str}` |

Shared rules: the subprocess's only writable scope is the project
directory, and it's told explicitly never to push (only the dispatcher
pushes, after a fresh verify pass). Its environment is a filtered copy of
the host env — any var name matching a secret-shaped pattern
(tokens/passwords/API keys/credentials/DB URLs/etc.) is stripped **except**
the CLI's own auth vars, since elevated permission means no per-tool
approval gate and a prompt-injected task body must not be able to
exfiltrate a secret it can't see. JSON verdicts are extracted tolerantly
(fenced block preferred, else the last well-balanced `{...}` via
brace-depth scan); a call that can't be parsed into a verdict is treated as
failure, not retried in-process — the dispatcher's normal fail path
applies.

**Escalation trigger** (owned by the dispatcher, §3.3): a ticket escalates
once its current-rung attempt count reaches the threshold (default 2) *and*
that attempt failed verification — success never escalates.

**Lesson distillation**: after a successful paid-lane rescue, a
best-effort side call asks for one short, concrete, generalizable lesson
and persists it against the project, surfaced back into future
local-loop task briefs, fenced as untrusted reference data, never
instructions. Fully non-blocking; any failure is silently absorbed.

---

## 5. Key Constants and Thresholds

| Constant | Default | Effect |
|---|---|---|
| Iteration cap | 40 (raised, e.g. ~200, for dispatcher-driven runs) | Hard stop if `done` never fires |
| Consecutive-green-to-auto-done | 2 (per-task configurable) | All-green runs before force-finish |
| Parse-fail abort (consecutive / total) | 12 / 30 | Aborts a wedged/garbage-output model early |
| No-progress abort | 25 turns with no write/run | Aborts a "reading in circles" run |
| Stuck-rewrite abort | 8 identical writes running | Aborts a model looping on one file |
| Force-verify gate | 5 writes without an intervening test run | Refuses further writes |
| Max file / read size | ~200 KB write / ~50 KB read | Bounds sandbox blast radius |
| Command / test / smoke timeout | 120s loop `run_cmd` / 180s verifier tests / 120s verifier smoke | Per invocation |
| Escalation threshold | 2 attempts on current rung | Triggers promotion |
| Max total attempts | 5 | Hard park regardless of rung |
| Stale in-progress | 10 min | Heartbeat-less task reclaimed |
| Review / QA timeout | 15 min each | Review: conditional auto-approve or human notify. QA: promote to review |
| Anti-wedge alert | 3 min of zero eligible/running work | Surfaces a stuck dependency chain |
| Daily paid-lane spend cap | configurable, off = unlimited | Blocks further paid escalation once exceeded (rolling 24h) |
| Parallel lane cap | 1 (configurable) | Max concurrent worktree tasks per assignee |
| Escalation-runner timeouts | build ~15m / unstick ~8m / QA ~10m / review ~4m | Per subprocess mode |

---

## 6. Verifiable Checkpoints

1. **Sandbox holds.** `run_cmd` with a path escape (`cat ../../secret`), a
   non-whitelisted binary, and a piped/chained injection (`cmd | sh`) each
   return `{"ok":false}` with a specific reason and spawn no process.
   *Check*: unit-test the path/command validators directly.
2. **Loop converges, doesn't run away.** Against a fixture project with one
   trivially-fixable failing test, the loop reaches `done` within a small
   bounded turn count after two consecutive green runs, never exceeding
   the iteration cap. *Check*: run the loop, assert `turns` stays low and
   `ok=true`.
3. **False-done is rejected.** Against a fixture repo with a clean git tree
   and no task-referencing commit, the verifier returns `ok=false` naming
   unproduced work, even with tests/files gates green. *Check*: call the
   verifier directly against that fixture state.
4. **Escalation fires on repeated failure only.** Two consecutive verifier
   rejections on the primary rung reassign to the next ladder rung and
   requeue to ready; one failure must not escalate, success must never.
   *Check*: drive two failed attempts through the dispatcher against a
   stub runner/verifier; inspect assignee/status/attempt count.
5. **Crash-orphans reclaimed, live runs untouched.** A stale-heartbeat
   in-progress task outside the live-running set is requeued to ready next
   tick; a fresh-heartbeat (or live-set) task is left untouched in the
   same tick. *Check*: seed both states in the store, run one dispatcher
   tick, assert both outcomes.
