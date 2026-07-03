# SPEC: Model Router + Catalog + Bench

Reimplementation spec for the gateway's model catalog, helper-role router, availability reconciliation, and candidate bench harness. Grounded in `config/model_catalog.yaml`, `gateway/model_catalog.py`, `gateway/routes/models.py`, `gateway/orchestrator/*.py`, `gateway/helpers/factory.py`, `gateway/helpers/base.py`, `scripts/benchmark_models.py`, `scripts/spawn_tier_eval.py`.

## 1. Catalog file: `config/model_catalog.yaml`

Two top-level lists, `models` and `helpers`, parsed by `load_catalog(path)` into two dicts keyed by id.

### 1.1 `models[]`

| Key | Type | Default | Notes |
|---|---|---|---|
| `id` | string | required | Unique; duplicate → `ValueError` at load. |
| `ollama_name` | string\|null | null | `ollama pull` tag. Required unless `cloud_provider`+`cloud_model_name` set. |
| `family` | string | `"unknown"` | Free-text label. |
| `gpu_vram_mb` | int | `0` | `0` = CPU-only / cloud. |
| `cpu_ram_mb` | int\|null | null | `null` = cannot run on CPU. |
| `cpu_fallback` | bool | `false` | May run with `num_gpu=0` when VRAM contested. |
| `speciality` | string | `""` | Human-readable, surfaced in system-prompt summary. |
| `use_for` | list[string] | `[]` | Informational eligible roles; authoritative list is `helpers[].candidates`. |
| `params` | dict | `{}` | Default Ollama `options` (e.g. `temperature`, `num_predict`). |
| `cloud_provider` | string\|null | null | e.g. `"anthropic"`. Presence marks it a cloud model. |
| `cloud_model_name` | string\|null | null | Provider-specific wire id. |
| `cost_per_1k_tokens_input` | float | `0.0` | USD. |
| `cost_per_1k_tokens_output` | float | `0.0` | USD; used by router cost term + bench scorecard. |

Load-time: every entry needs `ollama_name` OR (`cloud_provider` AND `cloud_model_name`), else `ValueError`.

```yaml
models:
  - id: planner-qwen
    ollama_name: qwen2.5-coder:7b
    family: qwen2.5
    gpu_vram_mb: 5200
    cpu_ram_mb: 6500
    cpu_fallback: true
    speciality: planning, coding, research, synthesis
    use_for: [planner, coder, researcher, synthesizer]
    params: {temperature: 0.3, num_predict: 1024}
  - id: claude-haiku-4-5-20251001
    cloud_provider: anthropic
    cloud_model_name: claude-haiku-4-5-20251001
    cost_per_1k_tokens_input: 0.0008
    cost_per_1k_tokens_output: 0.004
    speciality: fast cloud reasoning
    use_for: [chat_recall, summarizer]
    params: {}
```

### 1.2 `helpers[]`

| Key | Type | Default | Notes |
|---|---|---|---|
| `role` | string | required | Unique; duplicate → `ValueError`. |
| `model` | string | required | Primary/default model id; must exist in `models`. |
| `candidates` | list[string] | `[model]` | Ordered pool the router scores; each id must exist in `models`. |
| `system_prompt_file` | string | required | Path to role's system prompt. |
| `output_schema` | string | `"dict"` | Name of the registered output-shape class. |
| `timeout_s` | int | `30` | Hard per-call timeout. |
| `params` | dict | `{}` | Per-helper `options`, merged over the resolved model's `params` (helper wins on conflict). |

```yaml
helpers:
  - role: researcher
    model: planner-qwen
    candidates: [planner-qwen, gemma3-4b]
    system_prompt_file: prompts/researcher.md
    output_schema: ResearchPlan
    timeout_s: 90
```

## 2. Role map + runtime resolution

Fixed roles (one `HelperEntry` + one Python class each): `planner`, `coder`, `researcher`, `image_director`, `sysmon`, `summarizer`, `critic`, `librarian`, `synthesizer`, `skill_runner`, `chat_recall`, `fact_extractor`.

**Resolution order**, evaluated whenever a helper pool is built (`gateway/helpers/factory.py:_resolve_model_entry`):

1. If a `Router` is wired: `router.route_for(role)`. It reads `catalog.candidates_for_role(role)` (override-applied, §2.1) and scores every candidate that has bench data (§4.5). Highest composite score wins — regardless of the YAML default or manual override — tie broken by lower cost. If **no** candidate has bench data, falls back to `catalog.helper(role).model` (override if set, else YAML default). If `route_for` raises, fall through to step 2.
2. Else: `catalog.model(h_entry.model)` (override-adjusted YAML default).

A manual override only wins outright when nothing in its candidate pool has been benched yet; once bench data exists, the best scorer wins even if it isn't the override.

### 2.1 Manual runtime override (hot-swap, no restart)

