"""Tests for the M2.3 HiveCoordinator."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from gateway.event_emitter import ListEmitter
from gateway.helpers.base import Helper, HelperResult, HelperTask
from gateway.hive_coordinator import HiveCoordinator, TurnBudget, TurnContext
from gateway.model_catalog import load_catalog


# ---------------------------------------------------------------- fakes


class _FakeHelper:
    def __init__(self, role: str, model_id: str, output: dict, *,
                 plan: list[str] | None = None,
                 confidence: str = "high",
                 error: str | None = None,
                 sleep_s: float = 0.0) -> None:
        self.role = role
        self.model_id = model_id
        self._output = output
        self._plan = plan or []
        self._confidence = confidence
        self._error = error
        self._sleep_s = sleep_s
        self.invoked_with: list[HelperTask] = []

    async def invoke(self, task: HelperTask) -> HelperResult:
        self.invoked_with.append(task)
        if self._sleep_s:
            import asyncio
            await asyncio.sleep(self._sleep_s)
        return HelperResult(
            role=self.role, model_id=self.model_id,
            output=self._output, plan=self._plan,
            confidence=self._confidence,
            error=self._error,
            tokens_in=10, tokens_out=20,
            latency_ms=5,
            parent_id=task.parent_id,
        )


@pytest.fixture
def catalog():
    return load_catalog(
        Path(__file__).resolve().parents[2] / "config" / "model_catalog.yaml",
    )


@pytest.fixture
def base_ctx():
    return TurnContext(
        user_msg="hi terry",
        user_id=42, device_id="dev1",
        history_digest="", image_build=None,
        skills_digest="", available_helpers=[
            "planner", "researcher", "synthesizer", "critic",
        ],
    )


# ---------------------------------------------------------------- direct reply


@pytest.mark.asyncio
async def test_coordinator_direct_reply_skips_dispatch(catalog, base_ctx):
    helpers = {
        "planner": _FakeHelper(
            "planner", "qwen-7b",
            {"summary": "small talk", "direct_reply": "Hey!", "delegations": []},
        ),
        "synthesizer": _FakeHelper("synthesizer", "planner-qwen", {"reply": "x"}),
    }
    coord = HiveCoordinator(catalog, helpers)
    em = ListEmitter()
    turn = await coord.coordinate(base_ctx, em)
    assert turn.reply == "Hey!"
    assert turn.helpers_used == ["planner"]
    # Synthesizer should NOT have been called.
    assert helpers["synthesizer"].invoked_with == []
    # Events: thought + assistant.
    assert [e.type for e in em.events] == ["thought", "assistant"]


# ---------------------------------------------------------------- dispatch injects ctx fields


@pytest.mark.asyncio
async def test_dispatch_injects_user_id_and_thread_id(catalog):
    """_dispatch must inject user_id + thread_id (alongside bot) into helper
    inputs so that helpers like chat_recall can scope their queries without
    the planner having to know about TurnContext internals."""
    ctx = TurnContext(
        user_msg="what was the multiplication answer",
        user_id=12345, device_id="dev-test",
        bot="terry", thread_id="default",
        available_helpers=["planner", "chat_recall", "synthesizer"],
    )
    helpers = {
        "planner": _FakeHelper(
            "planner", "qwen-7b",
            {
                "summary": "look it up in chat log",
                "delegations": [
                    {
                        "role": "chat_recall",
                        "goal": "find multiplication answer",
                        "inputs": {"query": "what was the multiplication answer"},
                    },
                ],
            },
        ),
        "chat_recall": _FakeHelper(
            "chat_recall", "no-model",
            {"hits": [], "summary": "no hits"},
        ),
        "synthesizer": _FakeHelper(
            "synthesizer", "planner-qwen",
            {"reply": "I could not find that in the chat history."},
        ),
    }
    coord = HiveCoordinator(catalog, helpers)
    em = ListEmitter()
    await coord.coordinate(ctx, em)

    assert helpers["chat_recall"].invoked_with, "chat_recall was never called"
    received = helpers["chat_recall"].invoked_with[0].inputs
    assert received["user_id"] == 12345, (
        f"expected user_id=12345, got {received.get('user_id')!r}"
    )
    assert received["thread_id"] == "default", (
        f"expected thread_id='default', got {received.get('thread_id')!r}"
    )
    assert received["bot"] == "terry", (
        f"expected bot='terry', got {received.get('bot')!r}"
    )
    # Planner-supplied field must still be there.
    assert received["query"] == "what was the multiplication answer"


# ---------------------------------------------------------------- full pipeline


@pytest.mark.asyncio
async def test_coordinator_dispatches_then_synthesizes(catalog, base_ctx):
    base_ctx = TurnContext(
        user_msg="research the Drake Cutlass",
        user_id=42, device_id="dev1",
        available_helpers=["planner", "researcher", "synthesizer"],
    )
    helpers = {
        "planner": _FakeHelper(
            "planner", "qwen-7b",
            {
                "summary": "research it",
                "delegations": [
                    {"role": "researcher", "goal": "look up cutlass",
                     "inputs": {"topic": "Drake Cutlass"}, "risky": False},
                ],
            },
        ),
        "researcher": _FakeHelper(
            "researcher", "qwen-7b",
            {"summary": "found 3 facts", "facts": [{"claim": "It's a ship"}]},
        ),
        "synthesizer": _FakeHelper(
            "synthesizer", "planner-qwen",
            {"reply": "It's a Drake ship.",
             "actions": [{"verb": "vault_learn", "payload": {"title": "drake"}}]},
        ),
    }
    coord = HiveCoordinator(catalog, helpers)
    em = ListEmitter()
    turn = await coord.coordinate(base_ctx, em)
    assert turn.reply == "It's a Drake ship."
    assert turn.actions == [{"verb": "vault_learn", "payload": {"title": "drake"}}]
    assert "planner" in turn.helpers_used
    assert "researcher" in turn.helpers_used
    # Coordinator now injects `bot` into helper inputs so audience-aware
    # helpers (like librarian) can scope their queries.
    actual_inputs = helpers["researcher"].invoked_with[0].inputs
    assert actual_inputs["topic"] == "Drake Cutlass"
    assert actual_inputs["bot"] == "terry"

    types = [e.type for e in em.events]
    assert types == ["thought", "delegate", "helper_reply", "synthesis", "assistant"]


# ---------------------------------------------------------------- critic gate


@pytest.mark.asyncio
async def test_coordinator_critic_blocks_risky_action(catalog, base_ctx):
    helpers = {
        "planner": _FakeHelper(
            "planner", "qwen-7b",
            {
                "summary": "write to vault",
                "delegations": [
                    {"role": "researcher", "goal": "x", "inputs": {},
                     "risky": True},
                ],
            },
        ),
        "researcher": _FakeHelper(
            "researcher", "qwen-7b",
            {"summary": "found something"},
        ),
        "critic": _FakeHelper(
            "critic", "qwen-7b",
            {"block": True, "reason": "doesn't match user intent"},
        ),
        "synthesizer": _FakeHelper(
            "synthesizer", "planner-qwen", {"reply": "should not appear"},
        ),
    }
    coord = HiveCoordinator(catalog, helpers)
    em = ListEmitter()
    turn = await coord.coordinate(base_ctx, em)
    assert turn.blocked is True
    assert "doesn't match user intent" in turn.reply
    # Synthesizer should NOT have run.
    assert helpers["synthesizer"].invoked_with == []


# ---------------------------------------------------------------- vram fallback


@pytest.mark.asyncio
async def test_coordinator_routes_to_cpu_when_vram_full(catalog, base_ctx):
    """Two large helpers with budget=14000 — the second should get use_cpu=True."""
    base_ctx = TurnContext(
        user_msg="x", user_id=1, device_id="dev1",
        available_helpers=["planner", "researcher", "coder", "synthesizer"],
    )
    helpers = {
        "planner": _FakeHelper(
            "planner", "qwen-7b",
            {
                "summary": "two big helpers",
                "delegations": [
                    {"role": "researcher", "goal": "r", "inputs": {}},
                    {"role": "coder", "goal": "c", "inputs": {}},
                ],
            },
        ),
        "researcher": _FakeHelper("researcher", "qwen-7b",
                                  {"summary": "r"}, sleep_s=0.05),
        "coder": _FakeHelper("coder", "qwen-coder-7b",
                             {"summary": "c"}, sleep_s=0.05),
        "synthesizer": _FakeHelper("synthesizer", "planner-qwen", {"reply": "ok"}),
    }
    # Budget 8000mb means only one ~5500mb model can be on GPU at a time.
    budget = TurnBudget(vram_budget_mb=8000)
    coord = HiveCoordinator(catalog, helpers, budget=budget)
    em = ListEmitter()
    await coord.coordinate(base_ctx, em)
    # At least one of the two should have been routed to CPU.
    cpu_uses = [
        t.use_cpu for h in (helpers["researcher"], helpers["coder"])
        for t in h.invoked_with
    ]
    assert any(cpu_uses), "expected at least one helper to fall back to CPU"


# ---------------------------------------------------------------- planner failure


@pytest.mark.asyncio
async def test_coordinator_fallback_when_planner_fails(catalog, base_ctx):
    helpers = {
        "planner": _FakeHelper(
            "planner", "qwen-7b", {}, error="planner blew up",
        ),
    }
    coord = HiveCoordinator(catalog, helpers)
    em = ListEmitter()
    turn = await coord.coordinate(base_ctx, em)
    # Coordinator now propagates the actual error message into
    # AssistantTurn.error so turn-logs can see it.
    assert turn.error == "planner blew up"
    assert "rephrase" in turn.reply.lower() or "trouble" in turn.reply.lower()
    types = [e.type for e in em.events]
    assert "thought" in types
    assert "assistant" in types
    # The failed planner result is preserved for the turn-log path.
    assert turn.planner_result is not None
    assert turn.planner_result.error == "planner blew up"


# ---------------------------------------------------------------- synth failure


@pytest.mark.asyncio
async def test_coordinator_preserves_synth_error_in_turn(catalog, base_ctx):
    """Regression: when the synthesizer LLM emits unparseable JSON,
    the coordinator must (1) fall back to a composed reply and
    (2) keep the failed HelperResult on AssistantTurn.synth_result so
    the turn-log captures the error + raw_text. Pre-fix the synth
    result was discarded (returned as None), losing diagnostic info."""
    helpers = {
        "planner": _FakeHelper(
            "planner", "qwen-7b",
            {
                "summary": "use researcher",
                "delegations": [{"role": "researcher", "goal": "x", "inputs": {}}],
            },
        ),
        "researcher": _FakeHelper(
            "researcher", "qwen-7b",
            {"summary": "got facts", "facts": ["fact-1"]},
            confidence="high",
        ),
        "synthesizer": _FakeHelper(
            "synthesizer", "planner-qwen", {},
            error="empty response",
        ),
    }
    coord = HiveCoordinator(catalog, helpers)
    em = ListEmitter()
    turn = await coord.coordinate(base_ctx, em)
    assert turn.error is not None
    assert "empty response" in turn.error
    assert turn.synth_result is not None
    assert turn.synth_result.error == "empty response"
    # Fallback reply must not leak helper role names or summaries to the
    # user — that surface confused users in 04-26..05-01 production data.
    assert turn.reply
    reply_lc = turn.reply.lower()
    assert "researcher" not in reply_lc
    assert "got facts" not in reply_lc
    assert "outputs are below" not in reply_lc
    assert "- " not in turn.reply  # no helper-summary list markers


# ---------------------------------------------------------------- chat_recall pipeline


@pytest.mark.asyncio
async def test_chat_recall_hits_visible_to_synthesizer(catalog):
    """When chat_recall returns hits, the synthesizer task input
    helper_results must carry them through. This is the whole point
    of chat_recall existing — proves the planner→recall→synth handoff."""
    ctx = TurnContext(
        user_msg="what was the multiplication answer earlier",
        user_id=42, device_id="dev1",
        bot="terry", thread_id="default",
        available_helpers=["planner", "chat_recall", "synthesizer"],
    )
    helpers = {
        "planner": _FakeHelper(
            "planner", "qwen-7b",
            {
                "summary": "look up earlier turn",
                "delegations": [
                    {"role": "chat_recall",
                     "goal": "find multiplication answer",
                     "inputs": {"query": "multiplication answer"}},
                ],
            },
        ),
        "chat_recall": _FakeHelper(
            "chat_recall", "no-model",
            {
                "summary": "1 chat-log hit",
                "hits": [
                    {"role": "assistant",
                     "content": "17 times 23 is 391.",
                     "thread_id": "default", "created_at": 1_000},
                ],
            },
        ),
        "synthesizer": _FakeHelper(
            "synthesizer", "planner-qwen",
            {"reply": "The answer was 391."},
        ),
    }
    coord = HiveCoordinator(catalog, helpers)
    em = ListEmitter()
    turn = await coord.coordinate(ctx, em)

    assert turn.reply == "The answer was 391."
    assert helpers["synthesizer"].invoked_with, "synth never called"
    synth_inputs = helpers["synthesizer"].invoked_with[0].inputs
    helper_results_blob = str(synth_inputs.get("helper_results", ""))
    assert "391" in helper_results_blob, (
        "synthesizer must see chat_recall content; got: "
        f"{helper_results_blob[:200]!r}"
    )


@pytest.mark.asyncio
async def test_chat_recall_zero_hits_does_not_block_turn(catalog):
    """Empty chat_recall result is a normal outcome (no prior turn
    matched the query). The pipeline must still synthesize a reply
    rather than falling into the fallback path."""
    ctx = TurnContext(
        user_msg="what was the codeword",
        user_id=42, device_id="dev1",
        bot="terry", thread_id="default",
        available_helpers=["planner", "chat_recall", "synthesizer"],
    )
    helpers = {
        "planner": _FakeHelper(
            "planner", "qwen-7b",
            {
                "summary": "look up codeword",
                "delegations": [
                    {"role": "chat_recall", "goal": "find codeword",
                     "inputs": {"query": "codeword"}},
                ],
            },
        ),
        "chat_recall": _FakeHelper(
            "chat_recall", "no-model",
            {"summary": "no chat-log hits", "hits": []},
            confidence="low",
        ),
        "synthesizer": _FakeHelper(
            "synthesizer", "planner-qwen",
            {"reply": "I do not recall a codeword."},
        ),
    }
    coord = HiveCoordinator(catalog, helpers)
    em = ListEmitter()
    turn = await coord.coordinate(ctx, em)

    assert turn.error is None
    assert turn.reply == "I do not recall a codeword."
    assert helpers["chat_recall"].invoked_with
    assert helpers["synthesizer"].invoked_with


@pytest.mark.asyncio
async def test_chat_recall_error_propagates_to_synthesizer(catalog):
    """If chat_recall errors (vault disk full, daemon down, schema
    mismatch...), the turn must continue. The synthesizer needs to
    see the error so it can apologize / change tack rather than
    inventing data."""
    ctx = TurnContext(
        user_msg="what did we discuss yesterday",
        user_id=42, device_id="dev1",
        bot="terry", thread_id="default",
        available_helpers=["planner", "chat_recall", "synthesizer"],
    )
    helpers = {
        "planner": _FakeHelper(
            "planner", "qwen-7b",
            {
                "summary": "recall yesterday",
                "delegations": [
                    {"role": "chat_recall", "goal": "yesterday",
                     "inputs": {"query": "yesterday"}},
                ],
            },
        ),
        "chat_recall": _FakeHelper(
            "chat_recall", "no-model",
            {"summary": "search error: disk full", "hits": []},
            error="search error: disk full",
            confidence="low",
        ),
        "synthesizer": _FakeHelper(
            "synthesizer", "planner-qwen",
            {"reply": "I can't access chat history right now."},
        ),
    }
    coord = HiveCoordinator(catalog, helpers)
    em = ListEmitter()
    turn = await coord.coordinate(ctx, em)

    assert turn.reply == "I can't access chat history right now."
    # helper_results must include the chat_recall entry with its error.
    chat_recall_results = [r for r in turn.helper_results
                           if r.role == "chat_recall"]
    assert len(chat_recall_results) == 1
    assert chat_recall_results[0].error == "search error: disk full"


@pytest.mark.asyncio
async def test_chat_recall_alongside_researcher_both_reach_synth(catalog):
    """Multi-helper dispatch: chat_recall + researcher fanned out together,
    BOTH outputs must arrive at the synthesizer's helper_results so it
    can blend a 'here's what we said before AND here's what's online' reply."""
    ctx = TurnContext(
        user_msg="what did we say about the kraken and what's it actually for",
        user_id=42, device_id="dev1",
        bot="terry", thread_id="default",
        available_helpers=["planner", "chat_recall", "researcher", "synthesizer"],
    )
    helpers = {
        "planner": _FakeHelper(
            "planner", "qwen-7b",
            {
                "summary": "blend recall + research",
                "delegations": [
                    {"role": "chat_recall",
                     "goal": "find kraken discussion",
                     "inputs": {"query": "kraken"}},
                    {"role": "researcher", "goal": "kraken capabilities",
                     "inputs": {"topic": "Drake Kraken"}},
                ],
            },
        ),
        "chat_recall": _FakeHelper(
            "chat_recall", "no-model",
            {"summary": "1 chat-log hit",
             "hits": [{"role": "user", "content": "MARKER_RECALL_KRAKEN",
                       "thread_id": "default", "created_at": 1}]},
        ),
        "researcher": _FakeHelper(
            "researcher", "qwen-7b",
            {"summary": "MARKER_RESEARCH_KRAKEN: drake capital ship",
             "facts": ["capital ship"]},
        ),
        "synthesizer": _FakeHelper(
            "synthesizer", "planner-qwen",
            {"reply": "blended reply"},
        ),
    }
    coord = HiveCoordinator(catalog, helpers)
    em = ListEmitter()
    turn = await coord.coordinate(ctx, em)

    assert turn.reply == "blended reply"
    synth_inputs = helpers["synthesizer"].invoked_with[0].inputs
    blob = str(synth_inputs.get("helper_results", ""))
    assert "MARKER_RECALL_KRAKEN" in blob, "chat_recall hit lost"
    assert "MARKER_RESEARCH_KRAKEN" in blob, "researcher output lost"
    # Both helpers ran exactly once.
    assert len(helpers["chat_recall"].invoked_with) == 1
    assert len(helpers["researcher"].invoked_with) == 1


