# CLAUDE.md — Ai-Team / Hive

Single-owner home AI stack. This file is the orientation doc for Claude sessions.
Read it first; it points at everything else.

## What this is

A FastAPI **gateway** in front of a **Discord bot** ("Terry"), a **Flutter app**,
a **Hive coordinator** (LLM planner + helpers + synthesizer), and a **vault
writer** sidecar that owns SQLite + FTS5 + sqlite-vec for the user's vault.

| Component | Path | Role |
|---|---|---|
| Gateway | `gateway/` | FastAPI service, chat WS, image-gen orchestration, admin UI |
| Hive coordinator | `gateway/hive_coordinator.py`, `gateway/helpers/` | LLM planner → helpers → synthesizer turn loop |
| Orchestrator | `gateway/orchestrator/` | Bench harness + Router that picks the best model per helper role from a YAML candidate list |
| Auditor | `gateway/auditor/` | Hourly self-audit: scans turn-logs for hallucinations / repeat questions / unhandled requests / security flags / skill gaps; writes `vault/ops/audits/<YYYY-MM-DD-HH>.md`, escalates HIGH findings to `vault/ops/escalations/` |
| Groomer | `vault_writer/groomer/` | Idle-time vault grooming — duplicate detection, broken-wikilink flagging, format checks, contradiction signals, stale-note flagging. Suggestions land in `<vault>/ops/groomer/<kind>/<slug>.md` (filesystem-as-truth, never auto-applied). Auto-fixes (whitespace, line endings) applied directly. Driven by `IdleGroomerLoop` (60s tick, 5-tick idle confirmation, runs one scanner per cycle). |
| Bot adapter | `gateway/bot_adapters/terry.py` | Connects gateway to Terry-the-Discord-bot |
| Vault writer | `vault_writer/` | Sidecar SQLite daemon, FTS5 + sqlite-vec, RRF hybrid search |
| Vault content | `./vault/` | The operator's notes/canon/knowledge (separate git repo) |
| Worker pool | `gateway/worker_pool/` | Node pairing (invite codes, registry, heartbeat) |
| Hive node agent | `hive_node_agent/` | CLI client that pairs a worker box with the gateway |
| Flutter app | `lib/` | iOS/Android client over WS + REST |
| Top-level scripts | `scripts/` | `start-all.cmd`, `e2e_chat_driver.py`, smokes |

## Architecture map

```
                           ┌─ Discord ──► Terry bot ──┐
   user types in chat ─────┤                          │
                           └─ Flutter app ────────────┤
                                                      │
                                                      ▼
                                            FastAPI gateway
                                                  │
       ┌──────────────────────────────────────────┼───────────────────────────────┐
       ▼                  ▼                       ▼                ▼              ▼
   chat WS         /v1/images/*          HiveCoordinator     ActionExecutor    /admin/*
                                                  │                  │
                                          (planner, helpers,         │ (verbs: vault_learn,
                                           synthesizer)              │  generate_image, etc.)
                                                  ▲                  │
                                       Router ───┘ (per-role         │
                                       (orchestrator) model choice)  │
                                                  │                  │
                                                  └──── outputs ─────┤
                                                                     ▼
                                                            VaultClient (RPC) ──► vault_writer daemon
                                                                                      │
                                                                                      ▼
                                                                            ./vault/
                                                                            + SQLite (FTS5 + vec)
```

The **Router** (Phase 1 of the self-improvement loop) sits beside the
HiveCoordinator: `_model_for(role)` consults bench results from
`state/bench_results.json` (written by `python -m gateway.orchestrator.bench_harness`)
to pick the best candidate per role using composite scoring (quality 0.5,
latency 0.3, cost 0.2). Falls back to the YAML default when no bench data exists.

Memory is conceptually three tiers (Letta-shaped):
- **Verbatim** — `LLMClient.recent_messages` (last 200 turns, JSON on disk).
- **Mid (slots)** — `gateway/conversation_memory.py::MemoryStore` (`mid_summary`,
  `user_facts`, `open_tasks`, `decisions`).
- **Recall** — `chat_log` table in vault (FTS5 + sqlite-vec), indexed per turn.
- **Archival** — the vault itself (canon/knowledge/journals).

A long-form roadmap sits at `docs/superpowers/specs/` and
`docs/superpowers/plans/`. Shipped: Phase 1 + Phase 2 of node-installer
(invite-pair flow + dispatcher/scheduler with admin UI), Phase 1 of the
memory overhaul (chat_log + threads + tiered memory), Phase 1 of the
self-improvement loop (bench-driven Router for per-role model choice +
VRAM-aware adaptive parallelism), Phase 2 of the self-improvement loop
(hourly chat-log auditor with 5 scanners + findings_writer + scheduler),
Phase 3 of the self-improvement loop (idle-time vault groomer).

