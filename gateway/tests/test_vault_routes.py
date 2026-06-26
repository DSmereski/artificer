"""Vault route tests. VaultClient is monkeypatched at the route layer
to avoid hitting the real daemon or Ollama."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from gateway.routes import vault as vault_route


def _fake_search_result(path: str, body: str, audience: list[str]):
    # Minimal shape of vault_writer.index.SearchResult that the route consumes.
    class _R:
        def __init__(self) -> None:
            self.path = path
            self.note_type = path.split("/", 1)[0]
            self.author = "test"
            self.audience = audience
            self.body = body
            self.frontmatter = {}
            self.score = 0.9
    return _R()


class _FakeClient:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def search(self, *, query_embedding, k, audience, query_text=None):
        return [
            _fake_search_result("canon/maggy.md", "Maggy is the coder", ["all"]),
            _fake_search_result("ops/prefs.md", "terse replies", ["claude-code"]),
        ]

    async def learn(self, **kwargs):
        return {"ok": True, "path": f"{kwargs['category']}/{kwargs['title']}.md", "created": True}


async def _fake_embed(ollama_url: str, model: str, text: str) -> list[float]:
    return [0.1] * 8


def test_vault_search(
    client: TestClient, paired_token: tuple[str, str], monkeypatch
) -> None:
    monkeypatch.setattr(vault_route, "_embed_query", _fake_embed)
    # Route does `from shared.vault_client import VaultClient` at call time;
    # patch the source so the local import resolves to our fake.
    monkeypatch.setattr("shared.vault_client.VaultClient", _FakeClient)
    _, token = paired_token
    r = client.get(
        "/v1/vault/search?q=who%20is%20maggy",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    hits = r.json()
    assert len(hits) == 2
    assert hits[0]["path"] == "canon/maggy.md"
    assert "Maggy" in hits[0]["preview"]


def test_vault_tree(
    client: TestClient, paired_token: tuple[str, str], tmp_path: Path
) -> None:
    # Seed a minimal vault layout inside the tmp vault the fixture set up.
    st = client.app.state.ai_team
    vault = st.config.vault_path
    (vault / "canon").mkdir(exist_ok=True)
    (vault / "canon" / "maggy.md").write_text("# Maggy\n", encoding="utf-8")
    (vault / "ops").mkdir(exist_ok=True)
    (vault / "ops" / "prefs.md").write_text("# prefs\n", encoding="utf-8")
    _, token = paired_token
    r = client.get(
        "/v1/vault/tree", headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    tree = r.json()
    assert tree["is_dir"] is True
    top_names = {c["name"] for c in tree["children"]}
    assert "canon" in top_names
    assert "ops" in top_names


def test_vault_note_read(
    client: TestClient, paired_token: tuple[str, str]
) -> None:
    st = client.app.state.ai_team
    (st.config.vault_path / "canon").mkdir(exist_ok=True)
    (st.config.vault_path / "canon" / "world.md").write_text(
        "# World\n\nLore.", encoding="utf-8",
    )
    _, token = paired_token
    r = client.get(
        "/v1/vault/note?path=canon/world.md",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert "Lore" in r.json()["body"]


def test_vault_note_rejects_traversal(
    client: TestClient, paired_token: tuple[str, str]
) -> None:
    _, token = paired_token
    r = client.get(
        "/v1/vault/note?path=../../etc/passwd",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code in (400, 404)


def test_vault_note_requires_md(
    client: TestClient, paired_token: tuple[str, str]
) -> None:
    st = client.app.state.ai_team
    (st.config.vault_path / "readme.txt").write_text("x", encoding="utf-8")
    _, token = paired_token
    r = client.get(
        "/v1/vault/note?path=readme.txt",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 400


def test_vault_learn(
    client: TestClient, paired_token: tuple[str, str], monkeypatch
) -> None:
    monkeypatch.setattr("shared.vault_client.VaultClient", _FakeClient)
    _, token = paired_token
    r = client.post(
        "/v1/vault/learn",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "category": "knowledge",
            "title": "Foo Bar",
            "body": (
                "This is a knowledge note about Foo Bar. "
                "It contains enough informative content to clear the quality gate."
            ),
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["path"] == "knowledge/Foo Bar.md"


def test_vault_learn_rejected_below_quality(
    client: TestClient, paired_token: tuple[str, str], monkeypatch
) -> None:
    """Sub-threshold writes return 422 with a clear reason."""
    monkeypatch.setattr("shared.vault_client.VaultClient", _FakeClient)
    _, token = paired_token
    r = client.post(
        "/v1/vault/learn",
        headers={"Authorization": f"Bearer {token}"},
        json={"category": "knowledge", "title": "x", "body": "stub"},
    )
    assert r.status_code == 422, r.text
    assert "quality" in r.json()["detail"].lower()


# ---------------------------------------------------------------- delete


def test_vault_note_delete_happy(
    client: TestClient, paired_token: tuple[str, str]
) -> None:
    st = client.app.state.ai_team
    (st.config.vault_path / "knowledge").mkdir(exist_ok=True)
    note = st.config.vault_path / "knowledge" / "scratch.md"
    note.write_text(
        "---\ntype: knowledge\naudience: [all]\n---\n\nthrowaway\n",
        encoding="utf-8",
    )
    _, token = paired_token
    r = client.delete(
        "/v1/vault/note?path=knowledge/scratch.md",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 204, r.text
    assert not note.exists()


def test_vault_note_delete_404(
    client: TestClient, paired_token: tuple[str, str]
) -> None:
    _, token = paired_token
    r = client.delete(
        "/v1/vault/note?path=knowledge/nope.md",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 404


def test_vault_note_delete_rejects_traversal(
    client: TestClient, paired_token: tuple[str, str]
) -> None:
    _, token = paired_token
    r = client.delete(
        "/v1/vault/note?path=../../etc/passwd",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code in (400, 404)


def test_vault_note_delete_rejects_non_md(
    client: TestClient, paired_token: tuple[str, str]
) -> None:
    st = client.app.state.ai_team
    (st.config.vault_path / "readme.txt").write_text("x", encoding="utf-8")
    _, token = paired_token
    r = client.delete(
        "/v1/vault/note?path=readme.txt",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 400


def test_audience_matches_all_caller_sees_everything() -> None:
    """The 'all' caller wildcard regression we hit in the app's vault tab —
    devices paired with audience=['all'] couldn't see Hive-saved notes
    because audience_matches('all', ['hive', 'claude-code']) returned False."""
    from vault_writer.util import audience_matches
    assert audience_matches("all", ["hive", "claude-code"]) is True
    assert audience_matches("all", ["hive"]) is True
    assert audience_matches("all", ["all"]) is True
    # Other agents still gated normally.
    assert audience_matches("hive", ["claude-code"]) is False
    assert audience_matches("hive", ["hive"]) is True


# ---------------------------------------------------------------- title field


def test_vault_backlinks(
    client: TestClient, paired_token: tuple[str, str]
) -> None:
    """A note that wikilinks to X surfaces in /v1/vault/backlinks?path=X."""
    st = client.app.state.ai_team
    folder = st.config.vault_path / "knowledge"
    folder.mkdir(exist_ok=True)
    (folder / "alpha.md").write_text(
        "---\ntype: knowledge\ntitle: Alpha\n---\n\nFoundational note.",
        encoding="utf-8",
    )
    (folder / "beta.md").write_text(
        "---\ntype: knowledge\ntitle: Beta\n---\n\nReferences [[Alpha]].",
        encoding="utf-8",
    )
    (folder / "unrelated.md").write_text(
        "---\ntype: knowledge\ntitle: Other\n---\n\nNo links here.",
        encoding="utf-8",
    )
    _, token = paired_token
    r = client.get(
        "/v1/vault/backlinks?path=knowledge/alpha.md",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    paths = [h["path"] for h in r.json()]
    assert "knowledge/beta.md" in paths
    assert "knowledge/unrelated.md" not in paths


def test_vault_backlinks_matches_slug_form(
    client: TestClient, paired_token: tuple[str, str]
) -> None:
    """`[[alpha]]` (slug form) is treated as backlinking to Alpha."""
    st = client.app.state.ai_team
    folder = st.config.vault_path / "knowledge"
    folder.mkdir(exist_ok=True)
    (folder / "alpha.md").write_text(
        "---\ntype: knowledge\ntitle: Alpha\n---\n\nbody.",
        encoding="utf-8",
    )
    (folder / "beta.md").write_text(
        "---\ntype: knowledge\ntitle: Beta\n---\n\nReferences [[alpha]].",
        encoding="utf-8",
    )
    _, token = paired_token
    r = client.get(
        "/v1/vault/backlinks?path=knowledge/alpha.md",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    paths = [h["path"] for h in r.json()]
    assert "knowledge/beta.md" in paths


def test_vault_tags_and_by_tag(
    client: TestClient, paired_token: tuple[str, str]
) -> None:
    """/v1/vault/tags returns counts; /v1/vault/by-tag filters notes."""
    st = client.app.state.ai_team
    folder = st.config.vault_path / "knowledge"
    folder.mkdir(exist_ok=True)
    (folder / "drake.md").write_text(
        "---\ntype: knowledge\ntitle: Drake\ntags:\n  - star-citizen\n  - ships\n---\n\nDrake.",
        encoding="utf-8",
    )
    (folder / "kraken.md").write_text(
        "---\ntype: knowledge\ntitle: Kraken\ntags: [star-citizen]\n---\n\nKraken.",
        encoding="utf-8",
    )
    (folder / "untagged.md").write_text(
        "---\ntype: knowledge\ntitle: Plain\n---\n\nPlain.",
        encoding="utf-8",
    )
    _, token = paired_token
    r = client.get(
        "/v1/vault/tags", headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    by_tag = {row["tag"]: row["count"] for row in r.json()}
    assert by_tag.get("star-citizen") == 2
    assert by_tag.get("ships") == 1

    r = client.get(
        "/v1/vault/by-tag?tag=ships",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    paths = [row["path"] for row in r.json()]
    assert paths == ["knowledge/drake.md"]


def test_vault_search_includes_frontmatter_title(
    client: TestClient, paired_token: tuple[str, str], monkeypatch
) -> None:
    """SearchHit.title surfaces the frontmatter title so the app can show
    a real-readable name instead of `knowledge/2026/04/foo-bar.md`."""
    st = client.app.state.ai_team
    folder = st.config.vault_path / "canon"
    folder.mkdir(exist_ok=True)
    (folder / "maggy.md").write_text(
        "---\ntype: canon\ntitle: Maggy the Coder\n---\n\nMaggy is the coder",
        encoding="utf-8",
    )
    monkeypatch.setattr(vault_route, "_embed_query", _fake_embed)
    monkeypatch.setattr("shared.vault_client.VaultClient", _FakeClient)
    _, token = paired_token
    r = client.get(
        "/v1/vault/search?q=who%20is%20maggy",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    hits = r.json()
    by_path = {h["path"]: h for h in hits}
    # Frontmatter title wins.
    assert by_path["canon/maggy.md"]["title"] == "Maggy the Coder"
    # ops/prefs.md doesn't exist on disk → falls through to None.
    # (The slug fallback only fires when the file is on disk.)
    assert by_path["ops/prefs.md"]["title"] is None