@pytest.mark.asyncio
async def test_dispatch_injects_zero_user_id_correctly(catalog):
    """user_id=0 is a legitimate value (some tests use it; the owner
    user_id is derived from a stable hash, but 0 should still inject).
    Catches the bug where 'if user_id:' would falsy-filter zero."""
    ctx = TurnContext(
        user_msg="recall something",
        user_id=0, device_id="dev1",
        bot="terry", thread_id="default",
        available_helpers=["planner", "chat_recall", "synthesizer"],
    )
    helpers = {
        "planner": _FakeHelper(
            "planner", "qwen-7b",
            {
                "summary": "recall",
                "delegations": [
                    {"role": "chat_recall", "goal": "x",
                     "inputs": {"query": "anything"}},
                ],
            },
        ),
        "chat_recall": _FakeHelper(
            "chat_recall", "no-model",
            {"summary": "no hits", "hits": []},
        ),
        "synthesizer": _FakeHelper(
            "synthesizer", "planner-qwen", {"reply": "ok"},
        ),
    }
    coord = HiveCoordinator(catalog, helpers)
    em = ListEmitter()
    await coord.coordinate(ctx, em)

    received = helpers["chat_recall"].invoked_with[0].inputs
    assert "user_id" in received, "user_id=0 was filtered out"
    assert received["user_id"] == 0


