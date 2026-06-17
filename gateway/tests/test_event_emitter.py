"""Tests for ListEmitter + WebSocketEmitter."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from gateway.event_emitter import HiveEvent, ListEmitter, WebSocketEmitter
from gateway.helpers.base import HelperResult


# ---------------------------------------------------------------- ListEmitter


def test_list_thought_records_payload():
    em = ListEmitter()
    em.thought(
        summary="planning", delegations=[{"role": "x"}],
        model="m", latency_ms=10, tokens=20, id="tk-1",
    )
    assert len(em.events) == 1
    e = em.events[0]
    assert e.type == "thought"
    assert e.id == "tk-1"
    assert e.payload["summary"] == "planning"


def test_list_helper_reply_pulls_summary():
    em = ListEmitter()
    em.helper_reply(
        HelperResult(
            role="r", model_id="m",
            output={"summary": "got it"},
            confidence="high",
            tokens_in=10, tokens_out=20, latency_ms=5,
        ),
        id="tk.r", parent="tk",
    )
    e = em.events[0]
    assert e.payload["role"] == "r"
    assert e.payload["output_summary"] == "got it"
    assert e.payload["confidence"] == "high"


def test_list_assistant_and_system_notice():
    em = ListEmitter()
    em.assistant("hi", parent_id="tk")
    em.system_notice("legacy redirect")
    types = [e.type for e in em.events]
    assert types == ["assistant", "system_notice"]


# ---------------------------------------------------------------- WebSocketEmitter


@pytest.mark.asyncio
async def test_websocket_emitter_drains_in_order():
    sent: list[dict] = []
    async def send(p):
        sent.append(p)
    em = WebSocketEmitter(send_json=send)
    em.thought(summary="s", delegations=[], model="m",
               latency_ms=1, tokens=1, id="tk-1")
    em.delegate(role="r", goal="g", model="m", id="tk-1.r", parent="tk-1")
    em.assistant("done", parent_id="tk-1")
    await em.close()
    types = [p["type"] for p in sent]
    assert types == ["thought", "delegate", "assistant"]


@pytest.mark.asyncio
async def test_websocket_emitter_quiet_swallows_reasoning():
    sent: list[dict] = []
    async def send(p):
        sent.append(p)
    em = WebSocketEmitter(send_json=send, quiet=True)
    em.thought(summary="x", delegations=[], model="m",
               latency_ms=1, tokens=1)
    em.synthesis(summary="y", actions=[])
    em.assistant("here you go")
    await em.close()
    types = [p["type"] for p in sent]
    assert "thought" not in types
    assert "synthesis" not in types
    assert "assistant" in types


@pytest.mark.asyncio
async def test_websocket_emitter_send_failure_is_isolated():
    """A flaky `send_json` (e.g. WS already closed) shouldn't crash
    subsequent emits or break drain."""
    sent: list[dict] = []
    async def send(p):
        if p["type"] == "delegate":
            raise RuntimeError("WS closed")
        sent.append(p)
    em = WebSocketEmitter(send_json=send)
    em.thought(summary="s", delegations=[], model="m",
               latency_ms=1, tokens=1)
    em.delegate(role="r", goal="g", model="m")
    em.assistant("ok")
    await em.close()
    types = [p["type"] for p in sent]
    assert "thought" in types and "assistant" in types
    assert "delegate" not in types         # was rejected by send


# ---------------------------------------------------------------- helper.late (Phase B.2 / #476)


def test_list_helper_late_records_role_and_latency():
    """Late helpers emit a helper.late event for telemetry. The event
    carries the same shape as helper_reply so downstream tooling can
    treat it as 'helper finished, but synth had already fired'."""
    em = ListEmitter()
    em.helper_late(
        HelperResult(
            role="researcher", model_id="gemma3-4b",
            output={"summary": "slow but valid"},
            confidence="medium",
            tokens_in=10, tokens_out=20, latency_ms=45000,
        ),
        id="tk.r", parent="tk-1",
    )
    assert len(em.events) == 1
    e = em.events[0]
    assert e.type == "helper.late"
    assert e.parent == "tk-1"
    assert e.payload["role"] == "researcher"
    assert e.payload["model_id"] == "gemma3-4b"
    assert e.payload["latency_ms"] == 45000
    assert e.payload["error"] is None


@pytest.mark.asyncio
async def test_websocket_helper_late_emits_through_queue():
    """WebSocketEmitter mirrors ListEmitter: helper.late goes through
    the queue and arrives over the wire."""
    sent: list[dict] = []

    async def send(p):
        sent.append(p)

    em = WebSocketEmitter(send_json=send)
    em.helper_late(
        HelperResult(
            role="librarian", model_id="planner-qwen",
            output={"summary": "x"}, confidence="high",
            tokens_in=1, tokens_out=2, latency_ms=42000,
        ),
        id="tk.l", parent="tk",
    )
    await em.close()
    assert len(sent) == 1
    assert sent[0]["type"] == "helper.late"
    assert sent[0]["role"] == "librarian"
    assert sent[0]["latency_ms"] == 42000
