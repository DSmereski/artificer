"""Helpers extracted from `routes/chat.py::_hive_turn`.

The single 263-line function the analyst's 2026-04-29 review flagged
as "doing six unrelated jobs" splits naturally:

  1. `build_turn_context` — gather skills digest, image-build state,
     history digest, then build the TurnContext.
  2. `record_turn_telemetry` — append to the in-memory ring used by
     the /v1/telemetry surface.
  3. `record_turn_log` — append the structured JSONL log entry to
     `<state_dir>/turn-logs/`.
  4. `publish_turn_done_notifications` — event-bus + ntfy fan-out.
  5. `schedule_summarizer_refresh` — kick off the async memory-store
     summarizer when the turn count crosses the threshold.

`_hive_turn` itself stays in chat.py as the orchestrator (it owns the
WS emitter + the cancellation race), but each step is now testable in
isolation and the body shrinks from 263 lines to ~40.

None of these raise. Everything is best-effort with a logged warning;
the architect's principle is "the user-visible reply is the
contract, the rest is observability."
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import asdict
from typing import Any

from gateway.deps import track_background_task


log = logging.getLogger("gateway.hive_turn_helpers")

# Maximum wall-clock time (seconds) we will spend on the vault search
# that pre-populates TurnContext.vault_snippets. If the Ollama embedder
# or vault daemon is slow we must NOT stall the user's turn — we simply
# skip injection for this turn, which degrades gracefully to the old
# behavior (no retrieved context).
_VAULT_SEARCH_TIMEOUT_S: float = float(
    os.environ.get("HIVE_VAULT_SNIPPET_TIMEOUT_S", "3.0")
)
# Number of vault hits to inject. k=3 keeps the context block small
# (one short paragraph each) while still grounding the planner.
_VAULT_SNIPPET_K: int = int(os.environ.get("HIVE_VAULT_SNIPPET_K", "3"))
# Maximum characters per snippet in the injected block.
_VAULT_SNIPPET_MAX_CHARS: int = 300


async def _fetch_vault_snippets(
    app_state: Any,
    *,
    text: str,
    device_audience: list[str] | None,
) -> list[str]:
    """Run a cheap top-k hybrid vault search and return snippet strings.

    Returns an empty list when the vault client is unavailable, embedder
    is down, or the call takes longer than _VAULT_SEARCH_TIMEOUT_S.
    Never raises.
    """
    vc = getattr(app_state, "vault_client", None)
    if vc is None:
        return []
    config = getattr(app_state, "config", None)
    if config is None:
        return []
    try:
        ollama_url = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        embed_model = getattr(
            getattr(config, "vault_writer", None), "embed_model",
            "nomic-embed-text",
        ) or "nomic-embed-text"
        audience = (device_audience[0] if device_audience else "all")

        async def _search() -> list[str]:
            from shared.embeddings import embed_text
            vec = await embed_text(
                text, ollama_url=ollama_url,
                model=embed_model, timeout=_VAULT_SEARCH_TIMEOUT_S - 0.5,
                kind="query",
            )
            if not vec:
                return []
            results = vc.search(
                query_embedding=vec,
                k=_VAULT_SNIPPET_K,
                audience=audience,
                query_text=text,
            )
            snippets: list[str] = []
            for r in results:
                body = (getattr(r, "body", "") or "").strip().replace("\n", " ")
                path = getattr(r, "path", "")
                if body:
                    snippet = body[:_VAULT_SNIPPET_MAX_CHARS]
                    if len(body) > _VAULT_SNIPPET_MAX_CHARS:
                        snippet += "..."
                    snippets.append(f"[{path}] {snippet}")
            return snippets

        return await asyncio.wait_for(
            _search(), timeout=_VAULT_SEARCH_TIMEOUT_S,
        )
    except Exception as e:  # noqa: BLE001
        log.debug("vault snippet fetch skipped: %s", e)
        return []


async def build_turn_context_async(
    app_state: Any,
    *,
    user_id: int,
    text: str,
    device_id: str,
    device_audience: list[str] | None,
    thread_id: str = "default",
):
    """Async variant of build_turn_context that also injects vault snippets.

    Called from the chat route when the event loop is available. Falls back
    to build_turn_context (sync) if vault search fails — the turn is never
    blocked.
    """
    ctx = build_turn_context(
        app_state,
        user_id=user_id,
        text=text,
        device_id=device_id,
        device_audience=device_audience,
        thread_id=thread_id,
    )
    snippets = await _fetch_vault_snippets(
        app_state, text=text, device_audience=device_audience,
    )
    if snippets:
        from dataclasses import replace as _replace
        ctx = _replace(ctx, vault_snippets=snippets)
        log.debug(
            "vault snippets injected into TurnContext: %d hits", len(snippets),
        )
    return ctx


def build_turn_context(
    app_state: Any,
    *,
    user_id: int,
    text: str,
    device_id: str,
    device_audience: list[str] | None,
    thread_id: str = "default",
):
    """Compose the TurnContext the planner sees. Pulls skills /
    history / image-build state off `app_state` and falls back to
    empty defaults if any source is missing."""
    from gateway.hive_coordinator import TurnContext

    # Image-build state for this device — planner sees what slots are
    # already filled.
    image_build = None
    build_store = app_state.image_build_store
    if build_store is not None:
        bs = build_store.get(device_id)
        if bs is not None:
            image_build = asdict(bs)

    # Skill catalogue digest + trigger-suggested skills.
    skills_digest = ""
    suggested_skills: list[str] = []
    reg = app_state.skill_registry
    if reg is not None:
        try:
            reg.reload_if_changed()
            skills_digest = reg.digest_for_planner(audience="hive")
            suggested_skills = [s.name for s in reg.find_by_trigger(text)]
        except Exception as e:  # noqa: BLE001
            log.warning("skills digest failed: %s", e, exc_info=True)
            skills_digest = ""

    # Conversation memory digest. Thread-keyed when supported.
    history_digest = ""
    memory_store = app_state.memory_store_hive
    if memory_store is not None:
        try:
            if hasattr(memory_store, "get_for_thread"):
                mem = memory_store.get_for_thread(user_id, thread_id)
            else:
                mem = memory_store.get(user_id)
            history_digest = mem.render_for_planner()
        except Exception as e:  # noqa: BLE001
            log.warning("memory digest failed: %s", e, exc_info=True)
            history_digest = ""

    return TurnContext(
        user_msg=text,
        user_id=user_id,
        device_id=device_id,
        bot="hive",
        history_digest=history_digest,
        image_build=image_build,
        skills_digest=skills_digest,
        suggested_skills=suggested_skills,
        # synthesizer + planner are internal pipeline stages — the
        # coordinator runs them itself, the planner must NEVER delegate
        # to them or it'll loop / blow the turn budget.
        available_helpers=[
            r for r in app_state.helpers.keys()
            if r not in {"synthesizer", "planner"}
        ],
        device_audience=device_audience,
        thread_id=thread_id,
    )


def record_turn_telemetry(
    app_state: Any, turn: Any, *, device_id: str, text: str,
) -> None:
    """Push one TurnRecord to the in-memory telemetry ring. No-op
    when telemetry isn't configured (test fixtures, ablations)."""
    tel = app_state.turn_telemetry
    if tel is None:
        return
    from gateway.helpers.base import prompt_version
    from gateway.turn_telemetry import TurnRecord
    try:
        tel.record(TurnRecord(
            ts=time.time(),
            turn_id="ws-" + device_id[:6],
            bot="hive",
            user_msg_preview=text[:240],
            helpers_used=list(turn.helpers_used),
            total_tokens=turn.total_tokens,
            total_latency_ms=turn.total_latency_ms,
            blocked=turn.blocked,
            error=turn.error,
            actions=[
                str(a.get("verb", "?"))
                for a in turn.actions if isinstance(a, dict)
            ],
            planner_prompt_version=prompt_version("planner"),
        ))
    except Exception as e:  # noqa: BLE001
        log.warning("telemetry record failed: %s", e)