# ---------------------------------------------------------------- emitter shape


@pytest.mark.asyncio
async def test_event_emitter_records_helper_reply_summary(catalog, base_ctx):
    helpers = {
        "planner": _FakeHelper(
            "planner", "qwen-7b",
            {
                "summary": "use researcher",
                "delegations": [{"role": "researcher", "goal": "x", "inputs": {}}],
            },
        ),
        "researcher": _FakeHelper(
            "researcher", "qwen-7b",
            {"summary": "got it", "facts": []},
            confidence="high",
        ),
        "synthesizer": _FakeHelper("synthesizer", "planner-qwen", {"reply": "done"}),
    }
    coord = HiveCoordinator(catalog, helpers)
    em = ListEmitter()
    await coord.coordinate(base_ctx, em)
    helper_replies = [e for e in em.events if e.type == "helper_reply"]
    assert len(helper_replies) == 1
    p = helper_replies[0].payload
    assert p["role"] == "researcher"
    assert p["output_summary"] == "got it"
    assert p["confidence"] == "high"
    assert p["error"] is None


# ---------------------------------------------------------------- hallucination guard


def test_hallucination_guard_keeps_clean_reply():
    """Sentences with no numbers or with numbers that trace pass through."""
    from gateway.hallucination_guard import strip_hallucinated_sentences as _strip_hallucinated_sentences
    from gateway.helpers.base import HelperResult
    helpers = [
        HelperResult(
            role="researcher", model_id="x",
            output={"facts": [{"claim": "Cutlass holds 46 SCU cargo"}]},
            raw_text='{"facts":[{"claim":"Cutlass holds 46 SCU cargo"}]}',
        )
    ]
    reply = "The Drake Cutlass holds 46 SCU. It's a multi-role ship."
    out = _strip_hallucinated_sentences(reply, helpers)
    # Both sentences should survive — first traces, second has no numbers.
    assert "46 SCU" in out
    assert "multi-role" in out