- `PUT /v1/models/helpers/{role}` `{"model_id": "<id>"}` → `ModelCatalog.set_override`. Validates role/model (404/400 on failure), stores in-memory, persists to `<state_dir>/helper_overrides.json` via atomic write.
- `DELETE /v1/models/helpers/{role}` → `clear_override`, same persistence.
- Both routes call `rebuild_helper(catalog, role, ...)` and replace `app_state.helpers[role]` in place — next turn uses the new model. No restart, no effect on other roles.
- `helper(role)` applies an override by cloning the YAML entry with `model=override` and `candidates=(override,) + rest` — it does not remove the other candidates, so they can still outrank it once benched.
- `attach_overrides_file(path)` runs once at boot, before `build_helpers`; stale entries (role/model no longer in catalog) are dropped and logged, never applied.
- `GET /v1/models` returns each helper's `override` field (`null` if unset) — the full state needed to show "what model is this role using."

## 3. Catalog-vs-installed cross-check

`ModelCatalog.refresh_from_ollama()` (runs at boot, callable on demand):

1. `GET {OLLAMA_HOST}/api/tags` (env var, default `127.0.0.1:11434`), 10s timeout, via raw HTTP — not the `ollama list` CLI, because a wedged child process can hang a `subprocess.run(timeout=...)` join on Windows; an HTTP read-timeout always fires.
2. Build a `set[str]` of installed names from `models[].name` (fallback `.model`).
3. Cloud models (`cloud_provider` set): always marked available; real reachability is checked at call time via credentials (§4.5).
4. Local models: match `ollama_name` via `_ollama_name_present` — exact match, OR bare name matches `<name>:latest`, OR tag-suffix tolerance (installed name starts with the wanted name + `-`/`_`, so `qwen2.5:7b` matches `qwen2.5:7b-instruct-q4_K_M`).
5. Result stored per model id (`is_available(id)`), returned as `RefreshReport(available, missing)`. Missing entries log **WARN** with a literal `ollama pull <name>` remediation hint — never a silent substitute.

`GET /v1/models` → `{"models": [{...ModelEntry, "available": bool}], "helpers": [{...HelperEntry, "override": str|null}]}`.

## 4. Bench harness (candidate vs. incumbent, per role)

Under `gateway/orchestrator/`. This is the role-level quality/latency/cost comparator; `scripts/benchmark_models.py` is a separate coding-ladder tool (§4.6).

### 4.1 Corpus

`<corpus_dir>/<role>.jsonl` (default `config/bench_corpus/`, **not shipped by default** — author it per role first). One JSON object per line:

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | string | yes | Case id. |
| `prompt` | string | yes | Sent verbatim. |
| `expected_keywords` | list[string] | no (`[]`) | Case-insensitive judge substrings. |
| `max_tokens` | int | no (`256`) | Per-case output cap. |

`list_roles(corpus_dir)` = sorted `.jsonl` stems = roles benched in a full sweep.

### 4.2 Invocation

Per (role, candidate) pair, an `Invoker` is built:
- **Local**: `POST {host}/api/generate`, `{model, prompt, stream:false, options:{num_predict:max_tokens[, num_gpu:0]}}`. `num_gpu:0` forced when `gpu_vram_mb==0`, so latency matches the CPU path production actually uses.
- **Cloud** (`anthropic`): `AsyncAnthropic.messages.create`; skipped (logged) if `ANTHROPIC_API_KEY` unset or SDK missing.
- Any other provider, or a local entry missing `ollama_name`: skipped, logged, sweep continues.

`BenchInvocation = {output: str, token_count: int (output tokens), latency_ms: float}`.

### 4.3 Judge — deterministic, not LLM-as-judge

`quality_scorer.score_output(case, output) -> float[0,1]`:
- `expected_keywords` non-empty: `0.0` if zero substring hits; else `0.5 + 0.5 * (hits / len(keywords))`.
- Empty: length heuristic `min(1.0, len(output.strip()) / 200)`.

Deliberate cheap placeholder — module docstring notes it's swappable for an LLM judge later without changing the `Router` contract (only consumes a `0..1` float).

### 4.4 Scorecard: `bench_results.json`

```json
{"scores": {"<role>": {"<model_id>": {
  "latency_p50_ms": 0.0, "tokens_per_s": 0.0,
  "quality_score": 0.0, "cost_per_1k_tokens": 0.0,
  "last_run_at": 0.0
}}}}
```
- `latency_p50_ms` = median per-case latency. `tokens_per_s` = total output tokens / total wall-clock seconds. `quality_score` = mean per-case judge score. `cost_per_1k_tokens` = model's `cost_per_1k_tokens_output` (fixed, not measured).
- Written atomically; a fresh sweep **merges** into existing scores (never wipes unrelated roles/models).

