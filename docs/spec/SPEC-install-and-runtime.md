# SPEC -- Install & Runtime

**Reimplementation-grade spec.** Rebuild the installer, config-from-templates system,
self-heal/watchdog layer, Flutter companion app, and voice pipeline from this document
alone. Every host here is loopback (`127.0.0.1`); the only other permitted bind is an
optional operator-supplied mesh/VPN interface -- never `0.0.0.0`. Canonical local ports:
gateway `8766`, vault-writer daemon `8765`, Ollama `11434`.

## 1. Installer

Two functionally-parallel scripts: `installer/install.ps1` (PowerShell/Windows) and
`installer/install.sh` (POSIX/Linux+macOS). Both `cd` to the repo root first
(`Split-Path -Parent $PSScriptRoot` / `cd "$(dirname "$0")/.."`) so all relative paths
resolve from the project root. Both are **idempotent -- safe to re-run at any time.**

### 1.1 Flags / interactivity
- Sole flag: `-NonInteractive` (PS switch) / positional `--non-interactive` (bash). When
  set, every prompt returns its default with no console read.
- No `--force` and no `--skip-model` exist. The only "already installed" gate is file
  existence (see 1.5). Re-running never destroys an existing `config/.env`.
- Interactive prompts (`Ask`/`ask` helper): primary model (default = the VRAM-tier
  recommendation), optional cloud API key, vault path (default `./vault`), theme (default
  `warm-black`; options `warm-black` | `light` | `neutral-dark`).

### 1.2 GPU / VRAM detection -> model tier
1. Probe for `nvidia-smi` on PATH.
2. If present, run
   `nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits`; take the
   first line, split on `,` -> `(gpu_name, vram_mb:int)`.
3. Select a recommended primary model by VRAM tier. The PowerShell script uses three
   tiers; the bash script collapses to two (reimplementers SHOULD standardize on the
   three-tier table):

   | VRAM (MB)     | Recommended primary   | Label            |
   |---------------|-----------------------|------------------|
   | `>= 16000`    | `qwen2.5-coder:7b`    | comfortable      |
   | `8000-15999`  | `qwen2.5-coder:7b`    | good             |
   | `1-7999`      | `qwen3:8b`            | small GPU        |
   | `0` / no GPU  | `qwen3:8b`            | CPU / cloud only |

4. Entering `cloud` at the model prompt **skips the Ollama path entirely** (cloud-tier
   inference only).

### 1.3 Ollama + model pull
- The installer **does not install Ollama.** It checks `ollama` on PATH; if absent,
  PowerShell opens `https://ollama.com/download` and hard-stops (`throw`, non-zero exit),
  bash prints the URL and `exit 1`. Ollama is a manual prerequisite.
- Pull the chosen primary: `ollama pull <model>` (Ollama no-ops if cached -- the pull's
  idempotency). Then unconditionally ensure the four **baseline** models, skipping any
  equal to the primary: `qwen2.5-coder:7b`, `qwen3:8b`, `gemma3:4b`, `nomic-embed-text`
  (required for vault semantic search and the assistant/coder helper roles).

### 1.4 Python deps + vault scaffold
- Require `python`/`python3` on PATH (throw/exit if absent). If a root `requirements.txt`
  exists: `python -m pip install -q -r requirements.txt`.
- Vault scaffold at the chosen path (default `./vault`): create the root plus subdirs
  `canon/`, `notes/`, `plans/`. Write a one-line `README.md` into the vault **only if
  absent** (`# Vault`). Never overwrite an existing vault README.

### 1.5 Config from templates (exact mechanism)
Config generation is **literal file copy plus append -- never token substitution.**
1. Idempotency gate: if `config/.env` exists (`Test-Path` / `[ -f ]`), print
   `exists (left as-is)` and touch it no further.
2. Else copy `config/.env.template` -> `config/.env` verbatim, then **append**:
   `ANTHROPIC_API_KEY=<value>` (only if the operator supplied a cloud key) and
   `HIVE_VAULT_PATH=<chosen vault path>` (always).
3. `config/.theme` is written every run with the raw theme value -- intentionally not
   idempotent.

### 1.6 End state
Print next-step commands: start the gateway (`python -m gateway`), build the dashboard,
run verify (1.7). Reassert "idempotent -- re-run any time."