def test_hallucination_guard_drops_untraced_sentence():
    """Sentence with a specific number not in helpers gets dropped."""
    from gateway.hallucination_guard import strip_hallucinated_sentences as _strip_hallucinated_sentences
    from gateway.helpers.base import HelperResult
    helpers = [
        HelperResult(
            role="librarian", model_id="x",
            output={"hits": [{"path": "x.md", "excerpt": "Drake Cutlass info"}]},
            raw_text="Drake Cutlass info",
        )
    ]
    reply = "It's a Drake Cutlass. The top speed is 1200 m/s."
    out = _strip_hallucinated_sentences(reply, helpers)
    assert "1200" not in out
    assert "Drake Cutlass" in out


def test_hallucination_guard_normalises_numbers():
    """1,200 in reply matches 1200 in helper output."""
    from gateway.hallucination_guard import strip_hallucinated_sentences as _strip_hallucinated_sentences
    from gateway.helpers.base import HelperResult
    helpers = [
        HelperResult(
            role="researcher", model_id="x",
            output={"facts": [{"claim": "top speed 1200 m/s"}]},
            raw_text="top speed 1200 m/s",
        )
    ]
    reply = "Top speed is 1,200 m/s."
    out = _strip_hallucinated_sentences(reply, helpers)
    assert "1,200" in out


