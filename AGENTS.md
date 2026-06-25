# AGENTS.md — Ai-Team / Hive

Cross-harness guide for any coding agent (Codex, Cursor, Gemini CLI, Copilot,
Claude Code, …). This system is **folders and files** — model- and tool-agnostic
by design. Whatever harness you are, start here.

> **Full orientation lives in [`CLAUDE.md`](./CLAUDE.md).** It holds the
> architecture map, the path cheatsheet ("where things live"), and the gotchas.
> Read it before navigating the code. This file is the short, must-know layer
> that every agent needs in front of it regardless of harness.

## What this is

A FastAPI **gateway** in front of a **Discord bot**, a **Flutter app**, a
**Hive coordinator** (LLM planner → helpers → synthesizer), and a **vault
writer** sidecar (SQLite + FTS5 + sqlite-vec) for the operator's notes. Single
owner, single machine. See `CLAUDE.md` for the component table and diagram.

## Non-negotiable guardrails (security boundary — never route around these)

A prompt is **not** a permission layer. These are enforced at the code/tool
level. Do not bypass them, and do not add a path that skips them.

- **Every vault operation goes through `shared/vault_client.py` (`VaultClient`).**
  Never call the daemon raw. New verbs get a method there.
- **Every mutating verb routes through `ActionExecutor`** (`gateway/action_executor.py`).
  No side-effects sneaked into routes or helpers.
- **Every vault write is `clamp_audience`'d** (`shared/audience.py`). Only
  `terry`, `claude-code`, `owner` get through — nothing else.
- **Every helper output flowing into the synthesizer is `sanitise_helper_outputs`'d**
  (`gateway/prompt_safety.py`). This is the prompt-injection boundary. No
  exceptions.
- **The `vault_forget` allowlist is closed.** Only specific paths are
  forgettable. Don't open it back up.
- **Risky verbs are allowlisted** at `gateway/hive_coordinator.py` (`risky_verbs`).
  Anything that can send/delete/mutate externally must be gated, not assumed safe.
- **Single-operator system.** `Device.user = "owner"` everywhere. Don't add a
  multi-user code path without an explicit ask.

## Entry points

| Goal | Command |
|---|---|
| Start the stack | `scripts/start-all.ps1` (vault-writer + gateway + bot + scout) |
| Stop it | `scripts/stop-all.ps1` |
| Restart just the gateway | `scripts/start-gateway.ps1` (idempotent) |
| Gateway in foreground | `python -m gateway` |
| Run all tests | `python -m pytest gateway/orchestrator/tests/ gateway/auditor/tests/ vault_writer/groomer/tests/ gateway/tests/ shared/tests/ hive_node_agent/tests/ vault_writer/tests/ -q` |
| Drive a multi-turn e2e | `python scripts/e2e_chat_driver.py --token <T> --host 127.0.0.1:8766` |

Logs land in `./logs/`.

## Working rules

- **TDD is enforced.** Write the failing test first. For concurrency caps,
  assert `peak == limit` exactly — never `peak <= limit` (passes trivially when
  the cap is wrong).
- **Atomic writes for durable state.** Use `shared/atomic_write.py::atomic_write_json`,
  never `open(..., "w")` on persistent files.
- **Track every fire-and-forget task** with `track_background_task`
  (`gateway/deps.py`). Untracked tasks leak across lifespan.
- **Helper model choice is data, not code.** Add `candidates: [...]` to a role in
  `config/model_catalog.yaml`; the Router picks per turn from bench results.
- Full convention list, path cheatsheet, and the qwen/Tailscale/uvicorn/Ollama
  gotchas: **see `CLAUDE.md`.**

## If the task is ambiguous

If you cannot identify the target file, repo, or goal — **stop and report.** Do
not create files, do not guess, do not run a self-interview. Act only on an
explicit, validated match.
