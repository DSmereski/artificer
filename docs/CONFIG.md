# Configuration

Everything is env + YAML. Copy `config/.env.template` → `config/.env` (the
installer does this) and edit `config/model_catalog.yaml`. `config/.env` is
gitignored — never commit real values.

## Key environment variables

| Var | Default | What |
|---|---|---|
| `OLLAMA_HOST` | `127.0.0.1:11434` | Ollama endpoint |
| `CUDA_VISIBLE_DEVICES` | (all) | Pin Ollama to specific GPU(s), e.g. `0` or `0,1` |
| `OLLAMA_NUM_GPU` | (auto) | `0` forces a model onto CPU |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | — | Optional cloud tier |
| `HIVE_VAULT_PATH` | `./vault` | Notes/knowledge dir (Obsidian-compatible) |
| `HIVE_PROJECTS_ROOT` | `~/projects` | Where greenfield projects are scaffolded |
| `DISCORD_BOT_TOKEN` / `HIVE_OWNER_DISCORD_ID` | — | Optional Discord bot (off by default) |

## Models

See [../MODELS.md](../MODELS.md). Swap any helper role in
`config/model_catalog.yaml`; the catalog refreshes against `ollama list` at
startup and fails loudly if a model isn't pulled (never silent-downgrades).

## GPU / multi-GPU

Ollama splits a model across GPUs by layer — set `CUDA_VISIBLE_DEVICES` to expose
them (e.g. `0,1`). **NVLink is not used or required.** A 7B model fits one ~8GB
card; the installer recommends a tier from your detected VRAM.

## Optional features (off by default)

Discord bots, the image-generation pipeline, and the Suno integration are
opt-in via their env vars / keys. Leave them unset to run a lean local setup.

### Image / video generation backend

The image-gen routes (`/v1/images/*`) and video routes require an external
backend — a local checkout of **imageToVideo** that exposes `core.ai_generate`
and `wan_video`. This is **not** bundled with Artificer.

To enable:

1. Clone/place the imageToVideo backend somewhere on your machine.
2. Set the env var in `config/.env`:
   ```
   HIVE_IMAGE_BACKEND_PATH=/path/to/imageToVideo
   ```
3. Alternatively, set `image_app_root` in `config/gateway.yaml` (the env var
   takes precedence).

Leave both unset (the default) to run Artificer without image generation.
The `images.image_app_root` key in `gateway.yaml` defaults to `null`.