def _compute_synth_mode(synth: Any, explicit: str | None = None) -> str:
    """Derive the synth_mode string from a HelperResult (or None).

    "compose"                  — synth succeeded with a JSON reply
    "prose-rescue"             — _parse_fallback recovered a prose-only reply
    "fallback"                 — synth errored or returned an empty reply
    "coordinator-bypass"       — synth was never invoked (synth is None)
    "compose-skipped-by-design"— legitimate bypass (direct_reply, critic-block)

    `explicit` overrides derivation when the coordinator already knows
    the mode (e.g. direct_reply, critic-block paths).
    """
    if explicit is not None:
        return explicit
    if synth is None:
        return "coordinator-bypass"
    if synth.error:
        return "fallback"
    if getattr(synth, "prose_rescue", False):
        return "prose-rescue"
    if isinstance(synth.output, dict) and synth.output.get("reply"):
        return "compose"
    return "fallback"


async def record_turn_log(
    app_state: Any, turn: Any, *,
    user_id: int, device_id: str, text: str,
) -> None:
    """Append the structured-log JSONL entry. Awaits the disk write
    off the hot path inside the log store, so a slow filesystem
    doesn't stall the turn's `done` event."""
    log_store = app_state.turn_log_store
    if log_store is None:
        return
    try:
        from gateway.turn_log import (
            TurnLogEntry, helper_entries_from_results, _preview,
        )
        plan = turn.planner_result
        synth = turn.synth_result
        all_helpers = list(turn.helper_results)
        if turn.critic_result is not None:
            all_helpers.append(turn.critic_result)
        synth_mode = _compute_synth_mode(
            synth, explicit=getattr(turn, "synth_mode", None),
        )
        _SILENT_SYNTH_MODES = {"compose", "compose-skipped-by-design"}
        if synth_mode not in _SILENT_SYNTH_MODES:
            log.warning(
                "synth_mode=%s turn_id=%s synth_error=%s",
                synth_mode,
                turn.turn_id or "?",
                getattr(synth, "error", None) if synth else "no-synth",
            )
        entry = TurnLogEntry(
            turn_id=turn.turn_id or "?",
            device_id=device_id, user_id=user_id, bot="hive",
            user_msg=text[:2000],
            planner_summary=(
                plan.output.get("summary", "")[:240]
                if plan and isinstance(plan.output, dict) else ""
            ),
            planner_raw_preview=_preview(getattr(plan, "raw_text", "")) if plan else "",
            planner_error=getattr(plan, "error", None) if plan else None,
            delegations=[
                d.get("role", "?")
                for d in (plan.output.get("delegations") or [])
                if isinstance(d, dict)
            ] if (plan and isinstance(plan.output, dict)) else [],
            helpers=helper_entries_from_results(all_helpers),
            synth_reply=(
                synth.output.get("reply", "")[:500]
                if synth and isinstance(synth.output, dict) else ""
            ),
            synth_raw_preview=_preview(getattr(synth, "raw_text", "")) if synth else "",
            synth_error=getattr(synth, "error", None) if synth else None,
            synth_mode=synth_mode,
            actions=list(turn.actions),
            receipts=list(turn.receipts),
            final_reply=turn.reply[:1000],
            blocked=turn.blocked,
            total_tokens=turn.total_tokens,
            total_latency_ms=turn.total_latency_ms,
        )
        await log_store.append_async(entry)
    except Exception as e:  # noqa: BLE001
        log.warning("turn-log append failed: %s", e)


