<div align="center">

# 🐝 Hiveforge

### The hive that forges software.

**A self-hosted, autonomous AI dev team that runs on your own machine.**
Point it at a goal. It breaks the work into tickets, writes the code, runs the
tests, and only calls a ticket *done* when there's a real commit and the app
actually boots — all on a live command-center you watch in real time.

[![License: MIT](https://img.shields.io/badge/License-MIT-amber.svg)](LICENSE)
[![Release gates](https://img.shields.io/badge/release%20gates-enforced%20in%20CI-5EE65C.svg)](.github/workflows/gates.yml)
[![Local-first](https://img.shields.io/badge/local--first-Ollama-2BD4E8.svg)](https://ollama.com)
[![Python](https://img.shields.io/badge/gateway-FastAPI-5EE65C.svg)](gateway/)
[![Flutter](https://img.shields.io/badge/app-Flutter-7CE64A.svg)](app/)

</div>

---

> **Local-first by default.** Hive runs on your own GPU via [Ollama](https://ollama.com)
> with open-weight models — no API key, no data leaving your box. A cloud key
> (Anthropic / OpenAI) is optional, only for a stronger top tier.

## Why Hiveforge

Most "AI coding agents" are a chat box that hands you a diff. Hiveforge is the
opposite: a **standing team** with a **board**. You file a goal, it self-decomposes
into dependency-ordered tickets, and a build loop grinds each one to green —
writing files, running the test suite, reading its own failures, and retrying —
while you watch the board move from a dashboard on your wall.

```
   you ──"build a blackjack game in Flutter"──▶  ┌─────────────┐
                                                 │  Goal split │  ── ticket graph (deps)
                                                 └──────┬──────┘
                          ┌─────────────────────────────┼─────────────────────────────┐
                          ▼                              ▼                              ▼
                    ┌───────────┐                  ┌───────────┐                  ┌───────────┐
                    │  ticket   │  write→test→fix  │  ticket   │                  │  ticket   │
                    │  (engine) │ ───────────────▶ │ (UI)      │ ─ blocked-by ──▶ │ (polish)  │
                    └─────┬─────┘                  └───────────┘                  └───────────┘
                          ▼
                    ✅ verifier gate: real commit  +  app boots  +  tests green
```

## Features

- **🧩 Autonomous crew board** — a goal decomposes into tickets with real
  blocked-by links; the dispatcher runs them in dependency order, one shell per
  task, with git rollback on failure.
- **✅ Honest "done"** — a ticket isn't done because tests passed. The
  [verifier](gateway/crew_board/verifier.py) requires a **real commit** *and* a
  present **app entrypoint** (e.g. a Flutter `main()`) so "green tests, dead app"
  can't sneak through.
- **🔀 Model router** — per-task routing across local Ollama models and optional
  cloud models. Swap any role's model at runtime. See [MODELS.md](MODELS.md).
- **🖥️ Command-center dashboard** — a live, theme-swappable wall display: the
  full board, GPU/telemetry, a multi-session terminal, vault + git activity,
  alerts, and a now-building rail.
- **📚 Persistent wiki** — the vault self-synthesizes a wiki from what the hive
  learns, with a **review queue** for contradictions/gaps and an optional
  web **gap-fill** (Tavily / SearXNG) — every fetched snippet data-fenced and
  gated behind an explicit confirm.
- **🎨 Theme system** — multiple built-in themes + a live picker; one accent
  token drives the whole surface. See [docs/THEMING.md](docs/THEMING.md).
- **📱 Companion app** — a Flutter app ([`app/`](app/)) that mirrors the board and
  does push-to-talk to the hive.
- **🛠️ Guided installer** — detects your GPU/VRAM, installs Ollama + a model,
  scaffolds the vault, writes config from templates, picks a theme. Idempotent.

## What's in here

| Path | What it is |
|---|---|
| [`gateway/`](gateway/) | FastAPI service: crew board + autonomous build loop, model router, terminal, pairing, wiki |
| [`vault_writer/`](vault_writer/) | The knowledge vault daemon: indexing, retrieval, wiki synthesis + review queue |
| [`dashboard/`](dashboard/) | The command-center dashboard (theme-swappable) |
| [`app/`](app/) | The Hive companion app (Flutter) |
| [`skills/`](skills/) | The project's own 13 Claude Code skills |
| [`installer/`](installer/) | One guided setup — deps, models, vault, config |
| [`config/`](config/) | `model_catalog.yaml` + `*.template` config |
| [`scripts/release/`](scripts/release/) | Publish gates (`check-secrets` / `check-personal` / `check-nsfw`) |

## Quick start

```bash
# 1. clone
git clone https://github.com/<you>/hiveforge && cd hiveforge

# 2. run the installer — detects your GPU/VRAM, installs Ollama + a model,
#    scaffolds the vault, writes config from templates, picks a theme.
./installer/install.sh        # (install.ps1 on Windows)

# 3. start
./scripts/start-all.ps1       # gateway + dashboard
```

The installer is idempotent — re-run it any time. Full walkthrough in
[docs/QUICKSTART.md](docs/QUICKSTART.md).

## How the build loop works

1. **Decompose** — a goal becomes a graph of tickets with explicit `blocked-by`
   edges; nothing starts before its dependencies are done.
2. **Build** — for each ready ticket, the agent loop gets a sandboxed shell:
   list/read/write files, run commands, run the tests. It reads its own pytest
   output and retries.
3. **Verify** — tests green is necessary, not sufficient. The verifier also
   demands a real commit landed and the app's entrypoint exists. Fail any gate →
   the ticket stays open with a reason.
4. **Learn** — outcomes feed the vault; the wiki synth turns recurring lessons
   into wiki pages and queues contradictions for your review.

Architecture notes for the board live in
[docs/crew-board-design.md](docs/crew-board-design.md).

## Models

Local-first via Ollama, swappable per task. The installer auto-detects your
hardware and recommends a tier; change any role's model at runtime. Full list +
source links + licenses + VRAM tiers in [MODELS.md](MODELS.md).

> Multi-GPU is handled by Ollama (layer-split via `CUDA_VISIBLE_DEVICES`).
> NVLink is **not** used or required.

## Configuration

Everything is env + `config/*.template.yaml` — copy the templates, fill in your
values; nothing personal is baked into the code. Knobs (model swap, GPU pinning,
optional cloud features) are documented in [docs/CONFIG.md](docs/CONFIG.md).

## Companion skills

Hive ships its own 13 skills. The author also runs several third-party Claude
Code skills/plugins that pair well — see [SKILLS.md](SKILLS.md) for what they are
and where to get them (they are **not** bundled).

## ⚠️ Security & threat model — read before running

**Hiveforge runs AI-generated code on your machine, as you. Treat it like that.**

- The build loop and the optional cloud runner **execute commands and write
  files on the host**. **Anyone who can create a board task can cause code to run
  as your user.** Don't expose the gateway beyond your own machine; don't feed it
  tasks from untrusted sources.
- It is designed for a **single operator on loopback**. By default the gateway
  binds `127.0.0.1` (and refuses `0.0.0.0`), the terminal endpoint is
  loopback-only + token-gated, and board mutations need a token — keep it that
  way. A shared/public network removes those assumptions.
- The PowerShell/PTY terminal and the agent's `run_cmd` tool are **full shells**
  on your box. That's the product, not a bug — but you're trusting the local
  model (and yourself) the way you'd trust any script you run.
- Run it in a VM or container if you want isolation. Use a dedicated, scoped
  account for any cloud API keys.

If that model isn't acceptable for your environment, don't run it exposed.

## Project status

Hiveforge is the public mirror of a private, actively-developed system. Releases
are cut behind three automated gates ([`scripts/release/`](scripts/release/)) that
block any secret, personal marker, or unsafe model from reaching a public commit
— enforced in CI on every push ([`.github/workflows/gates.yml`](.github/workflows/gates.yml)).

## License

MIT — see [LICENSE](LICENSE). Covers Hive's own code; bundled-by-reference models
and third-party skills keep their own licenses.