def test_hallucination_guard_preserves_small_numbers():
    """A '2' in 'crew of 2' shouldn't fail trace — too short to be specific."""
    from gateway.hallucination_guard import strip_hallucinated_sentences as _strip_hallucinated_sentences
    from gateway.helpers.base import HelperResult
    helpers = [
        HelperResult(
            role="librarian", model_id="x",
            output={"hits": [{"path": "x.md", "excerpt": "Cutlass crew"}]},
            raw_text="Cutlass crew",
        )
    ]
    reply = "The Cutlass has a crew of 2."
    out = _strip_hallucinated_sentences(reply, helpers)
    assert "crew of 2" in out


def test_hallucination_guard_returns_original_when_all_dropped():
    """If every sentence would be filtered, fall back to the original
    rather than returning blank."""
    from gateway.hallucination_guard import strip_hallucinated_sentences as _strip_hallucinated_sentences
    from gateway.helpers.base import HelperResult
    helpers = [
        HelperResult(role="r", model_id="x", output={}, raw_text=""),
    ]
    reply = "Top speed 999. Cargo 888 SCU."
    out = _strip_hallucinated_sentences(reply, helpers)
    # Both untraced — guard returns original.
    assert out == reply


# ---------------------------------------------------------------- audit fixes


def test_hallucination_guard_skips_refusal_sentence():
    """Sentences with refusal/safety keywords pass through even if
    they have untraced numbers — the guard mustn't strip 'call 911'."""
    from gateway.hallucination_guard import strip_hallucinated_sentences as _strip_hallucinated_sentences
    from gateway.helpers.base import HelperResult
    helpers = [HelperResult(role="x", model_id="x", output={}, raw_text="")]
    reply = "I can't help with that. If this is urgent please call 911."
    out = _strip_hallucinated_sentences(reply, helpers)
    assert "911" in out
    assert "can't" in out


def test_hallucination_guard_skips_rate_limit_warning():
    from gateway.hallucination_guard import strip_hallucinated_sentences as _strip_hallucinated_sentences
    from gateway.helpers.base import HelperResult
    helpers = [HelperResult(role="x", model_id="x", output={}, raw_text="")]
    reply = "Rate limit exceeded — wait 300 seconds."
    out = _strip_hallucinated_sentences(reply, helpers)
    # Refusal-keyword carve-out: "Rate limit" keeps the sentence.
    assert "300" in out


# ---------------------------------------------------------------- action-claim


def test_hallucination_guard_strips_smart_link_when_no_vault_learn():
    """Smart-link claim with no vault_learn action emitted is a lie —
    auto-linking only happens as part of vault_learn."""
    from gateway.hallucination_guard import strip_hallucinated_sentences as _strip_hallucinated_sentences
    from gateway.helpers.base import HelperResult
    helpers = [HelperResult(role="x", model_id="x", output={}, raw_text="")]
    reply = (
        "Sure, I researched it. "
        "I've smart-linked the related pages so everything's connected. "
        "Want me to dig deeper?"
    )
    out = _strip_hallucinated_sentences(reply, helpers, actions=[])
    assert "smart-link" not in out.lower()
    assert "dig deeper" in out


def test_hallucination_guard_keeps_smart_link_claim_with_vault_learn():
    """vault_learn now auto-links — claims like 'I smart-linked' are
    legitimate when vault_learn was emitted."""
    from gateway.hallucination_guard import strip_hallucinated_sentences as _strip_hallucinated_sentences
    from gateway.helpers.base import HelperResult
    helpers = [HelperResult(role="x", model_id="x", output={}, raw_text="")]
    reply = "I saved it. I smart-linked the related pages."
    actions = [{"verb": "vault_learn", "payload": {}}]
    out = _strip_hallucinated_sentences(reply, helpers, actions)
    assert "smart-link" in out.lower()
    assert "saved" in out


def test_hallucination_guard_strips_save_claim_without_verb():
    """Reply claims 'saved to the vault' but no vault_learn action was
    emitted — synthesizer is lying."""
    from gateway.hallucination_guard import strip_hallucinated_sentences as _strip_hallucinated_sentences
    from gateway.helpers.base import HelperResult
    helpers = [HelperResult(role="x", model_id="x", output={}, raw_text="")]
    reply = "Got it. I've saved this to the vault."
    out = _strip_hallucinated_sentences(reply, helpers, actions=[])
    assert "saved" not in out.lower()
    assert "Got it" in out


def test_hallucination_guard_keeps_save_claim_with_verb():
    """Same claim, but vault_learn IS in actions — leave it alone."""
    from gateway.hallucination_guard import strip_hallucinated_sentences as _strip_hallucinated_sentences
    from gateway.helpers.base import HelperResult
    helpers = [HelperResult(role="x", model_id="x", output={}, raw_text="")]
    reply = "Got it. I've saved this to the vault."
    actions = [{"verb": "vault_learn", "payload": {}}]
    out = _strip_hallucinated_sentences(reply, helpers, actions)
    assert "saved this to the vault" in out.lower()