Run: `python -m gateway.orchestrator.bench_harness --catalog config/model_catalog.yaml --corpus-dir config/bench_corpus --results state/bench_results.json [--role <role>] [--ollama-host URL]`. Iterates every role with a corpus file (or just `--role`) × every id in that role's `candidates` — the incumbent (`helpers[].model`) is scored side-by-side with every declared challenger, same cases, same formula.

### 4.5 Router: composite score

```
composite = 0.5 * quality_score
          + 0.3 * min(500.0 / max(latency_p50_ms, 1.0), 1.0)
          + 0.2 * (1.0 if cost<=0 else min(0.001 / cost, 1.0))
```
- Candidates = `catalog.candidates_for_role(role)` (override-aware). Cloud candidate with missing/empty provider API-key env var is dropped silently — never selected, never crashes routing.
- Only candidates **with a bench score for this role** compete; scoreless ones are skipped (not zero-scored). Winner = max composite; tie → lower `cost_per_1k_tokens`.
- Zero scored candidates → YAML/override default, `reason="no-bench: fallback to YAML default"`.
- `ModelChoice.reason` example: `"score=0.812 (q=0.90 lat=500ms cost=0.0008/1k)"` — logged by the factory for audit.

Boot order: catalog loads → overrides file attached → `refresh_from_ollama` → `Router` built from catalog + current `state/bench_results.json` → `build_helpers(..., router=router)` bakes the winner into each helper. Nothing re-benches automatically — scores only change when §4.4 is run.

### 4.6 Coding-capability ladder (`scripts/benchmark_models.py`)

Separate, coarser tool: sweeps Ollama tags through `scripts/spawn_tier_eval.py`'s fixed difficulty ladder (fizzbuzz → calculator → TODO list → card package → ... → event bus). Per tier: fresh project dir + fresh agent-loop run, then an independent `pytest -q`. Judge is deterministic/code-based: `tier_pass = (pytest rc==0 and passed>0 and failed==0 and asserts_audit.ok)`, where `asserts_audit` AST-walks every `test_*` function and fails the tier if any has zero `assert`/`pytest.raises` (blocks stub tests). Ladder stops at first tier failure. Output `results.json` keyed by model tag → `{tiers, highest_tier_cleared, total_dt_s, pull_ok, ps_before, ps_during}` — comparable across models by `highest_tier_cleared`, then `total_dt_s`.

## 5. Pull-approval gate

**Nothing in the live gateway process ever calls `ollama pull`.** Production only checks (§3) and refuses loudly — a helper whose model isn't installed is still constructed but every call returns `HelperResult(error=...)`, never a silent downgrade.

The **only** code path that downloads weights is `benchmark_models.py`'s `_ollama_pull(tag)`, run only via explicit human CLI invocation:

| Step | Actor | Effect |
|---|---|---|
| Trigger | human, foreground CLI | `python scripts/benchmark_models.py --models <tag1>,<tag2>,...` (or built-in default roster). No scheduler/webhook/agent path triggers this. |
| Scope | script | Pulls only tags named on the command line — never anything from `model_catalog.yaml`. |
| Pull | `ollama pull <tag>` subprocess | Idempotent; 1hr timeout; failure recorded to `results.json`, that model's ladder aborts, sweep continues. |
| Promotion to production | human | A pulled/benched tag affects **no** live role until a human edits `model_catalog.yaml` (`candidates:`/`model:`) and redeploys, or calls `PUT /v1/models/helpers/{role}` (§2.1). The router only ranks models already in `candidates:` — it never acquires new ones. |

## 6. Verifiable checkpoints

1. **Catalog loads and validates.** `pytest gateway/tests/test_model_catalog.py -q` — duplicate-id rejection, missing-`ollama_name`-without-cloud rejection, `refresh_from_ollama` marking.
2. **Router scoring + fallback rules hold.** `pytest gateway/orchestrator/tests/test_router.py gateway/orchestrator/tests/test_router_scoring.py -q` — no-bench fallback, higher-score wins, free-model tiebreak, unknown-role `KeyError`, partial-bench skip.
3. **A role sweep produces a real scorecard.** Create `config/bench_corpus/<role>.jsonl` with ≥1 case, run `python -m gateway.orchestrator.bench_harness --role <role>`, confirm `state/bench_results.json` has `scores.<role>.<model_id>.quality_score` for every candidate with working credentials/an installed tag.
4. **Runtime override hot-swaps without restart.** `curl -X PUT http://127.0.0.1:8766/v1/models/helpers/<role> -d '{"model_id":"<other_candidate>"}'`, then `GET /v1/models` — confirm `helpers[].override == "<other_candidate>"`, effective next turn, no restart.
5. **No autonomous pull path exists.** `grep -rn "ollama pull\|_ollama_pull" gateway/ scripts/` — only hit outside `scripts/benchmark_models.py` should be the remediation-hint log string in `gateway/model_catalog.py`; nothing in `gateway/` executes a pull.