## Entry points

| Goal | Command |
|---|---|
| Start the bot stack | `scripts/start-all.cmd` (Terry + Scout + gateway) |
| Stop it | `scripts/stop-all.cmd` |
| Restart just the gateway | the user has a `relaunch-app` agent / muscle memory; `scripts/start-gateway.cmd` |
| Run gateway in foreground | `python -m gateway` |
| Run vault writer | `python -m vault_writer` (started by gateway lifespan in normal use) |
| Run a node agent | `python -m hive_node_agent --invite <CODE>` |
| Run a bench sweep (orchestrator) | `python -m gateway.orchestrator.bench_harness [--role chat_recall]` (writes `state/bench_results.json`) |
| Run all tests | `python -m pytest gateway/orchestrator/tests/ gateway/auditor/tests/ vault_writer/groomer/tests/ gateway/tests/ shared/tests/ hive_node_agent/tests/ vault_writer/tests/ -q` (~168s, 994 passing) |
| Drive a multi-turn e2e | `python scripts/e2e_chat_driver.py --token <T> --host 127.0.0.1:8766` |

Logs land in `./logs/` (gateway, terry, scout, vault_writer).

## Where things live (path cheatsheet)

| Need to find... | Look in |
|---|---|
| Chat WebSocket route | `gateway/routes/chat.py` |
| Hive turn loop | `gateway/hive_coordinator.py`, `gateway/hive_turn_helpers.py` |
| LLM client + verbatim history | `shared/llm_client.py` |
| Vault RPC façade (always use this, not raw daemon calls) | `shared/vault_client.py` |
| Memory store (mid-tier) | `gateway/conversation_memory.py` |
| Helper base class + schema-validation hook | `gateway/helpers/base.py` |
| All helper schemas | `gateway/helpers/shapes.py` |
| Action verb dispatch (every mutating verb) | `gateway/action_executor.py` |
| Risky-verb allowlist | `gateway/hive_coordinator.py:303` (`risky_verbs` set) |
| Prompt-injection sanitiser | `gateway/prompt_safety.py::sanitise_helper_outputs` |
| Audience clamping (vault-write security) | `shared/audience.py::clamp_audience` |
| Background-task tracker | `gateway/deps.py::track_background_task` |
| App-state shape | `gateway/deps.py::AppState` (note: ~30 `getattr` calls — typing debt) |
| FastAPI lifespan | `gateway/app.py::lifespan` |
| Multi-host uvicorn config | `gateway/__main__.py::_build_uvicorn_configs` |
| Model catalog (helper → candidates list) | `config/model_catalog.yaml`, `gateway/model_catalog.py::ModelCatalog.candidates_for_role` |
| Bench corpus (canonical prompts per role) | `config/bench_corpus/<role>.jsonl` |
| Bench harness (sweep + scoring) | `gateway/orchestrator/bench_harness.py::run_full_sweep` |
| Router (per-turn model choice) | `gateway/orchestrator/router.py::Router.route_for` |
| Auditor scheduler (hourly cron) | `gateway/auditor/scheduler.py::AuditorScheduler` |
| Auditor run orchestration | `gateway/auditor/audit_run.py::run_audit` |
| Auditor scanners | `gateway/auditor/scanners/` (5 scanners: hallucination, repeat_question, unhandled_request, security, skill_gap) |
| Audit findings writer | `gateway/auditor/findings_writer.py::write_audit` |
| Groomer idle-detection loop | `vault_writer/groomer/idle_loop.py` |
| Groomer orchestration | `vault_writer/groomer/groom_run.py` |
| Groomer scanners | `vault_writer/groomer/scanners/` (dup, link, format, contradiction, stale) |
| Groomer suggestions writer | `vault_writer/groomer/suggestions_writer.py` |
| Groomer auto-fixers | `vault_writer/groomer/auto_fixers.py` |
| VRAM-aware concurrency cap | `gateway/hive_coordinator.py::TurnBudget.live_max_concurrent` |
| Vault SQLite schema + migrations | `vault_writer/index.py::_apply_migration` |
| Embedding worker | `vault_writer/embed_worker.py` |
| Node registry + invite broker | `gateway/worker_pool/registry.py`, `gateway/worker_pool/invites.py` |
| Admin UI (HTML/JS) | `gateway/admin/` (served via `/admin/`) |
| Prompts (planner, synth, summariser, etc.) | `prompts/` |
| Canon (read-only, human-curated) | `./vault/canon/` |
| Test fixtures | `gateway/tests/conftest.py`, `shared/tests/conftest.py` |

## Conventions to follow

- **Use `VaultClient`, not raw daemon calls.** Every vault operation goes
  through `shared/vault_client.py`. New verbs get a method there.
