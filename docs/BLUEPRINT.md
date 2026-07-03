# Blueprint — build Artificer from scratch

This is the construction plan: how to build the whole system from nothing, in a
sane order, so a person (or a coding agent) can recreate it. The
[installer](../installer/) *runs* the shipped code; this doc explains how the
pieces are designed and the order to build them, plus the non-obvious decisions
that make it work.

Each phase ends at a **checkpoint** you can verify before moving on. Build in
order — later phases assume the earlier ones exist.

## The system in one paragraph

A local **gateway** (FastAPI) hosts an autonomous **crew board**: a goal
decomposes into dependency-linked tickets, a **dispatcher** runs each ticket
through a build → QA → review pipeline (one shell per task, git rollback on
failure), and a **verifier** refuses to call a ticket "done" without a real
commit and a live entrypoint. A **knowledge vault** daemon indexes markdown notes
for hybrid (vector + keyword) retrieval and self-synthesizes a wiki. A
theme-swappable **dashboard** shows it all live. Everything is model-routed
across local Ollama models with an explicit approval gate on anything that
leaves the box.

## Architecture map

```
                    ┌────────────────────────────────────────────┐
   you / voice ───▶ │  Gateway (FastAPI)                         │
                    │  ├─ crew board: tickets, lanes, dispatcher │──▶ git repos
                    │  ├─ build loop: agent + tools + verifier   │      (per project)
                    │  ├─ model router (per-role → Ollama/cloud) │──▶ Ollama
                    │  ├─ voice: STT → command → TTS             │
                    │  └─ self-heal watchdog + recovery          │
                    └───────────────┬────────────────────────────┘
                                    │ reads/writes
                    ┌───────────────▼────────────┐        ┌──────────────────┐
                    │  Vault daemon              │        │  Dashboard       │
                    │  ├─ index (vector + BM25)  │◀──────▶│  (theme-swappable│
                    │  ├─ chunk + embed          │        │   wall display)  │
                    │  └─ wiki synth + review    │        └──────────────────┘
                    └────────────────────────────┘
```

## Build order (phases)

### Phase 0 — Foundation
A FastAPI service, a config loader that reads `*.template` files (never
hard-code hosts/keys), and a SQLite store. Bind loopback only; refuse `0.0.0.0`.
**Checkpoint:** the service starts, serves a health endpoint on loopback, reads
config from templates.

### Phase 1 — Knowledge vault
A daemon that watches a folder of markdown notes, chunks each note, embeds the
chunks (Ollama embeddings), and stores vectors + an FTS index. Retrieval fuses
vector kNN and keyword (BM25) via reciprocal-rank, with a relative relevance
floor. Guard re-embedding with a content hash so unchanged notes are skipped.
**Checkpoint:** write a note → it's searchable by meaning and by keyword; edit
it → only the changed note re-embeds.

### Phase 2 — Crew board + store
Tickets with lanes (proposed → ready → in_progress → qa → review → done),
dependency links, assignees, and an audit trail. A goal decomposes into tickets.
**Checkpoint:** create a goal → it fans out into dependency-ordered tickets.

### Phase 3 — Build loop + verifier
An agent loop that, per ticket, calls tools (read/list/write/run) against a
project repo until acceptance criteria are met, one shell per task, with git
rollback on failure. The **verifier** is the keystone: "done" requires a *real
commit* AND a present entrypoint (e.g. a `main()`), so green-tests-dead-app
can't pass. **Checkpoint:** a trivial ticket builds, commits, and only then goes
green; a ticket that fakes tests without shipping is rejected.

### Phase 4 — Dispatcher
Polls ready tickets with an assignee, runs one per assignee at a time, moves
them through the lanes, parks failures for the owner after a capped number of
attempts, and reaps crash-orphans. **Checkpoint:** approve a ticket → it builds
→ QA → review without you touching it; a wedged run gets reaped, not stuck.

### Phase 5 — Model router
A model catalog (`model_catalog.yaml`) maps each role (coder, planner, reviewer,
summarizer, embedder…) to a model. Cross-check the catalog against what's
actually installed. A research pass proposes candidate models for your hardware
(it never auto-pulls), and a bench harness scores a candidate vs the incumbent
*per role* before adoption. Anything that pulls or leaves the box is behind an
explicit approval. **Checkpoint:** swap a role's model at runtime; bench a
candidate → get a scorecard.

### Phase 6 — Plan-first + human gates
A proposed ticket can draft a structured plan (goal + checkpoints + acceptance
criteria) that the human reviews and approves before it reaches the build queue.
Master plans break out into child tickets. **Checkpoint:** draft a plan on a
ticket → review the checkpoints → approve → it queues for the hive.

### Phase 7 — Dashboard
A theme-swappable wall display: the live board, GPU/telemetry, terminal, vault +
git activity, alerts, and a now-building rail. One accent token drives the whole
surface. Live agent "thinking" streams on in-progress/QA/review cards.
**Checkpoint:** the dashboard reflects board state in real time and reloads
cleanly on a service restart.

### Phase 8 — Self-heal
A watchdog that health-probes the service (HTTP-200 *serves*, not just a bound
port), a one-command recovery that hard-restarts + validates + reloads the wall,
and a dashboard "restart" button that works even when the main service is down
(it lives on a side channel). Boot validates the service actually serves before
declaring ready. **Checkpoint:** kill the service → it recovers on its own and
the display comes back live.

### Phase 9 — Persistent wiki + companion app
The vault self-synthesizes a wiki from what the hive learns, with a review queue
for contradictions/gaps. A companion app mirrors the board and does push-to-talk.
**Checkpoint:** the hive learns something → it shows up in the wiki behind a
review gate.

## Design principles (the non-obvious decisions)

- **Honest "done."** Tests passing ≠ done. Require a real commit + a live
  entrypoint. Prefer an external-signal probe (a live query returns hits; the
  app boots) over unit-green as the completion gate.
- **Hybrid retrieval, measured.** Vector + keyword fused, with a content-hash
  guard so the index never silently drifts from the query embedder. Keep the
  query-side and doc-side embedding model identical.
- **Plan before build.** Structured plan + human approval gate before autonomous
  work runs, so the hive builds against a spec, not a vibe.
- **Explicit gates on anything irreversible or outward-facing.** Model pulls,
  cloud calls, and public publishing sit behind an approval — enforced at the
  tool level, not as a prompt request.
- **Self-heal, don't just alert.** Validate that a service *serves* (not that a
  port is open), recover automatically, and reload the display so a wedged boot
  self-corrects.
- **Config from templates.** No hosts, paths, or keys in code — everything from
  `*.template` files the installer fills in. Bind loopback; refuse `0.0.0.0`.

## Recreate-from-nothing checklist

1. Clone, run the [installer](../installer/) (deps, Ollama + a model, vault
   scaffold, config from templates, a theme).
2. Build phases 0→9 in order; verify each checkpoint before the next.
3. Keep the publish gates (`scripts/release/check-*`) green if you ever make a
   public copy — no secrets, no personal data, SFW.

See also: [README](../README.md) · [crew-board-design](crew-board-design.md) ·
[CONFIG](CONFIG.md) · [QUICKSTART](QUICKSTART.md) · [MODELS](../MODELS.md).
