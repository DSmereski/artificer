"""Tests for the M6 polish bundle: quiet mode, skills POST, RAM safety."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest
from fastapi.testclient import TestClient

from gateway.event_emitter import WebSocketEmitter
from gateway.helpers.base import HelperResult


# ---------------------------------------------------------------- quiet mode


@pytest.mark.asyncio
async def test_quiet_emitter_swallows_reasoning_events():
    sent: list[dict] = []
    async def send(p):
        sent.append(p)
    emitter = WebSocketEmitter(send_json=send, quiet=True)

    emitter.thought(
        summary="planned",
        delegations=[], model="x", latency_ms=10, tokens=5,
    )
    emitter.delegate(role="researcher", goal="g", model="x")
    emitter.helper_reply(HelperResult(role="r", model_id="x"))
    emitter.synthesis(summary="done", actions=[])
    emitter.assistant("hello")
    emitter.system_notice("hi")

    # Allow the asyncio.ensure_future calls to run.
    import asyncio
    await asyncio.sleep(0.05)

    types = [p["type"] for p in sent]
    assert "thought" not in types
    assert "delegate" not in types
    assert "helper_reply" not in types
    assert "synthesis" not in types
    assert "assistant" in types
    assert "system_notice" in types


@pytest.mark.asyncio
async def test_loud_emitter_sends_all_events():
    sent: list[dict] = []
    async def send(p):
        sent.append(p)
    emitter = WebSocketEmitter(send_json=send, quiet=False)

    emitter.thought(summary="x", delegations=[], model="x", latency_ms=1, tokens=1)
    emitter.assistant("hi")
    import asyncio
    await asyncio.sleep(0.05)
    types = [p["type"] for p in sent]
    assert "thought" in types
    assert "assistant" in types


# ---------------------------------------------------------------- skills POST


_SKILL_BODY = dedent("""\
    ---
    name: post-test
    description: A skill authored via POST.
    audience: [terry]
    ---

    # Post test

    1. First step.
    2. Second step.
""")


def test_skills_post_creates(client: TestClient, paired_token, tmp_path, monkeypatch):
    # Inject a fresh registry rooted at tmp_path so this test doesn't
    # touch the real vault.
    from gateway.skill_registry import SkillRegistry
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    reg = SkillRegistry(skills_dir)
    reg.load()
    client.app.state.ai_team.skill_registry = reg

    _, token = paired_token
    H = {"Authorization": f"Bearer {token}"}
    r = client.post("/v1/skills",
                    json={"name": "post-test", "body": _SKILL_BODY},
                    headers=H)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["name"] == "post-test"
    assert reg.get("post-test") is not None


def test_skills_post_rejects_short_body(client: TestClient, paired_token, tmp_path):
    from gateway.skill_registry import SkillRegistry
    reg = SkillRegistry(tmp_path / "skills")
    reg.load()
    client.app.state.ai_team.skill_registry = reg
    _, token = paired_token
    r = client.post("/v1/skills",
                    json={"name": "too-small", "body": "tiny"},
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 422       # pydantic min_length


def test_skills_post_rejects_no_frontmatter(client: TestClient, paired_token, tmp_path):
    from gateway.skill_registry import SkillRegistry
    reg = SkillRegistry(tmp_path / "skills")
    reg.load()
    client.app.state.ai_team.skill_registry = reg
    _, token = paired_token
    body = "no fence here, just " + ("y" * 200)
    r = client.post("/v1/skills",
                    json={"name": "no-fm", "body": body},
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 400


def test_skills_post_rejects_duplicate(client: TestClient, paired_token, tmp_path):
    from gateway.skill_registry import SkillRegistry
    reg = SkillRegistry(tmp_path / "skills")
    reg.load()
    client.app.state.ai_team.skill_registry = reg
    _, token = paired_token
    H = {"Authorization": f"Bearer {token}"}
    # The body's frontmatter says `name: post-test`. Post with that
    # matching name (the new traversal hardening requires it).
    r = client.post(
        "/v1/skills",
        json={"name": "post-test", "body": _SKILL_BODY},
        headers=H,
    )
    assert r.status_code == 200
    r2 = client.post(
        "/v1/skills",
        json={"name": "post-test", "body": _SKILL_BODY},
        headers=H,
    )
    assert r2.status_code == 409


def test_skills_post_rejects_mismatched_frontmatter_name(
    client: TestClient, paired_token, tmp_path,
):
    """Audit: the body's frontmatter `name` must slugify to the same
    path as the request's `name`. This blocks a name-mismatch
    traversal angle."""
    from gateway.skill_registry import SkillRegistry
    reg = SkillRegistry(tmp_path / "skills")
    reg.load()
    client.app.state.ai_team.skill_registry = reg
    _, token = paired_token
    H = {"Authorization": f"Bearer {token}"}
    r = client.post(
        "/v1/skills",
        # body says `post-test` but request says `something-else`
        json={"name": "something-else", "body": _SKILL_BODY},
        headers=H,
    )
    assert r.status_code == 400


# ---------------------------------------------------------------- RAM safety


def test_free_system_ram_mb_returns_int_or_none():
    from gateway.hive_coordinator import _free_system_ram_mb
    out = _free_system_ram_mb()
    assert out is None or (isinstance(out, int) and out > 0)
