"""Tests for shared.vault_client."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
import pytest_asyncio  # noqa: F401

from shared.vault_client import VaultClient


def _write_canon(vault: Path, name: str, audience: list[str], body: str) -> None:
    (vault / "canon" / f"{name}.md").write_text(
        f"---\ntype: canon\nauthor: human\naudience: {json.dumps(audience)}\n---\n\n{body}\n",
        encoding="utf-8",
    )


def test_preload_canon_filters_by_audience(tmp_path: Path) -> None:
    for d in ("canon", "people", "sessions", "ops", "journals",
              "knowledge", "system", "projects", "tools"):
        (tmp_path / d).mkdir()
    _write_canon(tmp_path, "maggy", ["all"], "Maggy character body.")
    _write_canon(tmp_path, "scout", ["bots"], "Scout only for bots.")
    _write_canon(tmp_path, "cc",    ["claude-code"], "Claude Code only.")

    client = VaultClient(vault_path=tmp_path, daemon_host="127.0.0.1", daemon_port=0)

    for_maggy = client.preload_canon("maggy")
    assert "Maggy character body." in for_maggy
    assert "Scout only for bots." in for_maggy  # maggy is a bot
    assert "Claude Code only." not in for_maggy

    for_cc = client.preload_canon("claude-code")
    assert "Maggy character body." in for_cc
    assert "Claude Code only." in for_cc
    assert "Scout only for bots." not in for_cc


@pytest.mark.asyncio
async def test_ping_returns_false_when_daemon_unreachable(tmp_path: Path) -> None:
    (tmp_path / "canon").mkdir()
    client = VaultClient(vault_path=tmp_path, daemon_host="127.0.0.1", daemon_port=1)
    ok = await client.ping(timeout=0.5)
    assert ok is False


@pytest.mark.asyncio
async def test_ping_returns_true_when_daemon_reachable(tmp_path: Path) -> None:
    (tmp_path / "canon").mkdir()

    async def fake_daemon(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        line = await reader.readline()
        req = json.loads(line.decode())
        assert req["method"] == "ping"
        writer.write(json.dumps({"pong": True, "daemon_version": "test"}).encode() + b"\n")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(fake_daemon, "127.0.0.1", 0)
    try:
        port = server.sockets[0].getsockname()[1]
        client = VaultClient(vault_path=tmp_path, daemon_host="127.0.0.1", daemon_port=port)
        assert await client.ping(timeout=2.0) is True
    finally:
        server.close()
        await server.wait_closed()


# -- Phase 6a additions --------------------------------------------------------


def _make_vault_layout(vault: Path) -> None:
    for d in ("canon", "people", "sessions", "ops", "journals",
              "knowledge", "system", "projects", "tools"):
        (vault / d).mkdir(exist_ok=True)


def _seed_db(vault: Path, notes: list[tuple[str, list[str], list[float], str]]) -> None:
    from vault_writer.index import VaultIndex
    (vault / ".vault-writer").mkdir(parents=True, exist_ok=True)
    idx = VaultIndex.open(vault / ".vault-writer" / "vault.db", dimension=8)
    try:
        for path, audience, emb, body in notes:
            idx.upsert(
                path=path,
                note_type=path.split("/", 1)[0],
                author="test",
                audience=audience,
                frontmatter={},
                body=body,
                embedding=emb,
            )
    finally:
        idx.close()


def test_search_filters_by_audience_for_claude_code(tmp_path: Path) -> None:
    _make_vault_layout(tmp_path)
    _seed_db(tmp_path, [
        ("ops/bots-only.md", ["bots"],        [0.1] * 8, "bot-only ops"),
        ("ops/cc-only.md",   ["claude-code"], [0.1] * 8, "cc-only ops"),
        ("canon/world.md",   ["all"],         [0.1] * 8, "world canon"),
    ])
    client = VaultClient(vault_path=tmp_path, daemon_host="127.0.0.1", daemon_port=0)
    results = client.search(query_embedding=[0.1] * 8, k=10, audience="claude-code")
    paths = {r.path for r in results}
    assert "canon/world.md" in paths
    assert "ops/cc-only.md" in paths
    assert "ops/bots-only.md" not in paths


def test_search_returns_empty_when_db_missing(tmp_path: Path) -> None:
    _make_vault_layout(tmp_path)
    client = VaultClient(vault_path=tmp_path, daemon_host="127.0.0.1", daemon_port=0)
    assert client.search([0.1] * 8, k=5, audience="claude-code") == []


def test_search_returns_empty_on_dimension_mismatch(tmp_path: Path) -> None:
    _make_vault_layout(tmp_path)
    _seed_db(tmp_path, [("canon/x.md", ["all"], [0.1] * 8, "x")])
    client = VaultClient(vault_path=tmp_path, daemon_host="127.0.0.1", daemon_port=0)
    assert client.search([0.1] * 16, k=5, audience="claude-code") == []  # wrong dim


def test_preload_cc_includes_canon(tmp_path: Path) -> None:
    _make_vault_layout(tmp_path)
    _write_canon(tmp_path, "maggy", ["all"], "Maggy body.")
    _write_canon(tmp_path, "scout", ["bots"], "Scout only for bots.")
    client = VaultClient(vault_path=tmp_path, daemon_host="127.0.0.1", daemon_port=0)
    ctx = client.preload_for_claude_code(cwd=tmp_path)
    assert "Maggy body." in ctx
    assert "Scout only for bots." not in ctx


def test_preload_cc_includes_project_note_when_cwd_matches(tmp_path: Path) -> None:
    _make_vault_layout(tmp_path)
    (tmp_path / "projects" / "ai-team.md").write_text(
        "---\ntype: project\nauthor: human\naudience: [all]\n---\n\nAi-Team project notes.\n",
        encoding="utf-8",
    )
    client = VaultClient(vault_path=tmp_path, daemon_host="127.0.0.1", daemon_port=0)
    # cwd path's actual location doesn't matter; only the basename is used.
    fake_cwd = Path("fake") / "Ai-Team"
    ctx = client.preload_for_claude_code(cwd=fake_cwd)
    assert "Ai-Team project notes." in ctx


def test_preload_cc_excludes_bots_only_ops(tmp_path: Path) -> None:
    _make_vault_layout(tmp_path)
    (tmp_path / "ops" / "cc-only.md").write_text(
        "---\ntype: ops\nauthor: human\naudience: [claude-code]\n---\n\nDavid prefers terse replies.\n",
        encoding="utf-8",
    )
    (tmp_path / "ops" / "bots-only.md").write_text(
        "---\ntype: ops\nauthor: human\naudience: [bots]\n---\n\nBot-only rule.\n",
        encoding="utf-8",
    )
    client = VaultClient(vault_path=tmp_path, daemon_host="127.0.0.1", daemon_port=0)
    ctx = client.preload_for_claude_code(cwd=tmp_path)
    assert "David prefers terse replies." in ctx
    assert "Bot-only rule." not in ctx


@pytest.mark.asyncio
async def test_learn_sends_request_and_parses_response(tmp_path: Path) -> None:
    _make_vault_layout(tmp_path)
    received = []

    async def fake_daemon(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        line = await reader.readline()
        req = json.loads(line.decode())
        received.append(req)
        assert req["method"] == "learn"
        writer.write(json.dumps({
            "ok": True, "path": "knowledge/2026/04/x.md", "created": True
        }).encode() + b"\n")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(fake_daemon, "127.0.0.1", 0)
    try:
        port = server.sockets[0].getsockname()[1]
        client = VaultClient(vault_path=tmp_path, daemon_host="127.0.0.1", daemon_port=port)
        resp = await client.learn(
            category="knowledge", title="x", body="body",
            author="claude-code", tags=["foo"],
        )
        assert resp == {"ok": True, "path": "knowledge/2026/04/x.md", "created": True}
        assert received[0]["params"]["category"] == "knowledge"
        assert received[0]["params"]["tags"] == ["foo"]
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_learn_returns_none_when_daemon_unreachable(tmp_path: Path) -> None:
    _make_vault_layout(tmp_path)
    client = VaultClient(vault_path=tmp_path, daemon_host="127.0.0.1", daemon_port=1)
    resp = await client.learn(
        category="knowledge", title="x", body="y", author="claude-code",
    )
    assert resp is None


def test_preload_cc_includes_journal_tail(tmp_path: Path) -> None:
    _make_vault_layout(tmp_path)
    (tmp_path / "journals" / "claude-code.md").write_text(
        """---
type: journal
author: claude-code
audience: [all]
---

## 2026-04-20 — first entry

old stuff

## 2026-04-23 — latest entry

new stuff that should appear in tail
""",
        encoding="utf-8",
    )
    client = VaultClient(vault_path=tmp_path, daemon_host="127.0.0.1", daemon_port=0)
    ctx = client.preload_for_claude_code(cwd=tmp_path, journal_tail=1)
    assert "new stuff that should appear in tail" in ctx
    assert "old stuff" not in ctx
