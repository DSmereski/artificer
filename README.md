# Hiveforge

**The hive that forges software.** A self-hosted, autonomous AI dev team you run
on your own machine — a crew-board build pipeline, a model router over local +
cloud models, and a live command-center dashboard. Point it at a goal; it
decomposes the work into tickets and builds them.

> **Local-first.** By default Hive runs on your own GPU via [Ollama](https://ollama.com)
> with open-weight models — no API key, no data leaving your box. A cloud key
> (Anthropic / OpenAI) is optional for a stronger tier.

## What's in here

| Path | What it is |
|---|---|
| `gateway/` | FastAPI service: crew board + autonomous build loop, model router, terminal, pairing |
| `dashboard/` | The command-center dashboard (theme-swappable) |
| `app/` | The Hive companion app (Flutter) |
| `skills/` | The project's own Claude Code skills |
| `installer/` | One guided setup — deps, models, vault, config |
| `config/` | `model_catalog.yaml` + `*.template` config |
| `scripts/release/` | Publish gates (`check-secrets`/`check-personal`/`check-nsfw`) |

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

The installer is idempotent — re-run it any time. See [docs/QUICKSTART.md](docs/QUICKSTART.md).

## Models

Local-first via Ollama, swappable per task. The installer auto-detects your
hardware and recommends a tier; you can change any role's model at runtime.
Full list + source links + licenses + VRAM tiers in [MODELS.md](MODELS.md).

> Multi-GPU is handled by Ollama (layer-split via `CUDA_VISIBLE_DEVICES`).
> NVLink is **not** used or required.

## Configuration

Everything is env + `config/*.template.yaml` — copy the templates, fill in your
values, nothing personal is baked into the code. Knobs (model swap, GPU pinning,
optional Discord/cloud features) are documented in [docs/CONFIG.md](docs/CONFIG.md).

## Theming

The dashboard ships multiple themes and a picker — see [docs/THEMING.md](docs/THEMING.md).

## Companion skills

Hive ships its own skills. The author also runs several third-party Claude Code
skills/plugins that pair well — see [SKILLS.md](SKILLS.md) for what they are and
where to get them (they are not bundled).

## ⚠️ Security & threat model — read before running

**Hiveforge runs AI-generated code on your machine, as you. Treat it like
that.** Specifically:

- The crew board's build loop and the optional Claude runner **execute commands
  and write files on the host** to do their work. **Anyone who can create a
  board task can cause code to run as your user.** Don't expose the gateway
  beyond your own machine, and don't feed it tasks from untrusted sources.
- It is designed for a **single operator on loopback**. By default the gateway
  binds `127.0.0.1` (and refuses `0.0.0.0`), the terminal endpoint is
  loopback-only + token-gated, and board mutations need a token — keep it that
  way. Putting it on a shared/public network removes those assumptions.
- The PowerShell/PTY terminal and the agent's `run_cmd` tool are **full shells**
  on your box. That's the point of the product, not a bug — but it means you are
  trusting the local model (and yourself) the way you'd trust any script you run.
- Run it in a VM or container if you want isolation. Use a dedicated, scoped
  account for any cloud API keys.

If that model isn't acceptable for your environment, don't run it exposed.

## License

MIT — see [LICENSE](LICENSE). Covers Hive's own code; bundled-by-reference models
and third-party skills keep their own licenses.