### 1.7 Verify step -- `installer/verify-install.sh`
Sequential checks; each failure sets `fail=1` but does **not** early-exit. Process exit
code = `$fail` (`0` pass / `1` fail).
1. `config/.env` exists -> else fail.
2. `vault/` directory exists -> else fail.
3. `config/model_catalog.yaml` exists -> else fail.
4. **Model smoke test** (only if `ollama` on PATH): read the first catalog model --
   `grep -m1 'ollama_name:' config/model_catalog.yaml | awk '{print $2}'` -- then
   `ollama run "<model>" "reply with one word: ok"`; exit 0 = pass, else fail with an
   `ollama pull <model>` hint. If Ollama is absent, print a non-failing informational
   line (cloud-only mode).
5. **Gateway liveness** (informational, never sets `fail`):
   `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8766/board/stats --max-time 4`;
   pass note if `200`, else a non-fatal note.

Final line: `install verified` if `fail=0`, else `issues above`.

## 2. Config from templates

**Invariant: no hosts, keys, or secrets live in code.** Everything runtime-tunable is read
from committed YAML or from a gitignored `.env`.
- **`config/.env.template`** is the *only* true `*.template` file -- the seed copied to
  `config/.env` (1.5). `config/.env.example` is a documentation twin with every
  secret-shaped value replaced by the literal `<PLACEHOLDER>`; treat it as read-only
  reference, not an input.
- **`config/gateway.yaml`, `config/vault-writer.yaml`, `config/model_catalog.yaml`** are
  committed defaults **read directly at runtime** -- not templates, not copied/renamed.
  They ship with safe defaults and are edited in place.
- `.env` is gitignored. Header requirement: *"Nothing here is required to START (sane
  defaults). `.env` is gitignored -- never commit real values."*

### 2.1 `.env` keys (names only; values operator-supplied)
- Runtime/models: `OLLAMA_HOST` (default `127.0.0.1:11434`), `CUDA_VISIBLE_DEVICES`
  (commented GPU pin), `OLLAMA_NUM_GPU` (commented; `0` forces CPU), `ANTHROPIC_API_KEY`,
  `OPENAI_API_KEY` (both commented/optional cloud tier).
- Paths: `HIVE_VAULT_PATH` (`./vault`), `HIVE_PROJECTS_ROOT` (`~/projects`),
  `HIVE_PROMPTS` (commented, `./prompts`).
- Optional features (off by default): `DISCORD_BOT_TOKEN`, `SCOUT_BOT_TOKEN`,
  `HIVE_OWNER_DISCORD_ID`, `PROACTIVE_HIVE_ENABLED=false`, image/video backend paths.
- Optional integrations: `GITEA_TOKEN`, `CIVITAI_API_KEY`, `COMPOSIO_API_KEY`. Media
  device indices: `VOICE_DEVICE`, `VIDEO_DEVICE`.

### 2.2 Loopback enforcement (bind policy)
- `gateway.yaml`: `bind_host: 127.0.0.1` (default). The only other permitted host is an
  optional `tailscale_bind` (an operator mesh/VPN interface; `null` by default).
  `vault-writer.yaml`: `daemon_bind_host: 127.0.0.1`.
- The loopback-only rule is **validated in code at config-load time** (see 3.2), not
  merely by comment. A non-loopback / `0.0.0.0` value is rejected with a hard error.

## 3. Self-heal & runtime binding

### 3.1 Server entrypoint & multi-host serve
- Entry: `python -m gateway` -> `main()` loads `config/gateway.yaml`, builds
  `hosts = [bind_host]` (+ `tailscale_bind` if set), then
  `asyncio.run(_serve_many(app, hosts, bind_port))`.
- `_serve_many`: run the FastAPI lifespan **exactly once** (`app.router.lifespan_context`),
  then start **one `uvicorn.Server` per host** over a **single shared app** via
  `asyncio.gather(..., return_exceptions=True)`. N independent listeners, one app instance.
- Port: `bind_port` default `8766`, validated `0 < port <= 65535`.
- WS keepalive tuned for mobile: `ws_ping_interval=30s`, `ws_ping_timeout=90s` (the
  library defaults of 20/20 drop backgrounded phones).