def test_hallucination_guard_strips_cross_reference_claim_without_vault_learn():
    """'cross-referenced' is treated as a smart-link claim — only valid
    when vault_learn was actually emitted (auto-linker did it)."""
    from gateway.hallucination_guard import strip_hallucinated_sentences as _strip_hallucinated_sentences
    from gateway.helpers.base import HelperResult
    helpers = [HelperResult(role="x", model_id="x", output={}, raw_text="")]
    reply = "Done. I cross-referenced the new note with three others."
    out = _strip_hallucinated_sentences(reply, helpers, actions=[])
    assert "cross-referenc" not in out.lower()
    assert "Done" in out


# ---------------------------------------------------------------- router wiring


def _make_catalog_with_two_candidates(role: str, model_a_id: str, model_b_id: str):
    """Return a ModelCatalog with two ModelEntries and one HelperEntry.

    Both models are listed as candidates for `role`.  Caller decides which
    has better bench scores.
    """
    from gateway.model_catalog import ModelCatalog, ModelEntry, HelperEntry

    model_a = ModelEntry(
        id=model_a_id,
        ollama_name=f"{model_a_id}:latest",
        family="test",
        gpu_vram_mb=4000,
    )
    model_b = ModelEntry(
        id=model_b_id,
        ollama_name=f"{model_b_id}:latest",
        family="test",
        gpu_vram_mb=4000,
    )
    helper_entry = HelperEntry(
        role=role,
        model=model_a_id,
        system_prompt_file="",
        output_schema="",
        timeout_s=30,
        candidates=(model_a_id, model_b_id),
    )
    return ModelCatalog(
        models={model_a_id: model_a, model_b_id: model_b},
        helpers={role: helper_entry},
    )


def test_model_for_consults_router_when_present():
    """When a Router is wired in, _model_for should return the ollama_name
    of the highest-scoring candidate, not just the catalog default."""
    from gateway.orchestrator.bench_results import BenchResults, BenchScore
    from gateway.orchestrator.router import Router

    role = "planner"
    model_a_id = "model-weak"
    model_b_id = "model-strong"

    catalog = _make_catalog_with_two_candidates(role, model_a_id, model_b_id)

    # model-strong wins: quality 0.95 vs 0.50, same latency + cost.
    results = BenchResults(scores={
        role: {
            model_a_id: BenchScore(
                latency_p50_ms=300.0,
                tokens_per_s=40.0,
                quality_score=0.50,
                cost_per_1k_tokens=0.0,
                last_run_at=1_000.0,
            ),
            model_b_id: BenchScore(
                latency_p50_ms=300.0,
                tokens_per_s=40.0,
                quality_score=0.95,
                cost_per_1k_tokens=0.0,
                last_run_at=1_000.0,
            ),
        }
    })

    router = Router(catalog=catalog, results=results)
    coord = HiveCoordinator(catalog, helpers={}, router=router)

    model_str = coord._model_for(role)

    assert model_str == f"{model_b_id}:latest", (
        f"Expected router to pick {model_b_id}:latest, got {model_str!r}"
    )


def test_model_for_falls_back_to_catalog_when_router_is_none():
    """Without a router, _model_for returns the catalog default model's
    ollama_name exactly as before."""
    role = "planner"
    model_a_id = "model-weak"
    model_b_id = "model-strong"

    catalog = _make_catalog_with_two_candidates(role, model_a_id, model_b_id)

    # No router — legacy path must still work.
    coord = HiveCoordinator(catalog, helpers={})

    model_str = coord._model_for(role)

    # Catalog default is model_a_id (listed first in HelperEntry.model).
    assert model_str == f"{model_a_id}:latest", (
        f"Expected catalog default {model_a_id}:latest, got {model_str!r}"
    )


# ---------------------------------------------------------------- VRAM-aware cap integration


def test_resolve_helper_cap_uses_live_max_when_not_gaming(monkeypatch, catalog):
    """_resolve_helper_cap() must honour TurnBudget.live_max_concurrent().

    When vram_provider returns 0 free VRAM and gaming is not detected,
    the cap should be 1 (floor imposed by live_max_concurrent), not the
    static max_concurrent_helpers of 5.
    """
    monkeypatch.setattr(
        "gateway.hive_coordinator._gaming_on_gpu0", lambda: False,
    )
    budget = TurnBudget(
        max_concurrent_helpers=5,
        vram_provider=lambda: 0,
        helper_vram_estimate_mb=4000,
    )
    coord = HiveCoordinator(catalog, helpers={}, budget=budget)
    cap, gaming = coord._resolve_helper_cap()
    assert cap == 1
    assert gaming is False


def test_turn_budget_has_synth_gate_default_30s():
    """Phase B.1 (#476): synth-on-ready gate replaces wait_for hard
    cancellation. 30s default — typical helper p50 ~12s, p99 ~25s on
    GPU; CPU-resident gemma3-4b p50 ~22s. 30s lets either return
    before the gate fires; longer outliers detach as background tasks."""
    budget = TurnBudget()
    assert budget.synth_gate_s == 30.0


def test_coordinator_tracks_late_helper_tasks(catalog):
    """Phase B.4 (#476): coordinator owns the set of detached late
    helpers so shutdown can drain them and tests can assert detachment.
    """
    coord = HiveCoordinator(catalog, helpers={})
    assert hasattr(coord, "_late_helper_tasks")
    assert isinstance(coord._late_helper_tasks, set)
    assert len(coord._late_helper_tasks) == 0


@pytest.mark.asyncio
async def test_coordinator_drain_late_tasks_no_op_when_empty(catalog):
    """_drain_late_tasks must be safe to call when nothing detached."""
    coord = HiveCoordinator(catalog, helpers={})
    await coord._drain_late_tasks(timeout=1.0)  # must not raise / hang
    assert len(coord._late_helper_tasks) == 0


