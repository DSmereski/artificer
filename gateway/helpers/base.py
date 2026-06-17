"""Helper protocol — shared shapes + base class for every M2.2 helper.

Each helper:
  - implements `async invoke(task) -> HelperResult`
  - never raises (errors land in HelperResult.error)
  - validates LLM output against a Pydantic schema
  - reports tokens + latency for the M6.3 telemetry layer

Helpers run **quarantined**: their system prompt deliberately does NOT
include user chat history or other helpers' outputs. Whatever the
HiveCoordinator passes in `task.inputs` is the only context.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal, Protocol

import httpx
from pydantic import BaseModel, ValidationError

log = logging.getLogger("gateway.helpers")


# ---------------------------------------------------------------- shapes


@dataclass(frozen=True)
class HelperTask:
    role: str
    goal: str                              # human-readable objective
    inputs: dict[str, Any]                 # everything the helper needs
    constraints: list[str] = field(default_factory=list)
    expected_schema: type[BaseModel] | None = None
    parent_id: str | None = None           # for tree-rendered events
    use_cpu: bool = False                  # M2.3 CPU fallback hint


@dataclass
class HelperResult:
    role: str
    model_id: str
    plan: list[str] = field(default_factory=list)
    output: dict[str, Any] = field(default_factory=dict)
    citations: list[str] = field(default_factory=list)
    confidence: Literal["low", "medium", "high"] = "medium"
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    error: str | None = None
    parent_id: str | None = None
    # Raw text the LLM emitted (pre-schema-validation). Captured even
    # on success so turn-logs can surface what the model actually said
    # — invaluable when troubleshooting JSON validation failures.
    raw_text: str = ""
    # True when _parse_fallback recovered a prose-only reply (schema
    # parsing failed but the raw text was kept as the reply). Lets the
    # turn-log distinguish "compose" from "prose-rescue" without
    # re-inspecting raw_text.
    prose_rescue: bool = False


class SchemaValidationError(Exception):
    """Raised internally when an LLM reply can't be parsed into the
    expected Pydantic shape. Caught by `Helper.invoke` and turned into
    a HelperResult with `error` set."""


# ---------------------------------------------------------------- helper protocol


class Helper(Protocol):
    role: str

    async def invoke(self, task: HelperTask) -> HelperResult:
        ...


# ---------------------------------------------------------------- ollama


_OLLAMA_BASE = "http://localhost:11434"


class OllamaInvoker:
    """Thin async client for Ollama's /api/chat endpoint.

    Decoupled from helpers so tests can swap a fake invoker.
    """

    def __init__(self, base_url: str = _OLLAMA_BASE, timeout: float = 240.0) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout

    async def chat(
        self,
        *,
        model: str,
        system: str,
        user: str,
        params: dict[str, Any] | None = None,
        use_cpu: bool = False,
        fmt: dict | None = None,
        tools: list[dict] | None = None,
    ) -> tuple[str, int, int]:
        """Returns (assistant_text, prompt_tokens, response_tokens).

        `fmt` is an optional JSON Schema passed to Ollama's structured-
        output `format` field. `tools` is an optional Ollama function-
        calling tool list — preferred for tool-calling models (qwen3.6),
        which otherwise ignore `format` and emit their native
        `tool_call(name, {...})` DSL (the cause of the ~40% parse-fail
        rate). When the model returns native `tool_calls`, the FIRST is
        serialised back into the `{"tool":..,"args":..}` JSON the caller
        already parses — so callers need no change.
        """
        body = {
            "model": model,
            "stream": False,
            # Disable qwen3-style thinking blocks at the API level —
            # without this, the model burns ~1500 chars (~30s on
            # CPU+RAM, ~5s on GPU) of <think>...</think> reasoning
            # before emitting JSON, blowing the planner timeout.
            "think": False,
            # Pin loaded models in VRAM for 24h. Without this Ollama
            # evicts after 5min idle, so the next helper turn pays a
            # 30-90s cold reload that blows planner/summarizer timeouts.
            "keep_alive": "24h",
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "options": dict(params or {}),
        }
        if use_cpu:
            # Ollama: setting num_gpu=0 forces CPU+RAM execution.
            body["options"]["num_gpu"] = 0
        if tools is not None:
            body["tools"] = tools
        elif fmt is not None:
            # Only use format when NOT using native tools (they conflict
            # for tool-calling models).
            body["format"] = fmt

        async with httpx.AsyncClient(timeout=self._timeout) as http:
            r = await http.post(f"{self._base}/api/chat", json=body)
            if r.status_code == 400 and ("format" in body or "tools" in body):
                # Server lacks structured-output / tools — retry plain.
                body.pop("format", None)
                body.pop("tools", None)
                r = await http.post(f"{self._base}/api/chat", json=body)
            r.raise_for_status()
            data = r.json()
        msg = data.get("message") or {}
        text = msg.get("content", "")
        # Native tool call → serialise to the {"tool","args"} JSON the
        # loop's _extract_json parses. Arguments may be a dict or a JSON
        # string depending on the model/runtime.
        tcs = msg.get("tool_calls") or []
        if tcs:
            fn = (tcs[0] or {}).get("function") or {}
            name = fn.get("name", "")
            args = fn.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except (ValueError, TypeError):
                    args = {}
            if name:
                text = json.dumps({"tool": name, "args": args})
        tokens_in = int(data.get("prompt_eval_count") or 0)
        tokens_out = int(data.get("eval_count") or 0)
        return text, tokens_in, tokens_out


# ---------------------------------------------------------------- output parsing


_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", re.DOTALL)
_LEADING_JSON = re.compile(r"^\s*(\{.*?\}|\[.*?\])", re.DOTALL)
# Qwen3 reasoning models prefix output with <think>...</think> blocks
# that aren't part of the JSON. Strip them defensively. Two regexes:
# the closed form removes a properly-balanced block; the open form is
# a fallback for cases where the model exhausted its budget mid-think
# (or `think: false` produced a stub <think> tag with no close), which
# happens consistently when the prompt body is several KB.
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_THINK_BLOCK_OPEN = re.compile(r"<think>.*?(?=\{|\[|$)", re.DOTALL | re.IGNORECASE)


def extract_json(text: str) -> Any:
    """Best-effort extraction of a JSON object/array from an LLM reply.

    Tolerates a fenced code block, prose preamble before the JSON, raw
    JSON, or a Qwen3 `<think>…</think>` reasoning block prefix. Raises
    SchemaValidationError if nothing parses.
    """
    if not text or not text.strip():
        raise SchemaValidationError("empty response")
    # Drop reasoning blocks before searching for JSON. Always strip
    # closed-form `<think>...</think>` blocks. Only fall back to the
    # open-form regex (eats `<think>` up to the first `{`/`[`) when
    # the closed pattern didn't fire — applying both unconditionally
    # over a reply with `<think>...</think>middle{...}` would also
    # eat `middle` between the closing think and the opening brace.
    closed_stripped = _THINK_BLOCK.sub("", text)
    if closed_stripped != text:
        text = closed_stripped
    elif "<think>" in text.lower():
        text = _THINK_BLOCK_OPEN.sub("", text)

    # 1. fenced ```json {...} ``` block (preferred)
    m = _JSON_FENCE.search(text)
    candidates: list[str] = []
    if m:
        candidates.append(m.group(1))
    # 2. raw leading JSON
    m2 = _LEADING_JSON.search(text)
    if m2:
        candidates.append(m2.group(1))
    # 3. balanced first { ... matching } walking depth (handles JSON
    # whose interior strings contain `}`, common when an LLM embeds
    # code inside a "body" string).
    bal = _balanced_object(text)
    if bal is not None:
        candidates.append(bal)
    # 4. last-resort: greedy first { ... last }
    first = text.find("{")
    last = text.rfind("}")
    if first != -1 and last > first:
        candidates.append(text[first:last + 1])

    for c in candidates:
        try:
            return json.loads(c)
        except json.JSONDecodeError:
            continue

    # Last-resort: try a small set of common LLM-JSON repairs on each
    # candidate before giving up. The LLM occasionally emits trailing
    # commas, single-quoted strings, or unescaped newlines inside
    # quoted bodies — all of which break strict json.loads.
    for c in candidates:
        repaired = _repair_llm_json(c)
        if repaired is None:
            continue
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            continue

    raise SchemaValidationError(f"no parseable JSON in reply: {text[:120]!r}")


def _repair_llm_json(s: str) -> str | None:
    """Best-effort repair of a JSON-shaped string from an LLM.

    Returns a candidate string with common mistakes fixed, or None if
    no repair was attempted. Caller still has to try json.loads —
    repair doesn't guarantee validity.
    """
    if not s:
        return None
    fixed = s
    # Drop trailing commas before `}` or `]`.
    fixed = re.sub(r",(\s*[}\]])", r"\1", fixed)
    # Replace unescaped real-newlines inside string literals with `\n`.
    out = []
    in_str = False
    esc = False
    for ch in fixed:
        if in_str:
            if esc:
                out.append(ch)
                esc = False
            elif ch == "\\":
                out.append(ch)
                esc = True
            elif ch == '"':
                out.append(ch)
                in_str = False
            elif ch == "\n":
                out.append("\\n")
            elif ch == "\r":
                out.append("\\r")
            elif ch == "\t":
                out.append("\\t")
            else:
                out.append(ch)
        else:
            out.append(ch)
            if ch == '"':
                in_str = True
    return "".join(out) if out else None


def _balanced_object(text: str) -> str | None:
    """Return the first balanced `{...}` substring, respecting strings.

    Walks the text counting braces, skipping anything inside double-
    quoted strings (with backslash escapes). Returns None if no
    balanced block is found.
    """
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def parse_with_schema(text: str, schema: type[BaseModel]) -> BaseModel:
    """Extract JSON from `text` and validate against `schema`."""
    try:
        obj = extract_json(text)
    except SchemaValidationError:
        raise
    try:
        return schema.model_validate(obj)
    except ValidationError as e:
        raise SchemaValidationError(f"schema validation failed: {e}") from e


# ---------------------------------------------------------------- result builder


@dataclass
class ResultBuilder:
    """Helper for concrete helpers — accumulates timing/tokens/output."""

    role: str
    model_id: str
    parent_id: str | None = None
    _start_ns: int = field(default_factory=lambda: time.monotonic_ns())
    plan: list[str] = field(default_factory=list)
    output: dict[str, Any] = field(default_factory=dict)
    citations: list[str] = field(default_factory=list)
    confidence: Literal["low", "medium", "high"] = "medium"
    tokens_in: int = 0
    tokens_out: int = 0
    error: str | None = None
    raw_text: str = ""
    prose_rescue: bool = False

    def add_tokens(self, prompt: int, response: int) -> None:
        self.tokens_in += prompt
        self.tokens_out += response

    def fail(self, msg: str) -> "ResultBuilder":
        self.error = msg
        return self

    def build(self) -> HelperResult:
        latency_ms = (time.monotonic_ns() - self._start_ns) // 1_000_000
        return HelperResult(
            role=self.role,
            model_id=self.model_id,
            plan=self.plan,
            output=self.output,
            citations=self.citations,
            confidence=self.confidence,
            tokens_in=self.tokens_in,
            tokens_out=self.tokens_out,
            latency_ms=latency_ms,
            error=self.error,
            parent_id=self.parent_id,
            raw_text=self.raw_text,
            prose_rescue=self.prose_rescue,
        )


# ---------------------------------------------------------------- prompt loader


_PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"


def load_prompt(name: str) -> str:
    """Load a prompt file from the project's prompts/ directory.

    Accepts either a bare name (`planner`) or a relative path
    (`prompts/planner.md`).
    """
    if name.startswith("prompts/"):
        path = _PROMPTS_DIR.parent / name
    else:
        path = _PROMPTS_DIR / f"{name}.md"
    if not path.is_file():
        raise FileNotFoundError(f"prompt not found: {path}")
    return path.read_text(encoding="utf-8")


@lru_cache(maxsize=None)
def prompt_version(name: str) -> str:
    """Short content-derived version tag for a prompt file.

    Returns the first 12 hex chars of the prompt's SHA-256. The tag
    changes automatically whenever the prompt text changes, so turn
    telemetry can attribute behaviour to a specific prompt revision and
    A/B comparisons across edits become possible — no manual version
    bumping. Cached for the process lifetime (prompts don't change
    mid-run). Returns "missing" when the file is absent.
    """
    try:
        text = load_prompt(name)
    except FileNotFoundError:
        return "missing"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------- base helper


class BaseHelper:
    """Concrete helpers inherit from this. Provides the boilerplate
    invocation pattern: load prompt, call Ollama, parse JSON, build
    a HelperResult — never raises."""

    role: str = "base"

    def __init__(
        self,
        *,
        model_id: str,
        ollama_name: str,
        prompt_name: str,
        params: dict[str, Any],
        invoker: OllamaInvoker | None = None,
        timeout_s: int = 30,
        schema: type[BaseModel] | None = None,
    ) -> None:
        self.model_id = model_id
        self.ollama_name = ollama_name
        self.prompt_name = prompt_name
        self.params = params
        self.invoker = invoker or OllamaInvoker()
        self.timeout_s = timeout_s
        self.schema = schema

    def _build_user_message(self, task: HelperTask) -> str:
        """Format the task as a JSON-shaped user message. Helpers can
        override for richer formatting, but keep inputs quarantined —
        no chat history, no other helpers' outputs."""
        return json.dumps({
            "goal": task.goal,
            "inputs": task.inputs,
            "constraints": task.constraints,
        }, indent=2, default=str)

    async def invoke(self, task: HelperTask) -> HelperResult:
        rb = ResultBuilder(
            role=self.role, model_id=self.model_id,
            parent_id=task.parent_id,
        )
        try:
            system = load_prompt(self.prompt_name)
        except FileNotFoundError as e:
            return rb.fail(str(e)).build()

        user = self._build_user_message(task)
        try:
            text, t_in, t_out = await asyncio.wait_for(
                self.invoker.chat(
                    model=self.ollama_name,
                    system=system, user=user,
                    params=self.params, use_cpu=task.use_cpu,
                ),
                timeout=self.timeout_s,
            )
        except asyncio.TimeoutError:
            return rb.fail(f"helper {self.role} timed out after {self.timeout_s}s").build()
        except httpx.HTTPError as e:
            return rb.fail(f"ollama call failed: {e}").build()
        except Exception as e:  # noqa: BLE001
            log.exception("helper %s unexpected error", self.role)
            return rb.fail(f"unexpected: {type(e).__name__}: {e}").build()

        rb.add_tokens(t_in, t_out)
        rb.raw_text = text          # captured for turn-log debugging

        # Parse + validate output if a schema was supplied.
        if self.schema is not None:
            try:
                parsed = parse_with_schema(text, self.schema)
                rb.output = parsed.model_dump()
            except SchemaValidationError as e:
                fallback = self._parse_fallback(text, e)
                if fallback is None:
                    return rb.fail(str(e)).build()
                try:
                    parsed = self.schema.model_validate(fallback)
                    rb.output = parsed.model_dump()
                    rb.prose_rescue = True
                except ValidationError as ve:
                    return rb.fail(
                        f"fallback failed schema: {ve}"
                    ).build()
        else:
            rb.output = {"text": text}

        # Helper subclasses may extract `plan` / `citations` /
        # `confidence` from the parsed output by overriding _post_parse.
        try:
            self._post_parse(task, rb)
        except Exception as e:  # noqa: BLE001
            log.exception("helper %s post-parse failed", self.role)
            return rb.fail(f"post-parse: {e}").build()
        return rb.build()

    def _parse_fallback(
        self, text: str, error: SchemaValidationError,
    ) -> dict[str, Any] | None:
        """Last-resort recovery hook when schema parsing fails.

        Default: returns None (no recovery — caller surfaces the error).
        Subclasses may return a dict that will be validated against
        `self.schema`. The synthesizer uses this to wrap prose-only LLM
        output as a valid SynthesisPlan rather than emitting a
        meaningless 'helper outputs below' fallback.
        """
        return None

    def _post_parse(self, task: HelperTask, rb: ResultBuilder) -> None:
        """Extract `plan` / `citations` / `confidence` from rb.output.

        Default: pull common keys if they exist. Subclasses override
        for role-specific shaping.
        """
        out = rb.output
        plan = out.get("plan")
        if isinstance(plan, list):
            rb.plan = [str(s) for s in plan]
        cites = out.get("citations")
        if isinstance(cites, list):
            rb.citations = [str(c) for c in cites]
        conf = out.get("confidence")
        if isinstance(conf, str) and conf in ("low", "medium", "high"):
            rb.confidence = conf  # type: ignore[assignment]