### 3.2 Loopback-must-bind invariant + refuse-`0.0.0.0`
- Validation predicate `_is_loopback_or_tailscale(host)` accepts exactly: `127.0.0.1`,
  `::1`, `localhost`, any `127.*`, or an operator mesh/VPN address/hostname. It rejects
  `0.0.0.0` and all public IPs. At config load, `bind_host` (default `127.0.0.1`) and
  `tailscale_bind` are each run through this predicate; failure raises `ValueError` before
  the server starts.
- **Retry-bind across interfaces** (`_serve_one`):
  - `hosts[0]` (loopback) is **must-succeed, no retry** -- always locally available; a
    failure logs and returns without crashing sibling listeners.
  - `hosts[1+]` (mesh/VPN) uses `retry_bind=True`: on any bind/serve failure it sleeps a
    fixed `REBIND_RETRY_S = 30s` and retries **forever** (no backoff growth). The catch
    must trap both `Exception` **and** `SystemExit` (uvicorn raises `SystemExit`, a
    `BaseException`, on a failed bind -- otherwise it escapes `gather()`). Rationale: the
    mesh interface is often not up yet at boot/login; without retry the process stays
    loopback-only until a manual restart.

### 3.3 Watchdog / self-heal -- two complementary layers
A supervisor process (the "scout daemon") runs both loops.

**Layer A -- process-liveness watchdog** (interval `45s`): enumerate processes
(`Win32_Process` / `ps`) and regex-match an **allow-list** of command lines only (e.g.
`-m gateway`, the bot process). This is **PID-existence only**, not HTTP. If a required
process is missing -> restart it by spawning its start script **detached**
(`DETACHED_PROCESS`). The allow-list is a hard boundary -- nothing outside it is ever
matched or killed.

**Layer B -- HTTP health supervisor** (interval `45s`): the real *serves-HTTP-200, not
just port-open* check. `probe_gateway()` issues `GET /health`, falling back to
`GET /board/state` (both loopback:8766, 5s timeout, catches `URLError`/`OSError`); the
**first `200` wins**. A reimplementation SHOULD expose a dedicated `/health` returning
`200`; the `board/state` fallback exists so an older gateway without `/health` still
gates. On `FAIL_THRESHOLD = 3` consecutive non-200/unreachable probes -> restart the
gateway, then reset the counter so the new process can boot before re-counting.

Layer A catches *process-not-running*; Layer B catches *process-running-but-wedged*.

### 3.4 Circuit breaker (anti-restart-storm)
A pure, I/O-free state object holds a rolling deque of restart timestamps.
`MAX_RESTARTS_PER_HOUR = 3` within `CIRCUIT_WINDOW_S = 3600`. On the 4th restart inside
the window the breaker **trips and stays open** -- no further auto-restarts -- and fires a
single urgent push notification (title e.g. "supervisor: CIRCUIT OPEN"). Auto-restart is
gated by a boolean (`GATEWAY_AUTORESTART`, env-overridable, default on).

### 3.5 One-command recovery
`scripts/boot-all.ps1` is the idempotent one-command bring-up/recovery: it checks for an
existing Ollama process and service PIDs before starting anything, starts Ollama, then
delegates to `scripts/start-all.ps1`, which is idempotent per-service (`Test-BotRunning`
via command-line match) and **TCP-polls each service's port** after spawn against a
deadline (e.g. vault-writer `8765` 15s, gateway `8766` 10s). Both are safe to re-run;
re-running is the recovery. Layer B additionally issues a post-recovery display-refresh
nudge on platforms that host a live dashboard surface (a no-op where none exists).

## 4. Companion app (Flutter)

Offline-first mobile client that mirrors the board and drives push-to-talk.

### 4.1 Layers
- `GatewayClient` -- typed REST + WebSocket client; Bearer auth.
- `SyncGateway` (abstract) + `GatewayAdapter` (impl) -- narrow seam the sync loop depends
  on (swap a `FakeGateway` in tests).
- `SyncService` -- one supervisor loop: connect -> hydrate -> live, plus WS-frame handling,
  heartbeat, backoff, outbox drain.
- `AppDatabase` -- **Drift** (SQLite); the **only** UI read source.
- Repositories (`BoardRepository`, `EscalationRepository`, `ChatRepository`,
  `ActivityRepository`) -- optimistic writes patch the DB (`dirty=1`) and enqueue an outbox op.