- **Use `track_background_task` for every fire-and-forget task.** Untracked
  tasks leak across lifespan and can fire after shutdown.
- **Every mutating verb routes through `ActionExecutor`.** Don't sneak
  side-effects into routes or helpers.
- **Every vault write is `clamp_audience`'d.** `terry`, `claude-code`, `owner`
  — nothing else gets through.
- **Every helper output flowing into the synthesizer is
  `sanitise_helper_outputs`'d.** No exceptions (prompt-injection boundary).
- **Per-loop `asyncio.Semaphore` lazy creation.** Module-level `Semaphore()`
  binds to the first event loop and breaks pytest-asyncio. See the pattern at
  `gateway/asset_importer.py:81-87` (`_get_import_semaphore`).
- **Atomic writes for persistent state.** Use `shared/atomic_write.py::atomic_write_json`.
  Never `open(..., "w")` directly on durable files.
- **Schema validation hook for prose-only LLM output.** `BaseHelper._parse_fallback`
  lets a helper wrap plain-prose output instead of dropping it. Synthesizer
  uses this; other helpers should consider it too.
- **Test pattern for concurrency caps:** park each fake on a per-task
  `asyncio.Event` and assert `peak == limit` exactly (not just `≤`). See
  `gateway/tests/test_asset_importer.py::test_recipe_sub_imports_caps_at_two`.
- **Helper roles declare candidate models in YAML, not in code.** Add
  `candidates: [primary_id, backup_id, ...]` to a helper in
  `config/model_catalog.yaml`. The Router picks per turn from bench data.
  Single-element `candidates` (or omitting the field) preserves static
  behaviour for back-compat.
- **Cloud models need both `cloud_provider` AND `cloud_model_name`.**
  Validation in `load_catalog` rejects entries with neither `ollama_name`
  nor a complete cloud-provider pair.

## Common gotchas

- **qwen3 emits `<think>...</think>` reasoning blocks.** Strip with
  `_THINK_BLOCK` / `_THINK_BLOCK_OPEN` from `gateway/helpers/base.py`. The
  closed form is checked first; open-only is the fallback.
- **Tailscale CGNAT is `100.64.0.0/10`.** Phone clients arrive on the
  `100.x` interface; the gateway binds both loopback and the Tailscale IP.
- **uvicorn WS keepalive defaults are too aggressive for mobile.** We set
  `ws_ping_interval=30`, `ws_ping_timeout=90` in `gateway/__main__.py`.
- **Lifespan runs once across all hosts.** `_serve_many` enters the lifespan
  context manually; per-host configs use `lifespan="off"`.
- **`Device.user = "owner"` everywhere.** Single-operator system. Don't add a
  multi-user code path without an explicit ask.
- **`getattr(app_state, "...", None)` is a smell, not a feature.** ~30 of
  these papers over weak typing on `AppState`. Tracked as long-term tech debt.
- **Reset clears `LLMClient` AND `MemoryStore`.** Older bug: only LLMClient
  got cleared, leaving stale `mid_user_facts` to leak into the next
  conversation. Fixed; don't regress.
- **The `vault_forget` allowlist is closed.** Only specific paths are
  forgettable. Don't open it back up.
- **Ollama must be started via `scripts/start-ollama-tuned.cmd`.** The
  tray autostart drops `CUDA_VISIBLE_DEVICES=1,2`, targets GPU0 (gaming
  4080), and silently falls back to CPU. Symptom: `ollama ps` shows
  planner-qwen `100% CPU` while GPU1 has free VRAM. Restart with the
  script; verify with `ollama ps` after one warm prompt. (#437, #438)

## Test discipline

- 837 tests; full suite runs in ~165s.
- TDD is enforced (`tdd-guide` agent, user's CLAUDE rules).
- **Don't write `assert peak <= limit`.** That passes trivially when the
  cap is wrong. Park tasks and assert `peak == limit`.
- **Don't write `await asyncio.sleep(0)`** in concurrency tests as your
  only yield point — it doesn't prove anything.
- Per-loop event objects are created **inside** the test's `_run()`
  coroutine, not at module scope.

## When in doubt

- Plans: `docs/superpowers/plans/`
- Specs: `docs/superpowers/specs/`
- Reviews: `docs/reviews/`
- Memory (auto-saved across sessions): `~/.claude/projects/.../memory/MEMORY.md`
- Related projects on the same machine: `imageToVideo/` (image gen pipeline),
  `vault/` (the vault content, separate git repo).

## Out of scope for this repo

- Image-gen pipeline internals — lives in `imageToVideo/`.
- The vault content itself — lives in `./vault/`, separate git repo.
- Discord library quirks — adapt around them in `gateway/bot_adapters/terry.py`.