async def publish_turn_done_notifications(
    app_state: Any, turn: Any, *, device_id: str,
) -> None:
    """Fan-out: in-process EventBus + external ntfy. Skip on blocked
    / errored / empty turns — those are noise. ntfy stays at priority
    2 so chat doesn't hammer the phone."""
    if turn.blocked or turn.error is not None:
        return
    if not turn.reply or len(turn.reply) <= 8:
        return
    bus = app_state.event_bus
    if bus is not None:
        try:
            bus.publish({
                "type": "hive_turn_done",
                "device_id": device_id,
                "preview": turn.reply[:200],
            })
        except Exception as e:  # noqa: BLE001
            log.warning("event_bus publish failed: %s", e)
    ntfy = app_state.ntfy
    if ntfy is not None and getattr(ntfy, "enabled", False):
        try:
            await ntfy.publish(
                topic="ai-team-chat",
                title="Hive replied",
                message=turn.reply[:200],
                tags=["speech_balloon"],
                priority=2,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("ntfy chat-done publish failed: %s", e)


def schedule_summarizer_refresh(
    app_state: Any, turn: Any, *, user_id: int, text: str,
    thread_id: str = "default",
) -> None:
    """Kick off the async memory-summary refresh when this turn
    crossed the threshold. Tracked via deps.track_background_task so
    lifespan shutdown drains it."""
    memory_store = app_state.memory_store_hive
    if memory_store is None:
        return
    try:
        from gateway.conversation_memory import refresh_summary_async
        helpers = app_state.helpers
        summarizer = helpers.get("summarizer") if helpers else None
        fact_extractor = helpers.get("fact_extractor") if helpers else None
        memory_store.increment_turn(user_id, thread_id)
        mem_now = memory_store.get(user_id, thread_id)
        if not (memory_store.needs_refresh(mem_now) and summarizer is not None):
            return
        # Pull the rolling window from LLMClient instead of just the
        # current turn's pair. Without this the summarizer only ever
        # sees 2 messages and `mid_summary` never accumulates context
        # across the conversation — the HIGH-severity correctness bug
        # the 2026-04-29 architecture review flagged. Falls back to the
        # current pair if the LLM isn't reachable so tests stay green.
        recent_msgs: list[dict[str, str]] = []
        adapters = app_state.adapters or {}
        hive_adapter = adapters.get("hive")
        llm = getattr(hive_adapter, "_llm", None) if hive_adapter else None
        if llm is not None and hasattr(llm, "recent_messages"):
            try:
                recent_msgs = list(llm.recent_messages(user_id, limit=20))
            except Exception as e:  # noqa: BLE001
                log.warning("recent_messages fetch failed: %s", e)
                recent_msgs = []
        if not recent_msgs:
            recent_msgs = [{"role": "user", "content": text}]
            if turn.reply:
                recent_msgs.append({"role": "assistant", "content": turn.reply})
        track_background_task(
            app_state,
            asyncio.create_task(
                refresh_summary_async(
                    memory_store,
                    user_id=user_id,
                    thread_id=thread_id,
                    messages=recent_msgs,
                    summarizer_helper=summarizer,
                    fact_extractor_helper=fact_extractor,
                ),
                name=f"summary_refresh:{user_id}:{thread_id}",
            ),
        )
    except Exception as e:  # noqa: BLE001
        log.warning("summarizer schedule failed: %s", e)


def persist_hive_turn_history(
    app_state: Any, turn: Any, *, user_id: int, text: str,
) -> None:
    """Write the (user, assistant) pair to LLMClient history.

    Hive turns DON'T flow through `adapter.reply()` (which auto-
    records), so without this the history JSON for the user stays
    empty and the app's chat tab boots blank. Sits in the chat.py
    `finally` block so a mid-bridge WS disconnect still records the
    reply — the architect's #1 finding from the 2026-04-29 review.
    """
    if turn is None or not turn.reply or turn.blocked or turn.error:
        return
    try:
        adapters = app_state.adapters or {}
        hive_adapter = adapters.get("hive")
        llm = getattr(hive_adapter, "_llm", None) if hive_adapter else None
        if llm is not None and hasattr(llm, "record_turn"):
            llm.record_turn(user_id, text, turn.reply)
            app_state.last_turn_completed_at = time.time()
    except Exception as e:  # noqa: BLE001
        log.warning("hive history persist failed: %s", e)


def maybe_auto_title_thread(
    app_state: Any,
    *,
    bot: str,
    user_id: int,
    text: str,
    thread_id: str,
    trigger_turn: int = 3,
) -> None:
    """Phase 2.6: replace the heuristic chat_thread title with an
    LLM-generated short title once we have enough context.

    The thread is created with `title = first_user_message[:50]` (a
    cheap heuristic). Once the conversation has 3 turns, the
    summarizer can do meaningfully better — give a 2–6-word topic
    title that survives a sidebar glance.

    Fires exactly once per thread: at `mem.turn_count == trigger_turn`.
    Reads the per-thread turn counter on `MemoryStore`, which is
    incremented synchronously in `schedule_summarizer_refresh`, so
    this function MUST be called *after* `schedule_summarizer_refresh`
    in the route.

    Best-effort: every error path is a logged warning, never raised —
    the user-visible reply is the contract.
    """
    memory_store = app_state.memory_store_hive
    vc = app_state.vault_client
    if memory_store is None or vc is None:
        return
    helpers = app_state.helpers or {}
    summarizer = helpers.get("summarizer")
    if summarizer is None:
        return
    # Guard: if the user has manually renamed this thread (title_locked),
    # the auto-titler must not overwrite their choice.
    try:
        existing = vc.get_thread(thread_id) if hasattr(vc, "get_thread") else None
        if existing and existing.get("title_locked"):
            return
    except Exception as e:  # noqa: BLE001
        log.warning("auto_title: get_thread failed: %s", e)
        return
    try:
        mem = memory_store.get(user_id, thread_id)
    except Exception as e:  # noqa: BLE001
        log.warning("auto_title: memory get failed: %s", e)
        return
    if mem.turn_count != trigger_turn:
        return

    recent: list[dict[str, str]] = []
    adapters = app_state.adapters or {}
    hive_adapter = adapters.get("hive")
    llm = getattr(hive_adapter, "_llm", None) if hive_adapter else None
    if llm is not None and hasattr(llm, "recent_messages"):
        try:
            recent = list(llm.recent_messages(user_id, limit=10))
        except Exception as e:  # noqa: BLE001
            log.warning("auto_title: recent_messages failed: %s", e)
            recent = []
    if not recent:
        recent = [{"role": "user", "content": text}]

    try:
        track_background_task(
            app_state,
            asyncio.create_task(
                _generate_and_set_thread_title(
                    summarizer=summarizer, vault_client=vc,
                    thread_id=thread_id, messages=recent,
                ),
                name=f"auto_title:{thread_id}",
            ),
        )
    except Exception as e:  # noqa: BLE001
        log.warning("auto_title: schedule failed: %s", e)


async def _generate_and_set_thread_title(
    *,
    summarizer: Any,
    vault_client: Any,
    thread_id: str,
    messages: list[dict[str, str]],
) -> None:
    """Background helper: ask summarizer for a short title, set it.

    Runs the summarizer in `thread_title_mode` (see prompts/summarizer.md).
    The helper's `summary` field carries the generated title; the
    list fields stay empty in this mode."""
    from gateway.helpers.base import HelperTask

    task = HelperTask(
        role="summarizer",
        goal="generate a short title for this conversation",
        inputs={
            "messages": messages[-10:],
            "thread_title_mode": True,
        },
    )
    try:
        result = await summarizer.invoke(task)
    except Exception as e:  # noqa: BLE001
        log.warning("auto_title: summarizer raised: %s", e)
        return
    if result.error or not result.output:
        log.info("auto_title: summarizer error: %s", result.error)
        return
    title = str((result.output or {}).get("summary", "")).strip()
    if not title:
        log.info("auto_title: empty title returned")
        return
    # Trim to first 6 words / 60 chars and strip stray punctuation.
    title = " ".join(title.split()[:6])[:60].rstrip(".,!? ").strip('"\'')
    if not title:
        return
    try:
        await vault_client.thread_set_title(
            thread_id=thread_id, title=title,
        )
        log.info("auto_title: %s -> %r", thread_id, title)
    except Exception as e:  # noqa: BLE001
        log.warning("auto_title: thread_set_title failed: %s", e)


def index_hive_turn_to_chat_log(
    app_state: Any,
    turn: Any,
    *,
    user_id: int,
    text: str,
    bot: str = "hive",
    thread_id: str = "default",
) -> None:
    """Index the (user, assistant) pair into the vault's chat_log
    FTS5 table so "what did we say about X" works.

    Best-effort: a missing or unreachable daemon doesn't break the
    user-visible reply. Fires both legs as background tasks tracked
    via track_background_task so lifespan shutdown drains them.
    """
    if turn is None or not turn.reply or turn.blocked or turn.error:
        return
    vc = app_state.vault_client
    if vc is None or not hasattr(vc, "chat_log_append"):
        return
    turn_id = getattr(turn, "turn_id", None)
    try:
        if text:
            track_background_task(
                app_state,
                asyncio.create_task(
                    vc.chat_log_append(
                        bot=bot, user_id=user_id, role="user",
                        content=text, thread_id=thread_id,
                        turn_id=turn_id,
                    ),
                    name=f"chat_log_user:{user_id}",
                ),
            )
        track_background_task(
            app_state,
            asyncio.create_task(
                vc.chat_log_append(
                    bot=bot, user_id=user_id, role="assistant",
                    content=turn.reply, thread_id=thread_id,
                    turn_id=turn_id,
                ),
                name=f"chat_log_assistant:{user_id}",
            ),
        )
    except Exception as e:  # noqa: BLE001
        log.warning("chat_log index failed: %s", e)
