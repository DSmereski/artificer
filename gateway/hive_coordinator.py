"""HiveCoordinator — drives every Terry turn through the hive of helpers.

Includes a programmatic hallucination guard run after synthesis: any
sentence in the reply whose numeric facts (e.g. "1,200 m/s", "46 SCU",
"$110") aren't present in any helper output gets dropped. Catches the
common "qwen-3 9B fills missing-vault gaps with training-data
specifics" failure mode without needing a bigger model.

Pipeline:
  1. Plan (Planner helper)             → emit `thought` event
  2. Dispatch delegations              → emit `delegate` + `helper_reply` per
  3. Critic-gate any risky action      → may BLOCK the synthesis
  4. Synthesize                        → emit `synthesis` event
  5. Execute side-effect actions       → vault writes, image renders, ntfy
  6. Final reply                       → emit `assistant` event

Cross-cutting:
  - Per-turn budget (max helpers, total timeout, total tokens)
  - VRAM-aware scheduling: GPU first, fall back to CPU+RAM via use_cpu
    when VRAM would overflow (never downgrades quality)
  - Cancellation: if the WS disconnects, all in-flight helpers cancel
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from gateway.action_executor import ActionExecutor, ActionReceipt
from gateway.event_emitter import EventEmitter
from gateway.hallucination_guard import (
    enforce_empty_retrieval_reply,
    enforce_groundedness_with_hits,
    strip_hallucinated_sentences,
)
from gateway.helpers.base import Helper, HelperResult, HelperTask
from gateway.helpers.relevance_gate import filter_irrelevant
from gateway.model_catalog import ModelCatalog
from gateway.orchestrator.router import Router
from gateway.prompt_safety import sanitise_helper_outputs

log = logging.getLogger("gateway.hive")


# ---------------------------------------------------------------- budget


@dataclass
class TurnBudget:
    """Per-turn caps. Coordinator enforces these defensively."""
    max_concurrent_helpers: int = 5
    # When the operator's gaming GPU (index 0) is in use by a known game
    # process, fall back to this lower cap so the helpers running on the
    # remaining cards don't compete for VRAM/PCIe bandwidth.
    # The actual game-detection lives in services.scout_daemon; this
    # coordinator only needs the boolean.
    gaming_concurrent_helpers: int = 3
    # 360s total — under long-conversation load (scenario 10,
    # 2026-05-01) librarian (150s) + planner (180s) serialise on
    # planner-qwen with NUM_PARALLEL=1, plus synth_reservation_s=60.
    # 240s clipped both helpers; 360s leaves headroom.
    total_timeout_s: float = 360.0
    max_total_tokens_out: int = 8000
    # Reserve at the END of the turn for synthesizer (+ critic). This
    # guarantees that even if dispatch runs long, we have time to
    # compose Terry's reply. 130s = synth catalog timeout (120s) +
    # ~10s slack — raised from 60s after observing synth timing out
    # at 45s under long-conversation load (scenario 10, 2026-05-02).
    synth_reservation_s: float = 130.0
    # VRAM budget: when summed gpu_vram_mb across in-flight helpers
    # exceeds this, the next helper falls back to CPU+RAM.
    vram_budget_mb: int = 14000
    # Don't push a helper to CPU+RAM if free system RAM would drop
    # below this floor. None disables the check (psutil is optional).
    min_free_ram_mb: int | None = 4_000
    # VRAM-aware adaptive parallelism (T10).
    # When set, live_max_concurrent() queries this callable for free VRAM
    # (in MB) and tightens max_concurrent_helpers accordingly.
    # None (the default) preserves current behaviour: static cap.
    vram_provider: Callable[[], int] | None = None
    # Estimated VRAM cost (MB) per in-flight helper. Used only when
    # vram_provider is set.
    helper_vram_estimate_mb: int = 4000
    # Synth-on-ready gate (#476 Phase B + #484 hard-cancel). Once dispatch
    # starts, wait at most this many seconds for ALL helpers; whoever isn't
    # done by then is HARD-CANCELLED so synth gets the Ollama slot to
    # itself. Earlier (#476 B.5) we detached stragglers as background
    # tasks, but on a single-Ollama-slot rig (planner-qwen NUM_PARALLEL=1)
    # detached helpers kept running on GPU and blocked synth — scn04
    # 2026-05-06 saw 74% synth-timeout because of this contention.
    # Cancel propagates through `asyncio.wait_for` into httpx, which
    # tears down the Ollama HTTP request and frees the slot.
    synth_gate_s: float = 30.0

    def live_max_concurrent(self) -> int:
        """Live cap, optionally tightened by free VRAM via vram_provider.

        When vram_provider is None, returns max_concurrent_helpers.
        Otherwise: min(max_concurrent_helpers, max(1, free // helper_vram_estimate_mb)).
        """
        if self.vram_provider is None:
            return self.max_concurrent_helpers
        free = self.vram_provider()
        cap_by_vram = max(1, free // self.helper_vram_estimate_mb)
        return min(self.max_concurrent_helpers, cap_by_vram)


def _gaming_on_gpu0() -> bool:
    """Best-effort: is a known game process holding GPU 0 right now?

    Wraps `services.scout_daemon.gpu_monitor.detect_game_on_gpu(0)` so
    callers can stay free of scout_daemon imports. Any failure (module
    missing in tests, nvidia-smi unreachable, GPU 0 not present) is
    treated as "not gaming" — the safe default is the full cap.
    """
    try:
        from services.scout_daemon.gpu_monitor import detect_game_on_gpu
    except Exception:
        return False
    try:
        return bool(detect_game_on_gpu(0))
    except Exception:
        return False


def _free_system_ram_mb() -> int | None:
    """Available RAM in MB, or None if psutil isn't installed."""
    try:
        import psutil  # type: ignore
        return int(psutil.virtual_memory().available / (1024 * 1024))
    except Exception:
        return None


@dataclass
class TurnContext:
    """Everything the planner sees about the current turn."""
    user_msg: str
    user_id: int
    device_id: str
    bot: str = "terry"
    history_digest: str = ""
    image_build: dict | None = None
    skills_digest: str = ""
    # Skills whose trigger phrases match `user_msg` — planner can use
    # this as a hint to pick `skill_runner` directly without re-deriving
    # the workflow from scratch.
    suggested_skills: list[str] = field(default_factory=list)
    available_helpers: list[str] = field(default_factory=list)
    device_audience: list[str] | None = None
    # Phase 2: per-thread isolation. "default" preserves the pre-P2
    # single-thread behaviour for clients that haven't been updated.
    thread_id: str = "default"
    # Connected-brain: top-k vault snippets retrieved for the user's
    # message so EVERY turn is grounded without requiring the planner to
    # explicitly delegate to `researcher`. Injected by build_turn_context;
    # rendered into the planner task as fenced reference data. Empty list
    # means vault search was skipped, unavailable, or timed out — not an
    # error condition.
    vault_snippets: list[str] = field(default_factory=list)


@dataclass
class AssistantTurn:
    reply: str
    actions: list[dict] = field(default_factory=list)
    receipts: list[dict] = field(default_factory=list)
    helpers_used: list[str] = field(default_factory=list)
    total_tokens: int = 0
    total_latency_ms: int = 0
    blocked: bool = False
    error: str | None = None
    # Filled when present so `_hive_turn` can persist a debug log.
    turn_id: str = ""
    planner_result: Any = None        # HelperResult | None
    helper_results: list = field(default_factory=list)   # list[HelperResult]
    critic_result: Any = None         # HelperResult | None
    synth_result: Any = None          # HelperResult | None
    # Explicit synth_mode override. When None, hive_turn_helpers derives
    # the mode from synth_result. Set explicitly for known bypass paths
    # (direct_reply, critic-block) so the turn-log is unambiguous.
    synth_mode: str | None = None


# ---------------------------------------------------------------- write-intent detector

# Phrases that signal the user wants a side-effecting action (vault
# write, image render, vault forget, ...). When any of these appear,
# direct_reply is unsafe — the synthesizer is the only path that emits
# actions. Kept as a simple regex (no LLM) so detection is deterministic
# and cheap. Conservative: false positives cost one synth round-trip;
# false negatives cost a lost user write.
_WRITE_INTENT_RE = re.compile(
    r"\b(?:"
    r"save\s+(?:this|that|it|a\s+note|a\s+top[\s-]level\s+note|"
    r"the\s+answer|to\s+(?:my\s+)?vault|['\"`])"
    r"|saving\s+a\s+(?:top[\s-]level\s+)?note"
    r"|remember\s+(?:this|that|the\s+codeword)"
    r"|note\s+that\b"
    r"|add\s+(?:this|that|it)\s+to\s+(?:the\s+|my\s+)?vault"
    r"|store\s+(?:this|that|it)\s+(?:in|to)\s+(?:the\s+|my\s+)?vault"
    r"|write\s+(?:a\s+note|this\s+to|that\s+to)"
    r"|forget\s+(?:that|this|it|the(?:\s+\w+){0,4}\s+note|about|"
    r"the\s+\w+\s+(?:note|entry|memo))"
    r"|delete\s+(?:that|this|it|the(?:\s+\w+){0,4}\s+note|the\s+\w+\s+entry|"
    r"all\s+notes)"
    r"|remove\s+(?:that|this|it|the(?:\s+\w+){0,4}\s+note)"
    r"|correct\s+(?:that|the\s+note|the\s+\w+\s+(?:note|entry))"
    r"|update\s+(?:the\s+note|the\s+\w+\s+(?:note|entry))"
    r"|add\s+to\s+the\s+\w+\s+note"
    r"|render\s+(?:an?\s+|the\s+|some\s+|\d{1,2}\s+)?"
    r"(?:image|portrait|widescreen|landscape|picture|art|illustration)"
    r"|generate\s+(?:an?\s+|the\s+|some\s+|\d{1,2}\s+)?"
    r"(?:image|portrait|widescreen|landscape|picture|art|illustration)"
    r"|make\s+(?:\d{1,2}\s+|an?\s+|some\s+)?"
    r"(?:image|images|portrait|portraits|picture|pictures)"
    r"|draw\s+(?:an?\s+|some\s+)?(?:image|picture|portrait)"
    r"|push\s+(?:an?\s+)?ntfy"
    r"|create\s+(?:a\s+)?skill"
    r")\b",
    re.IGNORECASE,
)


def _looks_like_write_intent(user_msg: str) -> bool:
    if not user_msg:
        return False
    return bool(_WRITE_INTENT_RE.search(user_msg))


# Deterministic Save-as-note parser. The SC knowledge-base run (2026-06-04)
# showed planner-qwen producing prose like "Saved as `locations-lorville.md`"
# with empty `actions` 33/33 turns — even after the write-intent override
# forced the synth path. The LLM ignores the synth-prompt instruction to
# emit a vault_learn JSON envelope. Server-side fallback: when the user
# message matches a clear "Save 'TITLE' — BODY" / 'Save "TITLE" covering
# BODY' shape AND synth emitted no vault_learn, synthesize the action
# from the message itself. Title and body are taken verbatim so we
# preserve the user's exact phrasing.
#
# False-positive risk is low because the regex requires a quoted span
# AND a separator (em-dash/hyphen/covering/about/colon) AND a body of
# at least 20 chars — chatty messages without a clear save pattern
# don't trigger. False negatives only mean the synth's own action (if
# any) wins, which is fine.
_FORGET_PARSE_RE = re.compile(
    r"\b(?:forget|delete|remove)\b"
    r".{0,80}?"
    r"(?:'([^'\n]{3,200})'|\"([^\"\n]{3,200})\"|`([^`\n]{3,200})`"
    # "the note about X" / "the X note" — capture X out of either shape.
    r"|(?:the\s+)?note\s+(?:about|on|regarding|for|concerning)\s+"
    r"(?:the\s+)?([\w'-]+(?:\s+[\w'-]+){0,5}?)(?:\s*[.,;]|\s*$)"
    r"|(?:the\s+)?([A-Z][\w'-]+(?:\s+[\w'-]+){0,5}|"
    r"[a-z][\w'-]*(?:\s+[\w'-]+){1,5})\s+(?:note|entry|memo))",
    re.IGNORECASE | re.DOTALL,
)

_UPDATE_PARSE_RE = re.compile(
    r"\b(?:update|correct|edit|fix|change|amend|append\s+to|add\s+to)\b"
    r"\s+(?:the\s+)?"
    r"(?:'([^'\n]{3,200})'|\"([^\"\n]{3,200})\"|`([^`\n]{3,200})`"
    r"|([\w-]+(?:\s+[\w-]+){0,5}))"
    r"\s+(?:note|entry)"
    r"[\s\S]*?[—–:-]+\s*"
    r"(.{20,})",
    re.IGNORECASE | re.DOTALL,
)


_SAVE_NOTE_PARSE_RE = re.compile(
    # Same-style quote pairs so "Faction — Xi'an" (apostrophe inside
    # double quotes) parses cleanly. Earlier `[^'\"`]` rejected any
    # quote char inside the title, which dropped legitimate user
    # phrasings that name a Xi'an / O'Connor / Rosa's etc.
    r"(?:'([^'\n]{3,200})'|\"([^\"\n]{3,200})\"|`([^`\n]{3,200})`)"
    r"\s*(?:[—–-]+|covering|about|:)\s*"
    r"(.{20,})",
    re.IGNORECASE | re.DOTALL,
)

# Known Star Citizen entities — used to auto-tag "star-citizen" when
# the user message clearly sits in that domain. Conservative list: only
# nouns/proper nouns specific to the game.
_SC_TAG_HINTS = re.compile(
    r"\b(?:star\s+citizen|stanton|pyro|hurston|crusader|arccorp|"
    r"microtech|lorville|orison|area18|new\s+babbage|grimhex|uee|"
    r"squadron\s+42|vanduul|banu|xi['']?an|aegis|anvil|drake|"
    r"origin\s+jumpworks|misc|rsi|cutlass|carrack|reclaimer|"
    r"polaris|idris|hammerhead|kraken|nine\s+tails|messer|imperator)\b",
    re.IGNORECASE,
)


def _derive_save_action_from_user(
    user_msg: str, existing_actions: list[dict],
) -> dict | None:
    """Parse "Save 'TITLE' — BODY" out of the user message and return a
    `vault_learn` action dict. Returns None if no clear pattern matches
    or if the synth already emitted a vault_learn (don't double-write).
    """
    if not user_msg or not _looks_like_write_intent(user_msg):
        return None
    # Skip only if synth emitted a *complete* vault_learn (has both
    # title and body). Synth-emitted stubs that lack required fields
    # would silently fail in the executor's "missing category/title/
    # body" gate, so the user's save would be lost. Derive in that case
    # so the action_executor's dedup catches any genuine duplicate.
    for a in existing_actions:
        if not isinstance(a, dict) or a.get("verb") != "vault_learn":
            continue
        p = a.get("payload") or {}
        if not isinstance(p, dict):
            continue
        title = str(p.get("title") or "").strip()
        body = str(p.get("body") or p.get("body_md") or "").strip()
        if title and body:
            return None
    m = _SAVE_NOTE_PARSE_RE.search(user_msg)
    if not m:
        return None
    # The regex has three alternative title groups (one per quote style)
    # plus a body group as the last one. Pick whichever title matched.
    title = (m.group(1) or m.group(2) or m.group(3) or "").strip()
    body = m.group(m.lastindex).strip() if m.lastindex else ""
    # Trim a trailing instruction like "Save it under category knowledge."
    # so the body itself doesn't contain meta-instructions.
    body = re.sub(
        r"\s*(?:save\s+it\s+under\s+category\s+\w+|"
        r"save\s+under\s+category\s+\w+|"
        r"save\s+(?:it|that)\s+to\s+(?:my\s+)?vault)\.?\s*$",
        "",
        body,
        flags=re.IGNORECASE,
    ).strip()
    if len(body) < 20 or len(title) < 3:
        return None
    # Prepend the title when the body is short, so the user's bare-fact
    # phrasing ("luxury, ships include 300-series…") passes the vault
    # quality gate (MIN_BODY_CHARS=80) without losing the user's intent.
    # The title is already in frontmatter; including it in the body too
    # is harmless for retrieval and reads naturally ("Title — body").
    # Skip only when the body already starts with the title (avoids
    # "ArcCorp — ArcCorp is a planet…" duplication).
    if len(body) < 100 and not body.lower().startswith(title.lower()):
        body = f"{title} — {body}"
    tags: list[str] = []
    if _SC_TAG_HINTS.search(user_msg) or _SC_TAG_HINTS.search(title):
        tags.append("star-citizen")
    payload: dict[str, Any] = {
        "category": "knowledge",
        "title": title,
        "body": body,
    }
    if tags:
        payload["tags"] = tags
    # Mark server-derived so the executor's quality gate can wave
    # this through. The user's literal phrasing IS the authority —
    # we don't need an LLM-stub heuristic second-guessing it.
    payload["_server_derived"] = True
    return {"verb": "vault_learn", "payload": payload}


# Recall intent: "what was/is the X", "tell me the X", "recall X",
# "who is/was X". Capture group is the target term. Conservative —
# the search results are quoted verbatim, so a false positive only
# returns a vault snippet that may or may not be relevant.
_RECALL_INTENT_RE = re.compile(
    r"\b(?:"
    r"what\s+(?:was|is|are|were)\s+(?:the\s+|my\s+|our\s+)?(.+?)\??$"
    r"|tell\s+me\s+(?:the\s+|about\s+|what\s+)?(.+?)\??$"
    r"|recall\s+(?:the\s+|my\s+)?(.+?)\??$"
    r"|who(?:'s|\s+(?:is|was|leads|owns))\s+(.+?)\??$"
    r"|how\s+many\s+(.+?)\??$"
    r"|what'?s\s+(?:the\s+)?(.+?)\??$"
    r")",
    re.IGNORECASE | re.DOTALL,
)


_IMAGE_INTENT_RE = re.compile(
    r"\b(?:"
    r"(?:generate|render|make|create|draw|produce)\s+"
    r"(?:(\d{1,2})\s+)?"
    r"(?:an?\s+|some\s+)?"
    r"(?:portrait|widescreen|square|landscape|tall|wide|"
    r"image|images|picture|pictures|render|art|illustration)"
    r")\b"
    r"\s+(?:of\s+|for\s+|showing\s+|depicting\s+)?"
    r"(.{10,})",
    re.IGNORECASE | re.DOTALL,
)
_ASPECT_HINT_RE = re.compile(
    r"\b(portrait|widescreen|landscape|square|tall|wide)\b",
    re.IGNORECASE,
)


def _derive_image_action_from_user(
    user_msg: str, existing_actions: list[dict],
) -> dict | None:
    """Parse "generate/render N images of X" → image_render action.

    Bypasses the LLM image_director chain when the user's request is
    unambiguous. Returns None when there's no image intent, body is
    too short, or an image_render is already present in existing
    actions (don't double-fire when synth got it right).
    """
    if not user_msg:
        return None
    for a in existing_actions:
        if isinstance(a, dict) and a.get("verb") == "image_render":
            return None
    m = _IMAGE_INTENT_RE.search(user_msg)
    if not m:
        return None
    count_raw = m.group(1)
    prompt = (m.group(2) or "").strip().rstrip(".!?")
    if len(prompt) < 10:
        return None
    try:
        count = int(count_raw) if count_raw else 1
    except ValueError:
        count = 1
    count = max(1, min(8, count))  # clamp to a sane range
    # Aspect from explicit word in user msg, else default portrait.
    aspect_m = _ASPECT_HINT_RE.search(user_msg)
    aspect = "portrait"
    if aspect_m:
        word = aspect_m.group(1).lower()
        if word in ("widescreen", "landscape", "wide"):
            aspect = "landscape"
        elif word == "square":
            aspect = "square"
        elif word in ("portrait", "tall"):
            aspect = "portrait"
    # Negative prompt: capture "no <X>" or "without <X>" trailing hints.
    neg_m = re.search(
        r"\b(?:no|without)\s+([\w\s,]{2,80}?)(?:\s+visible|[.!?]|$)",
        user_msg, re.IGNORECASE,
    )
    payload: dict[str, Any] = {
        "prompt": prompt,
        "count": count,
        "aspect": aspect,
    }
    if neg_m:
        payload["negative_prompt"] = neg_m.group(1).strip()
    payload["_server_derived"] = True
    return {"verb": "image_render", "payload": payload}


def _derive_forget_action_from_user(
    user_msg: str, existing_actions: list[dict],
) -> dict | None:
    """Parse "delete/forget/remove the X note" out of user_msg → vault_forget.

    Falls back to a query-based forget so the executor matches by title
    substring (case-insensitive). Returns None when there's already a
    vault_forget in existing_actions or the message doesn't show a
    forget intent + extractable target.
    """
    if not user_msg:
        return None
    for a in existing_actions:
        if isinstance(a, dict) and a.get("verb") == "vault_forget":
            return None
    m = _FORGET_PARSE_RE.search(user_msg)
    if not m:
        return None
    # Pick whichever capture group matched (quoted or bare).
    target = next((g for g in m.groups() if g), "").strip()
    if len(target) < 3:
        return None
    # Drop "note" / "entry" suffix words that sometimes get captured
    # when the bare-token branch matches.
    target = re.sub(
        r"\s+(?:note|entry|memo)$", "", target, flags=re.IGNORECASE,
    ).strip()
    if len(target) < 3:
        return None
    return {"verb": "vault_forget", "payload": {"query": target}}


def _derive_update_action_from_user(
    user_msg: str, existing_actions: list[dict],
) -> dict | None:
    """Parse "update/correct the X note — Y" → vault_learn with title=X,
    body=Y. The executor's dedup will merge into the existing note when
    Jaccard ≥ 0.55, so this is the natural way to land a correction
    without inventing a new vault verb. Returns None when there's no
    update intent or the body is too short."""
    if not user_msg:
        return None
    # Skip if synth already emitted a complete vault_learn.
    for a in existing_actions:
        if not isinstance(a, dict) or a.get("verb") != "vault_learn":
            continue
        p = a.get("payload") or {}
        if isinstance(p, dict) and str(p.get("title") or "").strip() and (
            str(p.get("body") or p.get("body_md") or "").strip()
        ):
            return None
    m = _UPDATE_PARSE_RE.search(user_msg)
    if not m:
        return None
    title = next((g for g in m.groups()[:4] if g), "").strip()
    body = (m.group(5) or "").strip() if m.lastindex and m.lastindex >= 5 else ""
    if len(title) < 3 or len(body) < 20:
        return None
    # Match the executor's "missing category/title/body" gate by
    # filling category to "knowledge" — same default the save derive
    # uses. The dedup step inside _vault_learn will merge into the
    # existing same-title note instead of creating a duplicate.
    if len(body) < 100 and not body.lower().startswith(title.lower()):
        body = f"{title} — {body}"
    payload: dict[str, Any] = {
        "category": "knowledge",
        "title": title,
        "body": body,
    }
    if _SC_TAG_HINTS.search(user_msg) or _SC_TAG_HINTS.search(title):
        payload["tags"] = ["star-citizen"]
    payload["_server_derived"] = True
    return {"verb": "vault_learn", "payload": payload}


# ---------------------------------------------------------------- coordinator


class HiveCoordinator:
    def __init__(
        self,
        catalog: ModelCatalog,
        helpers: dict[str, Helper],
        budget: TurnBudget | None = None,
        executor: ActionExecutor | None = None,
        router: Router | None = None,
    ) -> None:
        self.catalog = catalog
        self.helpers = helpers
        self.budget = budget or TurnBudget()
        self.executor = executor
        self.router = router
        self._in_flight_vram = 0
        self._vram_lock = asyncio.Lock()
        # Vestigial since #484: helpers are now hard-cancelled at the
        # synth-gate (see `_dispatch`). Set retained as an always-empty
        # attribute so callers (app.py shutdown, tests) keep compiling
        # without changes.
        self._late_helper_tasks: set[asyncio.Task] = set()

    async def _maybe_recall_from_vault(
        self, ctx: TurnContext,
    ) -> str | None:
        """LLM-bypass recall path. Detects "what was X" / "tell me X" /
        "who is X" questions, searches the vault for X, and returns a
        grounded one-paragraph reply. Returns None when there's no
        recall intent or no vault hit.

        Best-effort and never raises.
        """
        if not ctx.user_msg:
            return None
        m = _RECALL_INTENT_RE.search(ctx.user_msg)
        if not m:
            return None
        # Capture group: the question target. Falls back to the whole
        # user message minus question words when the regex's bounded
        # capture is too narrow.
        query = (m.group(1) or "").strip(" .,!?:;'\"`")
        if len(query) < 2:
            return None
        # Pull vault client off the executor (action_executor wires it
        # up with vault_path + daemon host/port).
        if self.executor is None:
            return None
        vc_factory = getattr(self.executor, "_vault_client_factory", None)
        if vc_factory is None:
            return None
        try:
            from shared.embeddings import embed_text
            vec = await embed_text(
                text=query,
                ollama_url="http://127.0.0.1:11434",
                model="nomic-embed-text",
            )
            client = vc_factory()
            results = client.search(
                query_embedding=vec, k=3, audience="terry",
                query_text=query,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("recall fallback vault search failed: %s", e)
            return None
        if not results:
            return None
        # Compose a clean grounded reply. Body of the top hit is the
        # most direct answer.
        top = results[0]
        body = (getattr(top, "body", "") or "").strip()
        if not body:
            return None
        # Clamp body length so the answer stays scannable.
        if len(body) > 600:
            body = body[:600].rstrip() + "…"
        log.info(
            "recall from vault: query=%r → %s",
            query[:60], getattr(top, "path", "?"),
        )
        return body

    async def _execute_user_save_fallback(
        self, ctx: TurnContext, turn_id: str, emitter: EventEmitter,
    ) -> tuple[list[dict], list[dict]]:
        """Run vault_learn derived from the user message when the main
        synth/planner path failed to emit one.

        Returns (actions_executed, receipts). Both empty when no clear
        save intent + parseable shape is present, or the executor is
        unavailable. The action is appended to the turn-log so we can
        verify the save happened even when the LLM chain blew up.
        """
        derived_actions: list[dict] = []
        d_save = _derive_save_action_from_user(ctx.user_msg, [])
        if d_save:
            derived_actions.append(d_save)
        d_upd = _derive_update_action_from_user(ctx.user_msg, derived_actions)
        if d_upd:
            derived_actions.append(d_upd)
        d_forget = _derive_forget_action_from_user(ctx.user_msg, derived_actions)
        if d_forget:
            derived_actions.append(d_forget)
        d_img = _derive_image_action_from_user(ctx.user_msg, derived_actions)
        if d_img:
            derived_actions.append(d_img)
        if not derived_actions or self.executor is None:
            return [], []
        log.info(
            "vault actions derived in error-fallback path; verbs=%s",
            [a.get("verb") for a in derived_actions],
        )
        from dataclasses import asdict
        try:
            exec_results = await self.executor.execute_all(
                derived_actions,
                device_id=ctx.device_id,
                device_audience=ctx.device_audience,
                user_id=ctx.user_id,
                thread_id=ctx.thread_id,
                bot=ctx.bot,
            )
            receipts = [asdict(r) for r in exec_results]
        except Exception as e:  # noqa: BLE001
            log.exception("user-save fallback executor crashed")
            receipts = [{
                "verb": "executor", "ok": False,
                "detail": f"executor crashed: {e}",
                "payload": {},
            }]
        emitter.synthesis(
            summary=f"executed {sum(1 for r in receipts if r.get('ok'))}/"
                    f"{len(receipts)} fallback actions",
            actions=receipts, parent_id=turn_id,
        )
        return derived_actions, receipts

    async def _drain_late_tasks(self, timeout: float = 10.0) -> None:
        """No-op since #484 (helpers are cancelled at synth-gate).

        Kept as a public method because the lifespan shutdown hook in
        `gateway/app.py` and the existing test suite call it. Returns
        immediately because `_late_helper_tasks` is always empty.
        """
        if not self._late_helper_tasks:
            return
        await asyncio.wait(
            self._late_helper_tasks,
            timeout=timeout,
            return_when=asyncio.ALL_COMPLETED,
        )

    # ------------------------------------------------------------ public

    async def coordinate(
        self, ctx: TurnContext, emitter: EventEmitter,
    ) -> AssistantTurn:
        """Run a single user turn through the hive. Never raises."""
        deadline = time.monotonic() + self.budget.total_timeout_s
        turn_id = f"tk-{uuid.uuid4().hex[:8]}"

        # 1. Plan
        plan = await self._plan(ctx, turn_id, deadline, emitter)
        if plan is None or plan.error:
            # Planner failed — synthesize a basic reply so the user
            # isn't left hanging. Preserve the failed result for the
            # turn-log so we can debug the model output.
            # Still try the user-save fallback: a planner blow-up is
            # the most common reason a save gets lost, and we have a
            # clean shot at it without any LLM dependency.
            fb_actions, fb_receipts = await self._execute_user_save_fallback(
                ctx, turn_id, emitter,
            )
            # Recall fallback: when the user asked a "what was X" /
            # "recall X" question and the planner died before any
            # helper ran, search the vault directly and return a
            # grounded answer instead of the canned apology. Long-
            # session eval (2026-06-05) showed 3/3 late-turn recalls
            # dropping to "I had trouble planning" once Ollama drifted
            # to CPU — this surfaces the answer even when the LLM
            # chain is dead.
            recall_reply = await self._maybe_recall_from_vault(ctx)
            reply = recall_reply or self._fallback_reply(ctx)
            emitter.assistant(reply, parent_id=turn_id)
            return AssistantTurn(
                reply=reply,
                actions=fb_actions,
                receipts=fb_receipts,
                error=(plan.error if plan else "planner unavailable"),
                turn_id=turn_id,
                planner_result=plan,
                synth_mode="compose-skipped-by-design",
            )

        # `direct_reply` is for turns the planner can answer NOW with
        # no helper work — small talk, clarifying questions, simple
        # acknowledgements. If the planner ALSO emitted delegations,
        # it's being inconsistent: the user gets a promise ("I'm
        # researching now") but no work runs. Honour the delegations
        # in that case and ignore direct_reply.
        #
        # Also: direct_reply skips the synthesizer entirely, which is
        # the only path that emits side-effect actions (vault_learn,
        # image_render, vault_forget, ...). Planner prompt Rule 11
        # already forbids direct_reply for write/save intents, but the
        # SC knowledge-base run (2026-06-02) showed the LLM violating
        # it 33/33 times — every "save this note" produced a confident
        # acknowledgement and zero vault writes. Server-side override:
        # if user text shows write intent, fall through to dispatch/
        # synth so an action CAN be emitted.
        delegations_raw = plan.output.get("delegations") or []
        if (
            plan.output.get("direct_reply")
            and not delegations_raw
            and _looks_like_write_intent(ctx.user_msg)
        ):
            log.info(
                "direct_reply overridden: user text shows write intent — "
                "forcing synth path so vault_learn can be emitted"
            )
            plan.output["direct_reply"] = None
        if plan.output.get("direct_reply") and not delegations_raw:
            reply = str(plan.output["direct_reply"])
            emitter.assistant(reply, parent_id=turn_id)
            return AssistantTurn(
                reply=reply,
                helpers_used=["planner"],
                total_tokens=plan.tokens_in + plan.tokens_out,
                total_latency_ms=plan.latency_ms,
                turn_id=turn_id,
                planner_result=plan,
                synth_mode="compose-skipped-by-design",
            )

        # 2. Dispatch — bounded by an EARLIER deadline so synthesis +
        # critic always have room. Even if dispatch runs to its limit,
        # we still have ≥ synth_reservation_s for the closing stages.
        delegations = self._coerce_delegations(
            plan.output.get("delegations") or [],
        )
        dispatch_deadline = min(
            deadline,
            time.monotonic() + max(
                self.budget.total_timeout_s - self.budget.synth_reservation_s,
                10.0,
            ),
        )
        helper_results = await self._dispatch(
            delegations, ctx, turn_id, dispatch_deadline, emitter,
        )

        # 3. Critic gate (risky delegations)
        any_risky = any(d.get("risky") for d in delegations)
        critic_result: HelperResult | None = None
        if any_risky and "critic" in self.helpers:
            critic_result = await self._invoke_critic(
                plan, helper_results, ctx, turn_id, deadline, emitter,
            )
            if critic_result is not None and critic_result.output.get("block"):
                reason = critic_result.output.get("reason", "critic blocked")
                emitter.synthesis(
                    summary=f"Blocked: {reason}", actions=[], parent_id=turn_id,
                )
                emitter.assistant(
                    f"I held off — {reason}", parent_id=turn_id,
                )
                return AssistantTurn(
                    reply=f"I held off — {reason}",
                    blocked=True,
                    helpers_used=[r.role for r in helper_results] + ["critic"],
                    turn_id=turn_id,
                    planner_result=plan,
                    helper_results=helper_results,
                    critic_result=critic_result,
                    synth_mode="compose-skipped-by-design",
                )

        # 4. Synthesize
        synth = await self._synthesize(
            plan, helper_results, ctx, turn_id, deadline, emitter,
        )

        # 5. Compute totals
        all_results = [plan] + helper_results
        if critic_result is not None:
            all_results.append(critic_result)
        if synth is not None:
            all_results.append(synth)
        total_tokens = sum(r.tokens_in + r.tokens_out for r in all_results)
        total_latency = sum(r.latency_ms for r in all_results)

        if synth is None or synth.error:
            # Synth blew up — still try the user-save fallback so write
            # intents don't get silently dropped on synth timeout.
            fb_actions, fb_receipts = await self._execute_user_save_fallback(
                ctx, turn_id, emitter,
            )
            reply = self._compose_fallback(plan, helper_results)
            emitter.assistant(reply, parent_id=turn_id)
            return AssistantTurn(
                reply=reply,
                actions=fb_actions,
                receipts=fb_receipts,
                helpers_used=[r.role for r in helper_results],
                total_tokens=total_tokens,
                total_latency_ms=total_latency,
                error=(
                    f"synthesizer failed: {synth.error}"
                    if synth and synth.error
                    else "synthesizer unavailable"
                ),
                turn_id=turn_id,
                planner_result=plan,
                helper_results=helper_results,
                critic_result=critic_result,
                synth_result=synth,
            )

        reply = str(synth.output.get("reply", "")).strip()
        # Defensive: the LLM occasionally emits `actions` as a string
        # or null instead of a list. Without this guard, `list("foo")`
        # would split into chars and the executor would silently
        # discard each one.
        raw_actions = synth.output.get("actions")
        if isinstance(raw_actions, list):
            actions = list(raw_actions)
        else:
            if raw_actions is not None:
                log.warning(
                    "synthesizer emitted non-list `actions` of type %s — "
                    "ignoring; raw=%r",
                    type(raw_actions).__name__, str(raw_actions)[:100],
                )
            actions = []
        # Server-side vault_learn emitter: when the user message is a
        # clear "Save 'X' — Y" but synth dropped the action, derive it
        # from the user text. The synth's prose ("Saved as `x.md`") is
        # otherwise a lie — this turns it into truth.
        derived = _derive_save_action_from_user(ctx.user_msg, actions)
        if derived is not None:
            log.info(
                "vault_learn derived server-side from user msg (synth "
                "did not emit one); title=%r",
                derived["payload"].get("title", "")[:80],
            )
            actions.append(derived)
        # Same pattern for update + forget so planner-qwen's drift in
        # emitting non-save verbs doesn't drop the user's mutation.
        derived_update = _derive_update_action_from_user(ctx.user_msg, actions)
        if derived_update is not None:
            log.info(
                "vault_learn (update) derived server-side; title=%r",
                derived_update["payload"].get("title", "")[:80],
            )
            actions.append(derived_update)
        derived_forget = _derive_forget_action_from_user(ctx.user_msg, actions)
        if derived_forget is not None:
            log.info(
                "vault_forget derived server-side; query=%r",
                derived_forget["payload"].get("query", "")[:80],
            )
            actions.append(derived_forget)
        derived_image = _derive_image_action_from_user(ctx.user_msg, actions)
        if derived_image is not None:
            log.info(
                "image_render derived server-side; prompt[:60]=%r count=%d",
                derived_image["payload"].get("prompt", "")[:60],
                derived_image["payload"].get("count", 1),
            )
            actions.append(derived_image)
        if not reply:
            reply = self._compose_fallback(plan, helper_results)

        # Programmatic hallucination guard. Strips sentences whose
        # numeric claims don't trace to a helper, sentences that claim
        # an action verb that wasn't actually emitted in `actions`
        # (e.g. "smart-linked" — no such verb exists), sentences that
        # claim a helper ran when it didn't ("I fired a web search"
        # with no researcher in helper_results), and leaked meta-
        # preamble / action-JSON blocks.
        reply = strip_hallucinated_sentences(reply, helper_results, actions)

        # If the strip emptied the reply, the synth's whole response
        # was high-confidence hallucination (action-claim or helper-run
        # lies). Substitute the structured fallback so the user gets
        # an honest reply instead of nothing.
        if not reply:
            reply = self._compose_fallback(plan, helper_results)

        # Empty-retrieval guard. When every librarian/researcher entry
        # came back empty AND the LLM still produced a confident
        # narrative reply with no acknowledgement of the empty state,
        # force the canonical "I couldn't find that" message. The
        # synthesizer prompt's Rule 8 covers this case in principle
        # but the LLM occasionally fabricates anyway.
        reply = enforce_empty_retrieval_reply(reply, helper_results)
        # Groundedness guard: catches generic preamble replies that
        # surface no specific entities from the librarian/researcher
        # hits. Runs AFTER empty-retrieval guard so the two don't
        # conflict (this one only fires when hits exist).
        reply = enforce_groundedness_with_hits(
            reply, helper_results, user_msg=ctx.user_msg,
        )

        # 4b. Post-synthesis critic gate. Side-effects come from the
        # synthesizer's `actions`, not the planner's delegations —
        # checking only `delegations[].risky` lets vault_forget /
        # create_skill / image_render / ntfy_push slip through
        # un-reviewed. Re-classify based on the actual emitted verbs
        # and gate again if anything risky is in there. Skip when
        # we already ran the critic on a risky delegation (no need to
        # double-bill the model).
        # `escalate_to_dev` is included so a prompt-injected note can't
        # spam the developer queue without the critic seeing the
        # delegation it came from.
        risky_verbs = {
            "vault_forget", "create_skill", "image_render",
            "ntfy_push", "escalate_to_dev",
            # Phase 3: synthesizer-emitted memory mutations are risky
            # because a prompt-injected note could rewrite the user
            # profile or inject false "preferences" into the planner
            # prompt for every future turn. `core_memory_append` is
            # equally risky — append-only doesn't help when the appended
            # line is what lands in the planner's render window.
            "core_memory_replace", "core_memory_append",
            "entity_page_update",
            # `image_build_update`'s string slots (subject/mood/negative/notes)
            # land verbatim in the next turn's Planner system prompt via
            # ImageBuildState.render_block(). A prompt-injected vault note can
            # ride this path to poison Planner context across turns; the
            # critic gate is the chokepoint that catches it.
            "image_build_update",
            # `run_python` executes arbitrary code in a sandboxed
            # subprocess (memory + wall-clock capped, no network at OS
            # level). Network isolation is best-effort — gate via critic
            # so a prompt-injected note can't trick the synth into
            # exfiltrating data via socket calls.
            "run_python",
            # Phase C: file-write side effects. A prompt-injected note
            # can't directly exfiltrate via these (no network), but they
            # do persist arbitrary text under media/ where future renders
            # may surface it. Critic gate catches the injection up-front.
            "generate_doc", "generate_deck",
            # Phase B: external SaaS call via Composio. Network egress to
            # third-party app, can post messages / create tickets / send
            # email under the user's identity. Critic gate is mandatory.
            "saas_call",
        }
        synth_risky = actions and any(
            isinstance(a, dict) and a.get("verb") in risky_verbs
            for a in actions
        )
        if synth_risky and not any_risky and "critic" in self.helpers:
            post_critic = await self._invoke_critic_for_actions(
                plan, helper_results, actions, ctx, turn_id, deadline, emitter,
            )
            if post_critic is not None and post_critic.output.get("block"):
                reason = post_critic.output.get("reason", "critic blocked")
                emitter.synthesis(
                    summary=f"Blocked: {reason}", actions=[], parent_id=turn_id,
                )
                emitter.assistant(
                    f"I held off — {reason}", parent_id=turn_id,
                )
                return AssistantTurn(
                    reply=f"I held off — {reason}",
                    blocked=True,
                    helpers_used=[r.role for r in helper_results] + ["critic"],
                    turn_id=turn_id,
                    planner_result=plan,
                    helper_results=helper_results,
                    critic_result=post_critic,
                    synth_mode="compose-skipped-by-design",
                )
            critic_result = post_critic  # surface in turn-log

        # 5. Execute side-effect actions.
        receipts: list[dict] = []
        if actions and self.executor is not None:
            from dataclasses import asdict
            try:
                exec_results = await self.executor.execute_all(
                    actions,
                    device_id=ctx.device_id,
                    device_audience=ctx.device_audience,
                    user_id=ctx.user_id,
                    thread_id=ctx.thread_id,
                    bot=ctx.bot,
                )
                receipts = [asdict(r) for r in exec_results]
            except Exception as e:  # noqa: BLE001
                log.exception("action execution wrapper failed")
                receipts = [{
                    "verb": "executor", "ok": False,
                    "detail": f"executor crashed: {e}",
                    "payload": {},
                }]
            # Re-emit synthesis to surface receipts in the trace.
            emitter.synthesis(
                summary=f"executed {sum(1 for r in receipts if r.get('ok'))}/"
                        f"{len(receipts)} actions",
                actions=receipts, parent_id=turn_id,
            )

        emitter.assistant(reply, parent_id=turn_id)
        return AssistantTurn(
            reply=reply,
            actions=actions,
            receipts=receipts,
            helpers_used=[r.role for r in all_results if r.role != "synthesizer"],
            total_tokens=total_tokens,
            total_latency_ms=total_latency,
            turn_id=turn_id,
            planner_result=plan,
            helper_results=helper_results,
            critic_result=critic_result,
            synth_result=synth,
        )

    # ------------------------------------------------------------ planner

    async def _plan(
        self,
        ctx: TurnContext,
        turn_id: str,
        deadline: float,
        emitter: EventEmitter,
    ) -> HelperResult | None:
        """Returns the HelperResult always, even on error, so the caller
        can preserve it for the turn log. Returns None only if no
        planner helper is configured."""
        if "planner" not in self.helpers:
            return None
        # Build retrieved-knowledge block when vault snippets were injected
        # by build_turn_context. Fenced so the planner knows it's reference
        # material — may be incomplete, never authoritative.
        retrieved_knowledge: str = ""
        if ctx.vault_snippets:
            block_lines = [
                "```retrieved_knowledge (top-k vault search — may be incomplete)```"
            ]
            for i, snippet in enumerate(ctx.vault_snippets, 1):
                block_lines.append(f"[{i}] {snippet}")
            block_lines.append("```end_retrieved_knowledge```")
            retrieved_knowledge = "\n".join(block_lines)

        task = HelperTask(
            role="planner",
            goal="decide what to do next",
            inputs={
                "user_msg": ctx.user_msg,
                "context": ctx.history_digest,
                "retrieved_knowledge": retrieved_knowledge,
                "image_build": ctx.image_build,
                "skills": ctx.skills_digest,
                "suggested_skills": ctx.suggested_skills,
                "available_helpers": ctx.available_helpers
                                    or list(self.helpers.keys()),
            },
            constraints=["read-only", "≤30s"],
            parent_id=turn_id,
        )
        result = await self._run_helper(task, deadline)
        emitter.thought(
            summary=result.output.get("summary", "")
                    if not result.error else f"planner failed: {result.error}",
            delegations=result.output.get("delegations", [])
                        if not result.error else [],
            model=result.model_id, latency_ms=result.latency_ms,
            tokens=result.tokens_in + result.tokens_out,
            id=turn_id, parent=None,
        )
        if result.error:
            log.warning("planner failed: %s", result.error)
        return result

    # ------------------------------------------------------------ dispatch

    def _resolve_helper_cap(self) -> tuple[int, bool]:
        """Pick the active helper-fan-out cap for this turn.

        Returns (cap, gaming_detected). When the user is gaming on GPU 0
        we drop to `budget.gaming_concurrent_helpers` so the available
        cards (1, 2) aren't flooded; otherwise the full cap applies.
        Failures probing scout fall back to the full cap — we'd rather
        run faster than starve helpers on a transient detection error.
        """
        gaming = _gaming_on_gpu0()
        live_max = self.budget.live_max_concurrent()
        cap = (
            self.budget.gaming_concurrent_helpers
            if gaming
            else live_max
        )
        # Clamp so gaming cap never exceeds live_max (honours VRAM pressure
        # even when gaming detection fires), and never drops below 1.
        cap = max(1, min(cap, live_max))
        return cap, gaming

    async def _dispatch(
        self,
        delegations: list[dict],
        ctx: TurnContext,
        turn_id: str,
        deadline: float,
        emitter: EventEmitter,
    ) -> list[HelperResult]:
        cap, gaming = self._resolve_helper_cap()
        if gaming:
            log.info(
                "hive: gaming detected on GPU 0 — capping helpers at %d", cap,
            )
        # Cap to budget (gaming-aware).
        delegations = delegations[:cap]
        if not delegations:
            return []

        sem = asyncio.Semaphore(cap)

        async def _one(d: dict) -> HelperResult | None:
            async with sem:
                role = d.get("role", "")
                if role not in self.helpers:
                    log.warning("planner asked for unknown helper %r", role)
                    return None
                # Phase D comm-graph gate: defense-in-depth against a
                # prompt-injected planner output requesting an
                # off-graph role (e.g. trying to invoke synthesizer or
                # critic mid-plan to bypass the gates downstream).
                from gateway.helpers.comm_graph import is_allowed
                if not is_allowed("planner", role):
                    log.warning(
                        "comm-graph denied planner→%r (not in ALLOWED_EDGES)",
                        role,
                    )
                    return None
                inputs = dict(d.get("inputs", {}))
                # Inject the serving bot so audience-aware helpers
                # (e.g. librarian) can scope their queries correctly
                # without the planner having to know about TurnContext.
                inputs.setdefault("bot", ctx.bot)
                inputs.setdefault("user_id", ctx.user_id)
                inputs.setdefault("thread_id", ctx.thread_id)
                task = HelperTask(
                    role=role,
                    goal=d.get("goal", role),
                    inputs=inputs,
                    constraints=d.get("constraints", []),
                    parent_id=turn_id,
                )
                emitter.delegate(
                    role=role, goal=task.goal,
                    model=self._model_for(role),
                    parent=turn_id,
                    id=f"{turn_id}.{role}",
                )
                result = await self._run_helper(task, deadline)
                emitter.helper_reply(result, id=f"{turn_id}.{role}", parent=turn_id)
                return result

        # Synth-on-ready gate (#476 Phase B + #484 hard-cancel). Wait up
        # to synth_gate_s for ALL helpers; whoever finished feeds synth.
        # Pending tasks are HARD-CANCELLED so synth gets the Ollama slot
        # to itself (single NUM_PARALLEL=1 slot on planner-qwen). Cancel
        # propagates through `asyncio.wait_for` into httpx, which closes
        # the Ollama HTTP request and frees the slot. No fake
        # "turn budget timeout" rows — pending tasks just don't make it
        # into `results`.
        tasks: list[asyncio.Task] = [
            asyncio.create_task(_one(d), name=f"helper:{d.get('role', '?')}")
            for d in delegations
        ]
        gate = self.budget.synth_gate_s
        done, pending = await asyncio.wait(
            tasks, timeout=gate, return_when=asyncio.ALL_COMPLETED,
        )
        results: list[HelperResult] = []
        for t in done:
            try:
                r = t.result()
            except Exception:  # noqa: BLE001
                log.exception("helper task raised before synth gate")
                continue
            if r is not None:
                results.append(r)
        if pending:
            for t in pending:
                t.cancel()
            # Drain so cancellation actually propagates into Ollama HTTP
            # teardown before synth fires. Bounded — httpx cancel is
            # essentially synchronous.
            await asyncio.gather(*pending, return_exceptions=True)
        return results

    # ------------------------------------------------------------ critic

    async def _invoke_critic(
        self,
        plan: HelperResult,
        helper_results: list[HelperResult],
        ctx: TurnContext,
        turn_id: str,
        deadline: float,
        emitter: EventEmitter,
    ) -> HelperResult | None:
        if "critic" not in self.helpers:
            return None
        task = HelperTask(
            role="critic",
            goal="review proposed risky actions",
            inputs={
                "user_msg": ctx.user_msg,
                "planner_summary": plan.output.get("summary", ""),
                "helper_summaries": [
                    {"role": r.role, "summary": r.output.get("summary", "")}
                    for r in helper_results
                ],
            },
            parent_id=turn_id,
        )
        emitter.delegate(
            role="critic", goal=task.goal,
            model=self._model_for("critic"),
            parent=turn_id, id=f"{turn_id}.critic",
        )
        result = await self._run_helper(task, deadline)
        emitter.helper_reply(result, id=f"{turn_id}.critic", parent=turn_id)
        if result.error:
            log.warning("critic failed: %s — proceeding without gate", result.error)
            return None
        return result

    async def _invoke_critic_for_actions(
        self,
        plan: HelperResult,
        helper_results: list[HelperResult],
        actions: list[dict],
        ctx: TurnContext,
        turn_id: str,
        deadline: float,
        emitter: EventEmitter,
    ) -> HelperResult | None:
        """Critic gate over the synthesizer's emitted action list.

        Use case: the planner may dispatch only `librarian` (a safe
        read) but the synthesizer emits a `vault_forget` to delete a
        bunch of notes. The pre-dispatch critic never saw that action.
        This second-pass gate lets the critic veto the action set,
        with the same {block, reason} contract.
        """
        if "critic" not in self.helpers:
            return None
        # Show the critic just the verbs + payload summaries — no
        # need for the full helper trace at this stage; that already
        # ran through the pre-dispatch critic call (when applicable).
        action_digest = [
            {"verb": a.get("verb"), "payload_keys": list((a.get("payload") or {}).keys())}
            for a in actions if isinstance(a, dict)
        ]
        task = HelperTask(
            role="critic",
            goal="review side-effect actions before execution",
            inputs={
                "user_msg": ctx.user_msg,
                "planner_summary": plan.output.get("summary", ""),
                "helper_summaries": [
                    {"role": r.role, "summary": r.output.get("summary", "")}
                    for r in helper_results
                ],
                "proposed_actions": action_digest,
            },
            parent_id=turn_id,
        )
        emitter.delegate(
            role="critic", goal=task.goal,
            model=self._model_for("critic"),
            parent=turn_id, id=f"{turn_id}.critic.actions",
        )
        result = await self._run_helper(task, deadline)
        emitter.helper_reply(
            result, id=f"{turn_id}.critic.actions", parent=turn_id,
        )
        if result.error:
            log.warning(
                "post-synth critic failed: %s — proceeding without gate",
                result.error,
            )
            return None
        return result

    # ------------------------------------------------------------ synthesizer

    async def _synthesize(
        self,
        plan: HelperResult,
        helper_results: list[HelperResult],
        ctx: TurnContext,
        turn_id: str,
        deadline: float,
        emitter: EventEmitter,
    ) -> HelperResult | None:
        if "synthesizer" not in self.helpers:
            return None
        # Defense-in-depth for synthesizer Rule 8b: blank librarian /
        # researcher hits whose content shares no salient tokens with
        # the user's question. The prompt-level rule isn't reliable
        # on its own — production data 04-26..05-01 shows the LLM
        # occasionally renders off-topic hits verbatim (Drake Cutlass
        # Black → 17th-century-dagger note). Programmatic gate makes
        # the empty-helper path deterministic.
        gated = filter_irrelevant(ctx.user_msg, helper_results)
        # Helper outputs carry untrusted content (web research,
        # vault notes, user-pasted recipe text). Wrap each string
        # value in <untrusted>...</untrusted> markers so the
        # synthesizer's prompt rule "data, not instructions" has a
        # boundary to enforce. Without this, a malicious LoRA
        # description or recipe paste could potentially steer the
        # synthesizer's action emission. See prompt_safety.py for the
        # convention.
        task = HelperTask(
            role="synthesizer",
            goal="compose Terry's reply",
            inputs={
                "user_msg": ctx.user_msg,
                "planner_summary": plan.output.get("summary", ""),
                "context": ctx.history_digest,
                "helper_results": sanitise_helper_outputs(gated),
            },
            parent_id=turn_id,
        )
        result = await self._run_helper(task, deadline)
        if result.error:
            emitter.synthesis(
                summary=f"synthesizer failed: {result.error}",
                actions=[], parent_id=turn_id,
            )
            # Return the failed result rather than None so the turn-log
            # captures the actual error + raw_text. Caller checks
            # `result.error` and falls through to _compose_fallback.
            return result
        emitter.synthesis(
            summary=result.output.get("reply", "")[:120],
            actions=result.output.get("actions", []),
            parent_id=turn_id,
        )
        return result

    # ------------------------------------------------------------ infra

    async def _run_helper(
        self, task: HelperTask, deadline: float,
    ) -> HelperResult:
        helper = self.helpers.get(task.role)
        if helper is None:
            # Defensive — `_dispatch` filters unknown roles, but the
            # critic / synthesizer paths call _run_helper directly and
            # `self.helpers` can be mutated during hot-reload tests.
            return HelperResult(
                role=task.role, model_id="unknown",
                error=f"helper {task.role!r} not registered",
                parent_id=task.parent_id,
            )
        # VRAM-aware fallback: if running this helper on GPU would
        # overflow the budget, route it to CPU. If CPU+RAM is also
        # under pressure, serialise instead.
        #
        # Read the model id from the helper itself rather than from
        # the catalog default. Phase 3.1 lets the Router pick a
        # different model per role at construction time (faster/cheaper
        # for simple roles); the helper.model_id reflects that choice,
        # so VRAM accounting + invocation stay aligned. If the helper
        # carries a synthetic model id that isn't in the catalog
        # (common in tests), fall back to the catalog's YAML default.
        helper_model_id = getattr(helper, "model_id", None)
        model_id = None
        m_entry = None
        if helper_model_id:
            try:
                m_entry = self.catalog.model(helper_model_id)
                model_id = helper_model_id
            except KeyError:
                m_entry = None
        if m_entry is None:
            try:
                model_id = self.catalog.helper(task.role).model
                m_entry = self.catalog.model(model_id)
            except KeyError:
                return HelperResult(
                    role=task.role, model_id="unknown",
                    error=f"no catalog entry for helper {task.role!r}",
                    parent_id=task.parent_id,
                )
        use_cpu = False
        async with self._vram_lock:
            if (
                self._in_flight_vram + m_entry.gpu_vram_mb
                > self.budget.vram_budget_mb
            ):
                if m_entry.cpu_fallback:
                    # System-RAM safety check.
                    free_ram = _free_system_ram_mb()
                    floor = self.budget.min_free_ram_mb
                    cpu_cost = m_entry.cpu_ram_mb or 0
                    if (
                        free_ram is None
                        or floor is None
                        or free_ram - cpu_cost >= floor
                    ):
                        use_cpu = True
                    # else: even CPU is contested → queue on GPU.
            if not use_cpu:
                self._in_flight_vram += m_entry.gpu_vram_mb

        # Build a new task with CPU hint.
        if use_cpu and not task.use_cpu:
            task = HelperTask(
                role=task.role, goal=task.goal, inputs=task.inputs,
                constraints=task.constraints,
                expected_schema=task.expected_schema,
                parent_id=task.parent_id, use_cpu=True,
            )

        # `deadline` is retained on the signature for caller compatibility
        # (Phase B.5 may re-use it for synth-on-ready gate accounting); no
        # longer enforced here. BaseHelper.invoke already wraps its model
        # call in `asyncio.wait_for(self.timeout_s)` (helpers/base.py:430),
        # so genuinely-stuck helpers still abort. The old outer wait_for
        # was producing latency_ms:0 "turn budget timeout" rows for
        # researcher in scenarios 06-09 of #447 — see #476.
        del deadline  # silence linters; intentionally unused
        # Pre-bind so a `CancelledError` propagating out of the try
        # doesn't hit the `return result` with `result` unbound.
        # (CancelledError inherits from BaseException since 3.8 and
        # bypasses the `except Exception` arm — without this default
        # the function raised UnboundLocalError mid-cancel.)
        result = HelperResult(
            role=task.role, model_id=model_id,
            error="cancelled",
            parent_id=task.parent_id,
        )
        try:
            result = await helper.invoke(task)
        except asyncio.CancelledError:
            # Turn was cancelled (WS disconnect, parent shutdown). Keep
            # the prebound "cancelled" result so VRAM still releases
            # cleanly in `finally`, then re-raise so cancellation
            # propagates up to the gather caller.
            raise
        except Exception as e:  # noqa: BLE001
            # A subclass that broke its "never raise" contract.
            # Convert to an error result so VRAM still releases AND the
            # turn-log captures the cause.
            log.exception("helper %s raised unexpectedly", task.role)
            result = HelperResult(
                role=task.role, model_id=model_id,
                error=f"helper crashed: {type(e).__name__}: {e}",
                parent_id=task.parent_id,
            )
        finally:
            if not use_cpu:
                async with self._vram_lock:
                    self._in_flight_vram = max(
                        0, self._in_flight_vram - m_entry.gpu_vram_mb,
                    )
        return result

    def _model_for(self, role: str) -> str:
        if self.router is not None:
            try:
                choice = self.router.route_for(role)
                return (
                    choice.model.ollama_name
                    or choice.model.cloud_model_name
                    or choice.model.id
                )
            except KeyError:
                pass  # fall through to legacy lookup
        try:
            return self.catalog.model(self.catalog.helper(role).model).ollama_name
        except KeyError:
            return "unknown"

    @staticmethod
    def _coerce_delegations(raw: list[Any]) -> list[dict]:
        out: list[dict] = []
        for r in raw:
            if isinstance(r, dict):
                out.append(r)
            elif hasattr(r, "model_dump"):
                out.append(r.model_dump())
        return out

    @staticmethod
    def _fallback_reply(ctx: TurnContext) -> str:
        return (
            "I had trouble planning that one. Could you rephrase, "
            "or break it into smaller steps?"
        )

    @staticmethod
    def _compose_fallback(
        plan: HelperResult, helper_results: list[HelperResult],
    ) -> str:
        """Used when the synthesizer times out / errors / returns empty.
        Always returns a single clean line — never exposes helper
        role names or per-helper summaries to the user. The previous
        "Here's what I got (...): - role: summary" path leaked
        internal architecture (10%+ of turns in 2026-04-26..05-01 prod
        data) and made the bot look broken; the diagnostic detail still
        lives in turn-logs via `synth_result.error`.

        Specialised when all retrieval helpers came back empty — the
        more informative 'I couldn't find that in your vault or on
        the web' message is more actionable than the generic apology.
        """
        from gateway.hallucination_guard import (
            all_retrieval_helpers_empty,
            grounded_snippets_from_helpers,
        )
        if all_retrieval_helpers_empty(helper_results):
            return (
                "I couldn't find that in your vault or on the web. "
                "Try rephrasing, or give me a more specific angle to "
                "search for."
            )
        # Synthesizer failed but helpers returned real content — hand
        # the user what we actually found rather than a generic apology.
        # Snippets are verbatim helper output (no role names, no
        # fabrication), so we stay within the no-leak rule.
        snippets = grounded_snippets_from_helpers(helper_results)
        if snippets:
            # Inline rather than bulleted: bulleted lists in the
            # fallback path were how the old "leaks helper structure"
            # bug surfaced (see test_coordinator_preserves_synth_error
            # _in_turn). Joining with "; " keeps the rule "no list
            # markers" while still handing the user real content.
            body = "; ".join(s.rstrip(".") for s in snippets)
            return (
                "I couldn't polish this into a full answer, but here's "
                f"what I found: {body}."
            )
        return (
            "I worked through that but couldn't compose a clean reply. "
            "Try rephrasing or splitting the request?"
        )


# ---------------------------------------------------------------- hallucination guard


