# Quickstart

From zero to a running Artificer in a few minutes.

## Prerequisites

Install these before running the installer:

| Tool | Minimum | Recommended | Get it |
|------|---------|-------------|--------|
| **Python** | 3.10 | 3.11+ | https://python.org |
| **Node.js** | 18 | 20 LTS | https://nodejs.org |
| **Flutter** | 3.11 | latest stable | https://flutter.dev (only needed for app builds) |
| **Ollama** | latest | latest | https://ollama.com/download |

> The installer checks for Ollama and Python and prints a download link if they
> are missing. Flutter is only required if you want to build the companion app.

## 1. Clone

```bash
git clone https://github.com/<you>/artificer && cd artificer
```

## 2. Run the installer

It detects your GPU/VRAM, recommends a model, installs + pulls it, installs
Python deps, scaffolds the vault, writes `config/.env` from the template, and
lets you pick a model / theme / optional cloud key. Press Enter through for the
defaults.

```powershell
# Windows
powershell -ExecutionPolicy Bypass -File installer\install.ps1
```
```bash
# Linux / macOS
bash installer/install.sh
```

Prerequisites the installer checks for (and points you to if missing):
[Ollama](https://ollama.com/download), Python 3, and Node (for the dashboard).

## 3. Start

**Start vault-writer first** — the gateway waits up to 15 s for it before
accepting requests, so starting vault-writer first avoids that wait.

```bash
# Recommended: start everything at once (waits for vault-writer before gateway)
powershell -ExecutionPolicy Bypass -File scripts/start-all.ps1   # Windows
```

Or start services individually in this order:

```bash
python -m vault_writer                 # 1. vault sidecar (MUST start first)
python -m gateway                      # 2. the gateway (crew board + API)
cd dashboard && npm ci && npm run build # 3. the dashboard
```

**Dashboard, cross-platform:** on Windows it can run as a Lively wallpaper; on
**any OS** just serve the build and open it in a browser:

```bash
cd dashboard && npm run serve          # → http://localhost:4318
```

## 4. Verify

```bash
bash installer/verify-install.sh
```

Confirms config + vault exist, the model responds, and (if started) the gateway
is up.

## 5. Use it

Open the dashboard, or hit the API. Give the crew board a goal — it decomposes
the work into tickets and the hive builds them. See [CONFIG.md](CONFIG.md) for
every knob and [../MODELS.md](../MODELS.md) for swapping models.

> Default is **local + free** (Ollama). No data leaves your machine unless you
> set a cloud API key.
