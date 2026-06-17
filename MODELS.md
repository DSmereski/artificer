# Models

Hive is **local-first**: by default every helper runs on your own GPU via
[Ollama](https://ollama.com) with open-weight models. The model catalog lives in
[`config/model_catalog.yaml`](config/model_catalog.yaml) — each helper role maps
to a model, and you can swap any role at runtime. A cloud tier (Anthropic /
OpenAI) is optional.

Multi-GPU is handled by Ollama (layer-split via `CUDA_VISIBLE_DEVICES`). NVLink
is **not** used or required.

## Shipped defaults (Ollama, `ollama pull` them)

| Catalog id | Ollama model | Maker | Source | License | VRAM |
|---|---|---|---|---|---|
| `planner-qwen` (default helper) | `qwen2.5-coder:7b` | Alibaba Qwen | [ollama.com/library/qwen2.5-coder](https://ollama.com/library/qwen2.5-coder) | Apache-2.0 | ~5 GB |
| `qwen3-8b` (fast planner) | `qwen3:8b` | Alibaba Qwen | [ollama.com/library/qwen3](https://ollama.com/library/qwen3) | Apache-2.0 | ~5 GB |
| `gemma3-4b` (CPU researcher) | `gemma3:4b` | Google | [ollama.com/library/gemma3](https://ollama.com/library/gemma3) | Gemma Terms | CPU/~3 GB |
| `nomic-embed` (embeddings) | `nomic-embed-text` | Nomic AI | [ollama.com/library/nomic-embed-text](https://ollama.com/library/nomic-embed-text) | Apache-2.0 | CPU |

## Optional cloud tier (set a key in `config/.env`)

| Catalog id | Provider | Source |
|---|---|---|
| `claude-haiku-*` / `claude-opus-*` | Anthropic | [anthropic.com](https://www.anthropic.com) |
| (add your own) | OpenAI | [openai.com](https://openai.com) |

## Bigger local options (opt-in — add to the catalog by VRAM)

| Model | Maker | Source | License | VRAM |
|---|---|---|---|---|
| `qwen3-coder:30b` | Alibaba Qwen | [ollama.com/library/qwen3-coder](https://ollama.com/library/qwen3-coder) | Apache-2.0 | ~18 GB |
| `deepseek-r1:32b` | DeepSeek | [ollama.com/library/deepseek-r1](https://ollama.com/library/deepseek-r1) | MIT | ~19 GB |
| `devstral-small:24b` | Mistral | [ollama.com/library/devstral](https://ollama.com/library/devstral) | Apache-2.0 | ~15 GB |
| `gpt-oss:20b` / `:120b` | OpenAI | [ollama.com/library/gpt-oss](https://ollama.com/library/gpt-oss) | Apache-2.0 | 13 / 65 GB |
| `phi4:14b` | Microsoft | [ollama.com/library/phi4](https://ollama.com/library/phi4) | MIT | ~9 GB |

> ⚠️ Some popular models have restrictive licenses — e.g. **Codestral** (Mistral
> MNPL, non-commercial) and **Gemma** (gated Google terms). Hive's shipped
> defaults are Apache/MIT; add the restricted ones yourself only if their terms
> work for you.

## Swapping a model

Per role at runtime, or edit `config/model_catalog.yaml`:

```yaml
helpers:
  - role: coder
    model: planner-qwen
    candidates: [planner-qwen, qwen3-coder]   # router picks among these
```

The catalog refreshes against `ollama list` at startup and **fails loudly** if a
configured model isn't pulled — it never silently downgrades.
