# Quickstart

From zero to a running Hiveforge in a few minutes.

## 1. Clone

```bash
git clone https://github.com/<you>/hiveforge && cd hiveforge
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

```bash
python -m gateway                      # the gateway (crew board + API)
cd dashboard && npm ci && npm run build # the dashboard
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