- Riverpod providers wire it together (`sessionProvider`, `gatewayClientProvider`, ...).

**Offline-first is real:** the UI never reads the gateway directly (Drift is the single
read source); local writes are optimistic + queued; any hydrate overwrites local rows and
clears `dirty`.

### 4.2 Pairing
- QR payload: `app://pair?url=<gatewayUrl>&code=<code>` (a bare JSON
  `{"url":...,"code":...}` is accepted as a fallback). A `PairPayload.tryParse` handles
  both forms. Scanning uses the `mobile_scanner` package.
- Redeem: `POST <gatewayUrl>/v1/pair` with JSON
  `{"code": <code>, "name": "companion phone", "platform": "android"}` -> response
  `{"token": "<bearer>"}`.
- Manual entry alternative: a `connect(url, secret)` path auto-detects a short pairing
  code (regex `^[A-Z0-9]{4,12}$`) vs. a raw token -- it only calls the pair endpoint when
  the input looks like a code.
- Persistence: `SharedPreferences` keys `v2.gateway.url` and `v2.gateway.token`.
- Every REST call sends `Authorization: Bearer <token>`. WebSocket connects pass the token
  as a `?token=<token>` query param (WS clients cannot set headers).
- Auth-failure policy: a 401/403 fires an `onAuthFailed` hook **once**, but the app
  deliberately leaves it unset so transient blips do **not** wipe the saved session; only
  an explicit Disconnect clears the prefs.

### 4.3 Board mirror (hybrid pull + push)
- WS `/v1/events` (token as query param) delivers **lossy signal frames that never carry
  trusted deltas.** Any frame whose `type` in `{task_progress, task_moved, task_created,
  task_updated, board, approval}` triggers a **full re-pull** of `GET /board/state`;
  escalation frames re-pull `/v1/escalations`; chat frames re-pull that bot's messages.
- A 30s heartbeat `Timer.periodic` forces a `board/state` re-pull even with no frames, to
  catch silent NAT/Wi-Fi drops (a failed pull throws -> drop handler -> reconnect).
- Reconciliation is **full-snapshot replace**: upsert every snapshot row in one
  transaction, then `DELETE WHERE slug NOT IN (<kept>)` (honors server archive/delete).
  Gateway-wins -- this clobbers local `dirty` rows.
- Optimistic local writes set `dirty=true` and show immediately; they survive only until
  the next authoritative upsert (no field merge, last hydrate wins). Outbox ops are
  separate rows replayed **FIFO, one in-flight** (preserves approve-then-reject order); a
  failing op retries to `maxAttempts=5` then is marked `failed`.
- Reconnect FSM: `disconnected -> hydrating -> live`; backoff `[1, 2, 5, 15, 30] s`.

### 4.4 Push-to-talk
- Capture: the `record` package (`AudioRecorder`) with
  `RecordConfig(encoder: wav, sampleRate: 16000)` -> 16 kHz mono WAV to a temp file.
- Trigger: **tap-to-toggle** -- first tap starts, second tap stops and sends.
- Upload: read WAV bytes -> `POST /v1/stt` with `Content-Type: audio/wav`, raw bytes as
  the body (not multipart) + Bearer header. Response `{"text": ..., "duration_s": ...}`.
- Reply path: the transcript is **not** posted to a voice endpoint; it is handed to the
  normal chat send (`onSend(transcript)`) and streamed through the standard chat WebSocket
  `/v1/chat/{bot}`, so the full coordinator pipeline answers and the reply streams back as
  assistant/trace frames into the chat view. (A separate interleaved-audio voice WS exists
  but is deprecated for the phone UI.)

## 5. Voice pipeline -- endpoint contracts

Three capabilities: STT (audio->text) as a standalone REST route; a combined
STT->command->TTS round trip over one WebSocket; TTS only as an internal step of that WS
(no standalone TTS route). **All routes require a Bearer device token** (`HTTPBearer`);
device tokens and node tokens are cross-rejected against the wrong registry.

### 5.1 STT -- `POST /v1/stt`
- **Request:** raw `audio/wav` body **or** multipart form field `audio` (content-type
  sniffed). Audio = 16 kHz mono s16le PCM WAV. Optional `?lang=en` (accepted, may be
  ignored; keep for contract stability). Auth: Bearer.
