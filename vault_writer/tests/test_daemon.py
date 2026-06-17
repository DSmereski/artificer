"""Integration test for the vault-writer daemon."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio  # noqa: F401

from vault_writer.config import AuthConfig, Config, GiteaConfig, ScanConfig, SearchConfig
from vault_writer.daemon import Daemon


class FakeEmbedder:
    """Deterministic embeddings: hash(text) rolled into a fixed-dim vector."""

    def __init__(self, dimension: int = 8) -> None:
        self.dimension = dimension
        self.calls: list[str] = []

    async def embed(self, text: str, *, kind: str = "document") -> list[float]:
        self.calls.append(text)
        h = abs(hash(text))
        return [((h >> i) & 0xFF) / 255.0 for i in range(self.dimension)]

    async def embed_chunks(
        self, text: str, *, kind: str = "document", chunk_size: int | None = None,
    ) -> list[list[float]]:
        from vault_writer.embedder import chunk_text, _CHUNK_SIZE
        chunks = chunk_text(text, chunk_size=chunk_size or _CHUNK_SIZE)
        result = []
        for chunk in chunks:
            self.calls.append(chunk)
            h = abs(hash(chunk))
            result.append([((h >> i) & 0xFF) / 255.0 for i in range(self.dimension)])
        return result


def _config(tmp_vault: Path, port: int) -> Config:
    return Config(
        vault_path=tmp_vault,
        daemon_bind_host="127.0.0.1",
        daemon_bind_port=port,
        ollama_url="http://fake",
        embedding_model="fake",
        embedding_dimension=8,
        chunk_max_chars=4000,
        gitea=GiteaConfig(remote="", token_env="GITEA_TOKEN",
                          push_on_write=False, batch_window_seconds=5),
        search=SearchConfig(default_k=5, min_score=0.4),
        scan=ScanConfig(initial_full_scan=True, periodic_seconds=0,
                        reconcile_orphans=False),
        auth=AuthConfig(token_path=None),
    )


@pytest.mark.asyncio
async def test_daemon_initial_scan_indexes_existing_files(tmp_vault: Path) -> None:
    (tmp_vault / "canon" / "maggy.md").write_text(
        "---\ntype: canon\nauthor: human\naudience: [all]\n---\n\nMaggy body.\n",
        encoding="utf-8",
    )
    (tmp_vault / "canon" / "terry.md").write_text(
        "---\ntype: canon\nauthor: human\naudience: [all]\n---\n\nTerry body.\n",
        encoding="utf-8",
    )

    daemon = Daemon(_config(tmp_vault, port=0), embedder=FakeEmbedder())
    await daemon.start()
    try:
        await daemon.wait_idle(timeout=5.0)
        assert daemon.index.count() == 2
    finally:
        await daemon.stop()


@pytest.mark.asyncio
async def test_daemon_watches_for_new_files(tmp_vault: Path) -> None:
    daemon = Daemon(_config(tmp_vault, port=0), embedder=FakeEmbedder())
    await daemon.start()
    try:
        await daemon.wait_idle(timeout=5.0)
        assert daemon.index.count() == 0

        (tmp_vault / "canon" / "scout.md").write_text(
            "---\ntype: canon\nauthor: human\naudience: [all]\n---\n\nScout body.\n",
            encoding="utf-8",
        )

        await daemon.wait_idle(timeout=5.0)
        assert daemon.index.count() == 1
    finally:
        await daemon.stop()


@pytest.mark.asyncio
async def test_daemon_ping_rpc(tmp_vault: Path) -> None:
    daemon = Daemon(_config(tmp_vault, port=0), embedder=FakeEmbedder())
    await daemon.start()
    try:
        port = daemon.bound_port
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(json.dumps({"method": "ping", "params": {}}).encode() + b"\n")
        await writer.drain()
        line = await reader.readline()
        resp: dict[str, Any] = json.loads(line.decode())
        assert resp["pong"] is True
        assert resp["daemon_version"]
        writer.close()
        await writer.wait_closed()
    finally:
        await daemon.stop()


async def _learn(port: int, params: dict) -> dict:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(json.dumps({"method": "learn", "params": params}).encode() + b"\n")
    await writer.drain()
    line = await reader.readline()
    writer.close()
    await writer.wait_closed()
    return json.loads(line.decode())


@pytest.mark.asyncio
async def test_daemon_learn_knowledge_creates_file(tmp_vault: Path) -> None:
    daemon = Daemon(_config(tmp_vault, port=0), embedder=FakeEmbedder())
    await daemon.start()
    try:
        resp = await _learn(daemon.bound_port, {
            "category": "knowledge",
            "title": "Ollama nomic-embed-text is 768-dim",
            "body": "Confirmed via a POST to /api/embeddings. Use Python httpx AsyncClient for bot-side calls.",
            "author": "claude-code",
            "audience": ["claude-code"],
            "tags": ["ollama", "embeddings"],
        })
        assert resp["ok"] is True
        assert resp["created"] is True
        rel = resp["path"]
        assert rel.startswith("knowledge/")
        assert rel.endswith(".md")
        written = (tmp_vault / rel).read_text(encoding="utf-8")
        assert "type: knowledge" in written
        assert "author: claude-code" in written
        assert "Confirmed via a POST" in written
        assert daemon.index.count() == 1
    finally:
        await daemon.stop()


@pytest.mark.asyncio
async def test_daemon_learn_journal_appends(tmp_vault: Path) -> None:
    # Seed existing journal file.
    (tmp_vault / "journals" / "claude-code.md").write_text(
        "---\ntype: journal\nauthor: claude-code\naudience: [all]\n---\n\n# Claude Code journal\n\n",
        encoding="utf-8",
    )
    daemon = Daemon(_config(tmp_vault, port=0), embedder=FakeEmbedder())
    await daemon.start()
    try:
        r1 = await _learn(daemon.bound_port, {
            "category": "journal", "title": "first entry",
            "body": "started work on vault learn",
            "author": "claude-code",
        })
        r2 = await _learn(daemon.bound_port, {
            "category": "journal", "title": "second entry",
            "body": "finished learn rpc",
            "author": "claude-code",
        })
        assert r1["created"] is False  # appended to existing
        assert r2["created"] is False
        assert r1["path"] == "journals/claude-code.md"
        assert r2["path"] == "journals/claude-code.md"
        body = (tmp_vault / "journals" / "claude-code.md").read_text(encoding="utf-8")
        assert "first entry" in body
        assert "second entry" in body
        # Each second entry appends to the same file; index has one row.
        assert daemon.index.count() == 1
    finally:
        await daemon.stop()


@pytest.mark.asyncio
async def test_daemon_learn_rejects_canon(tmp_vault: Path) -> None:
    daemon = Daemon(_config(tmp_vault, port=0), embedder=FakeEmbedder())
    await daemon.start()
    try:
        resp = await _learn(daemon.bound_port, {
            "category": "canon", "title": "rewrite maggy",
            "body": "evil plan", "author": "claude-code",
        })
        assert "error" in resp
        assert "canon" in resp["error"].lower()
    finally:
        await daemon.stop()


@pytest.mark.asyncio
async def test_daemon_learn_rejects_human_author(tmp_vault: Path) -> None:
    daemon = Daemon(_config(tmp_vault, port=0), embedder=FakeEmbedder())
    await daemon.start()
    try:
        resp = await _learn(daemon.bound_port, {
            "category": "knowledge", "title": "x", "body": "y",
            "author": "human",
        })
        assert "error" in resp
        assert "human" in resp["error"].lower()
    finally:
        await daemon.stop()


@pytest.mark.asyncio
async def test_daemon_learn_rejects_path_traversal_via_discord_id(tmp_vault: Path) -> None:
    daemon = Daemon(_config(tmp_vault, port=0), embedder=FakeEmbedder())
    await daemon.start()
    try:
        # Attacker attempts to escape the vault via discord_id.
        resp = await _learn(daemon.bound_port, {
            "category": "person",
            "title": "evil",
            "body": "pwn",
            "author": "claude-code",
            "extra": {"discord_id": "../../../../etc/passwd"},
        })
        # Slugifier strips the traversal so the write stays inside people/.
        assert resp["ok"] is True
        assert resp["path"].startswith("people/")
        assert ".." not in resp["path"]
    finally:
        await daemon.stop()


@pytest.mark.asyncio
async def test_daemon_learn_with_auth_required_rejects_unauthed(
    tmp_vault: Path, tmp_path: Path
) -> None:
    tok = tmp_path / "tok"
    tok.write_text("correct-token", encoding="utf-8")
    cfg = _config(tmp_vault, port=0)
    cfg_with_auth = Config(
        vault_path=cfg.vault_path,
        daemon_bind_host=cfg.daemon_bind_host,
        daemon_bind_port=cfg.daemon_bind_port,
        ollama_url=cfg.ollama_url,
        embedding_model=cfg.embedding_model,
        embedding_dimension=cfg.embedding_dimension,
        chunk_max_chars=cfg.chunk_max_chars,
        gitea=cfg.gitea,
        search=cfg.search,
        scan=cfg.scan,
        auth=AuthConfig(token_path=tok),
    )
    daemon = Daemon(cfg_with_auth, embedder=FakeEmbedder())
    await daemon.start()
    try:
        # Request without an auth field is rejected.
        resp = await _learn(daemon.bound_port, {
            "category": "knowledge", "title": "x", "body": "y",
            "author": "claude-code",
        })
        assert "error" in resp
        # Request with correct auth succeeds.
        resp2 = await _learn_with_auth(daemon.bound_port, "correct-token", {
            "category": "knowledge", "title": "ok", "body": "y",
            "author": "claude-code",
        })
        assert resp2["ok"] is True
    finally:
        await daemon.stop()


async def _learn_with_auth(port: int, token: str, params: dict) -> dict:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(
        json.dumps({"method": "learn", "auth": token, "params": params}).encode() + b"\n"
    )
    await writer.drain()
    line = await reader.readline()
    writer.close()
    await writer.wait_closed()
    return json.loads(line.decode())