# ---------------------------------------------------------------- write-intent override (sc-kb run)

def test_write_intent_detection_matches_save_phrasings():
    from gateway.hive_coordinator import _looks_like_write_intent
    save_phrasings = [
        "save this to my vault",
        "save that as a note",
        "save the answer",
        "remember this for later",
        "remember the codeword penguin-glacier",
        "note that the dev port is 2949",
        "add this to vault",
        "add it to my vault",
        "store this in the vault",
        "write a note about Star Citizen",
        "forget that note about the password",
        "render an image of a forest",
        "generate an image of a ship",
        "Now save a note 'UEE — United Empire of Earth'",
    ]
    for p in save_phrasings:
        assert _looks_like_write_intent(p), f"expected write-intent: {p!r}"


def test_write_intent_detection_skips_innocuous_messages():
    from gateway.hive_coordinator import _looks_like_write_intent
    for p in [
        "what was the multiplication answer",
        "hello terry",
        "what is star citizen",
        "tell me about UEE",
        "summarise what we talked about",
    ]:
        assert not _looks_like_write_intent(p), f"false positive on: {p!r}"


@pytest.mark.asyncio
async def test_direct_reply_overridden_when_user_asks_to_save(catalog, base_ctx):
    """SC-kb regression: planner sometimes ignores Rule 11 and emits
    a direct_reply for save intents. Coordinator must override and
    fall through to dispatch+synth so vault_learn CAN be emitted."""
    ctx = TurnContext(
        user_msg="Save a note 'X' to the vault under category knowledge.",
        user_id=1, device_id="dev1",
        available_helpers=["planner", "synthesizer"],
    )
    helpers = {
        "planner": _FakeHelper(
            "planner", "qwen-7b",
            {
                "summary": "user wants a save",
                "direct_reply": "Saved!",   # planner being lazy
                "delegations": [],
            },
        ),
        "synthesizer": _FakeHelper(
            "synthesizer", "planner-qwen",
            {"reply": "Done", "actions": [{"verb": "vault_learn"}]},
        ),
    }
    coord = HiveCoordinator(catalog, helpers)
    em = ListEmitter()
    turn = await coord.coordinate(ctx, em)
    # Synthesizer ran (the override worked).
    assert helpers["synthesizer"].invoked_with, (
        "expected synthesizer to be invoked because user asked to save"
    )
    # Reply came from synth, not the lazy direct_reply.
    assert turn.reply != "Saved!"


def test_derive_save_action_dash_form():
    """SC-kb regression: synth produces prose like 'Saved as `x.md`' but
    emits no vault_learn. Deterministic parser must extract title+body
    from the user's 'Save X — Y' message and synthesise the action."""
    from gateway.hive_coordinator import _derive_save_action_from_user
    msg = (
        "Save 'Hurston' — first super-Earth in Stanton, owned by Hurston "
        "Dynamics, capital city Lorville, moons Aberdeen, Arial, Magda, Ita."
    )
    action = _derive_save_action_from_user(msg, [])
    assert action is not None
    assert action["verb"] == "vault_learn"
    assert action["payload"]["category"] == "knowledge"
    assert action["payload"]["title"] == "Hurston"
    assert "Stanton" in action["payload"]["body"]
    assert "star-citizen" in action["payload"]["tags"]


def test_derive_save_action_covering_form():
    """The first SC turn uses 'covering' instead of an em-dash. Parser
    must handle both."""
    from gateway.hive_coordinator import _derive_save_action_from_user
    msg = (
        "Now save a note 'UEE — United Empire of Earth' covering the "
        "dominant human government, its capital Earth, the Messer era, "
        "and the modern Imperator system."
    )
    action = _derive_save_action_from_user(msg, [])
    assert action is not None
    assert action["payload"]["title"] == "UEE — United Empire of Earth"
    assert "Messer" in action["payload"]["body"]


def test_derive_save_action_skips_when_synth_emitted_one():
    """No double-write: if synth already emitted a COMPLETE vault_learn
    (with title AND body), don't add another."""
    from gateway.hive_coordinator import _derive_save_action_from_user
    existing = [{
        "verb": "vault_learn",
        "payload": {"title": "X", "body": "some adequately-sized body content."},
    }]
    msg = "Save 'Y' — some body content that is long enough."
    assert _derive_save_action_from_user(msg, existing) is None


def test_derive_save_action_overrides_synth_stub():
    """SC-kb regression: synth sometimes emits a vault_learn with only
    a slug field. The executor rejects it ('missing category/title/
    body'). Derive must still fire so the user's save isn't lost."""
    from gateway.hive_coordinator import _derive_save_action_from_user
    # Stub that would fail in the executor.
    stub = [{"verb": "vault_learn", "payload": {"slug": "ship-role-cargo"}}]
    msg = (
        "Save 'Ship role — Cargo hauling' — small (Cutlass Black, "
        "Freelancer), medium (Caterpillar, C2 Hercules), large (Hull C, "
        "Hull D, M2 Hercules)."
    )
    action = _derive_save_action_from_user(msg, stub)
    assert action is not None, "stub vault_learn should NOT block derive"
    assert "Cargo" in action["payload"]["title"]


def test_derive_save_action_skips_without_write_intent():
    """Non-save messages don't trigger, even if they have quoted text."""
    from gateway.hive_coordinator import _derive_save_action_from_user
    msg = "What is 'Hurston' — the planet in Stanton with several moons."
    assert _derive_save_action_from_user(msg, []) is None