- **Limits:** body > 2 MB -> `400 {"detail":"audio exceeds 2 MB limit"}`; duration > 30 s
  (from WAV header) -> `400`; empty body -> `400 {"detail":"audio body is empty"}`;
  unparseable WAV -> `400` with the parse error.
- **Success:** `200 {"text": "<transcript>", "duration_s": <float, 3dp>}`.
- **Unavailable:** pipeline not loaded or transcription throws ->
  `503 {"detail":"ASR backend unavailable"}`.
- **Engine:** local Whisper (`openai/whisper-large-v3`) via a transformers ASR pipeline,
  forced English, no timestamps; input resampled to 16 kHz if needed.

### 5.2 Command -- `WS /v1/voice/{bot}` (STT + LLM + TTS in one turn)
There is **no** text-in/JSON-out "command" REST route; the command step is the bot
adapter's own `chat(user_id, text) -> str` (an LLM call, no separate intent/keyword parser).
- **Connect:** Bearer token via `Authorization` header **or** `?token=<token>` query.
  `{bot}` selects a persona adapter (e.g. `assistant`, `researcher`); a code-only agent
  with no audio persona and any unknown bot are rejected with WS close `1008`.
- **Client -> server:** one **binary** WS frame = a complete WAV (any sample rate; server
  resamples). A non-binary frame -> error frame "expected binary WAV".
- **Server -> client**, in order: `{"type":"transcript","text":...}` JSON,
  `{"type":"assistant","text":...}` JSON, **binary** WAV bytes (only if reply non-empty),
  then `{"type":"done"}` JSON.
- **Errors** (`{"type":"error","message":...}`; socket stays open for the next turn):
  non-binary input, empty transcript ("no speech detected"), or pipeline exception.
- **State:** none server-side between turns beyond the adapter's own conversation memory
  keyed by a derived `user_id` (hash of device id); no session id is exchanged.

### 5.3 TTS (internal only)
- `synthesize(text) -> wav_bytes`, invoked from the WS pipeline after the LLM reply.
- **Engine:** local SpeechT5 (`microsoft/speecht5_tts` + `microsoft/speecht5_hifigan`
  vocoder), a fixed speaker embedding from `config/speaker_embedding.npy`, 16 kHz mono WAV
  out.
- Text is hard-truncated to 180 chars, snapped back to the last sentence/clause boundary
  (`. `, `! `, `? `, `, `) if that boundary is past the halfway point.
- Both STT and TTS models lazy-load on first use and share one GPU device string
  (`VOICE_DEVICE`, a local device index -- not a secret).

## 6. Verifiable checkpoints

**C1 -- Installer idempotency & config seed.** Run the installer twice. After run 1,
`config/.env` exists and ends with a `HIVE_VAULT_PATH=` line and `vault/{canon,notes,plans}`
exist. Run 2 prints `exists (left as-is)` and leaves `config/.env` byte-identical
(`sha256sum` matches across runs; `test -d vault/canon`).

**C2 -- Verify gates correctly.** `bash installer/verify-install.sh; echo $?` -> `0` on a
good install. Delete `config/.env`, re-run -> `1` with the `.env` failure line. The
gateway-liveness line is informational and must never flip the exit code.

**C3 -- Refuse non-loopback bind.** Set `bind_host: 0.0.0.0` and start `python -m gateway`:
startup fails fast with a `ValueError` at config load (no listener opened). Restore
`127.0.0.1` -> binds; `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8766/board/stats`
-> `200`.

**C4 -- HTTP-200 health gate, not port-open.** With the gateway up, `probe_gateway()`
returns healthy (`200` from `/health` or `/board/state`). Wedge it (accept TCP but return
non-200 / hang) and confirm the supervisor counts 3 consecutive failures then restarts --
proving the probe is HTTP-200, not a bare TCP connect. The breaker stops after 3
restarts/hour and emits one urgent notification.

**C5 -- Voice round trip.** `POST /v1/stt` with a Bearer token and a <=30 s / <=2 MB 16 kHz
mono WAV -> `200` with non-empty `text` and numeric `duration_s`. Open `WS /v1/voice/{bot}`,
send that WAV as one binary frame, and receive, in order: `transcript` JSON, `assistant`
JSON, binary WAV, `done` JSON. Unauthenticated STT -> `401`; a > 2 MB body -> `400`.