def test_derive_save_action_handles_apostrophe_in_double_quoted_title():
    """SC-kb regression: 'Faction — Xi\\'an' has an apostrophe inside
    the title. Earlier regex `[^'\"`]+` rejected ALL quote chars inside
    the title and failed to parse this kind of name. Same-style pair
    matching fixes it."""
    from gateway.hive_coordinator import _derive_save_action_from_user
    msg = (
        'Save "Faction — Xi\'an" — diplomatic alien race, '
        "technologically advanced, ships Khartu-al and Nox "
        "influenced by their tech."
    )
    action = _derive_save_action_from_user(msg, [])
    assert action is not None, "double-quoted title with apostrophe inside failed to parse"
    assert "Xi'an" in action["payload"]["title"]
    assert "alien" in action["payload"]["body"]


def test_derive_save_action_prepends_title_when_body_short():
    """Body of 65 chars would fail the vault_quality MIN_BODY_CHARS=80
    gate. Prepending the title makes the save land while preserving the
    user's phrasing."""
    from gateway.hive_coordinator import _derive_save_action_from_user
    msg = (
        "Save 'Origin Jumpworks' — luxury manufacturer, ships include "
        "300-series, 600i, 890 Jump, 100-series, 400i, X1."
    )
    action = _derive_save_action_from_user(msg, [])
    assert action is not None
    body = action["payload"]["body"]
    assert body.startswith("Origin Jumpworks — "), (
        "expected title prefix for short body"
    )
    assert len(body) >= 80, "expected body to clear quality gate"


def test_derive_save_action_strips_trailing_meta_instruction():
    """First overview turn ends with 'Save it under category knowledge.'
    That phrase is a meta-instruction, not part of the note body."""
    from gateway.hive_coordinator import _derive_save_action_from_user
    msg = (
        "Save a top-level note: 'Star Citizen — overview' covering what "
        "the game is, the developer, and the persistent universe concept. "
        "Save it under category knowledge."
    )
    action = _derive_save_action_from_user(msg, [])
    assert action is not None
    assert not action["payload"]["body"].lower().endswith(
        "save it under category knowledge."
    )


def test_derive_forget_action_quoted_title():
    """Phase 2: 'Forget the Banu refresh note' must yield vault_forget
    with a query payload."""
    from gateway.hive_coordinator import _derive_forget_action_from_user
    a = _derive_forget_action_from_user("Forget the Banu refresh note.", [])
    assert a is not None
    assert a["verb"] == "vault_forget"
    assert "Banu refresh" in a["payload"]["query"]


def test_derive_forget_action_delete_phrasing():
    """'Delete the note about the Banu faction.' → vault_forget."""
    from gateway.hive_coordinator import _derive_forget_action_from_user
    a = _derive_forget_action_from_user(
        "Delete the note about the Banu faction.", [],
    )
    assert a is not None
    assert a["verb"] == "vault_forget"


def test_derive_forget_skips_when_already_present():
    """Don't double-fire when synth already emitted vault_forget."""
    from gateway.hive_coordinator import _derive_forget_action_from_user
    existing = [{"verb": "vault_forget", "payload": {"query": "x"}}]
    assert _derive_forget_action_from_user("Forget the X note", existing) is None


def test_derive_update_action_correction():
    """'Update the Hurston note — actually it has 5 moons: …' → vault_learn
    targeting title='Hurston' so dedup merges into the existing note."""
    from gateway.hive_coordinator import _derive_update_action_from_user
    msg = (
        "Update the Hurston note — actually it has 5 moons: Aberdeen, "
        "Arial, Magda, Ita, and Etna. Save this correction."
    )
    a = _derive_update_action_from_user(msg, [])
    assert a is not None
    assert a["verb"] == "vault_learn"
    assert "Hurston" in a["payload"]["title"]
    assert "5 moons" in a["payload"]["body"] or "Etna" in a["payload"]["body"]


def test_derive_update_skips_when_synth_emitted_complete_one():
    from gateway.hive_coordinator import _derive_update_action_from_user
    existing = [{
        "verb": "vault_learn",
        "payload": {"title": "Hurston", "body": "complete body content here"},
    }]
    msg = "Update the Hurston note — has 5 moons now: Aberdeen, Arial, Magda."
    assert _derive_update_action_from_user(msg, existing) is None


def test_derive_image_action_basic_generate():
    from gateway.hive_coordinator import _derive_image_action_from_user
    a = _derive_image_action_from_user(
        "Generate an image of a Drake Cutlass over Lorville.", [],
    )
    assert a is not None
    assert a["verb"] == "image_render"
    assert "Drake Cutlass" in a["payload"]["prompt"]
    assert a["payload"]["count"] == 1


def test_derive_image_action_explicit_count():
    from gateway.hive_coordinator import _derive_image_action_from_user
    a = _derive_image_action_from_user(
        "Make 4 images of a Drake Vulture salvaging wreckage.", [],
    )
    assert a is not None
    assert a["payload"]["count"] == 4


def test_derive_image_action_widescreen_aspect():
    from gateway.hive_coordinator import _derive_image_action_from_user
    a = _derive_image_action_from_user(
        "Generate a widescreen image of the Stanton system.", [],
    )
    assert a is not None
    assert a["payload"]["aspect"] == "landscape"


def test_derive_image_action_negative_prompt():
    from gateway.hive_coordinator import _derive_image_action_from_user
    a = _derive_image_action_from_user(
        "Render an image of New Babbage at night, no humans visible.", [],
    )
    assert a is not None
    assert a["payload"].get("negative_prompt") == "humans"


def test_derive_image_action_skips_when_present():
    from gateway.hive_coordinator import _derive_image_action_from_user
    existing = [{"verb": "image_render", "payload": {"prompt": "x"}}]
    msg = "Generate an image of something else longer than ten chars."
    assert _derive_image_action_from_user(msg, existing) is None
