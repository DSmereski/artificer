"""Tests for gateway graph routes: /v1/graph/neighbors|path|explain|god-nodes.

Uses the standard `client` + `paired_token` fixtures from conftest.
Writes a minimal entity_page DB directly under the vault path so the
routes' _load() helper finds it without needing the daemon.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers to seed the vault DB
# ---------------------------------------------------------------------------


def _seed_db(vault_path: Path, entities: list[dict]) -> None:
    """Write a minimal vault.db with entity_page rows under vault_path."""
    db_dir = vault_path / ".vault-writer"
    db_dir.mkdir(parents=True, exist_ok=True)
    db = db_dir / "vault.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        """CREATE TABLE IF NOT EXISTS entity_page (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            title TEXT NOT NULL,
            compiled_truth TEXT,
            timeline TEXT,
            created_at INTEGER NOT NULL DEFAULT 0,
            last_mentioned_at INTEGER NOT NULL DEFAULT 0,
            relationships TEXT NOT NULL DEFAULT '[]'
        )"""
    )
    for e in entities:
        conn.execute(
            "INSERT OR REPLACE INTO entity_page "
            "(id, kind, title, compiled_truth, relationships) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                e["id"],
                e.get("kind", "character"),
                e.get("title", e["id"]),
                e.get("compiled_truth", ""),
                json.dumps(e.get("relationships", [])),
            ),
        )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def graph_client(client: TestClient, tmp_path: Path, paired_token):
    """TestClient with a seeded graph DB in the vault path."""
    vault_path = client.app.state.ai_team.config.vault_path
    _seed_db(vault_path, [
        {
            "id": "drake",
            "kind": "org",
            "title": "Drake Interplanetary",
            "compiled_truth": "Manufacturer of large, utilitarian ships.",
            "relationships": [
                {"target_slug": "kraken", "label": "manufactures", "confidence": "EXTRACTED"},
            ],
        },
        {
            "id": "kraken",
            "kind": "ship",
            "title": "Kraken",
            "compiled_truth": "A Drake capital carrier.",
            "relationships": [
                {"target_slug": "uee", "label": "operates_in", "confidence": "INFERRED"},
            ],
        },
        {
            "id": "uee",
            "kind": "faction",
            "title": "United Earth Empire",
            "compiled_truth": "The governing faction.",
            "relationships": [],
        },
    ])
    return client, paired_token[1]


# ---------------------------------------------------------------------------
# /v1/graph/neighbors
# ---------------------------------------------------------------------------


def test_neighbors_auth_required(graph_client) -> None:
    client, _ = graph_client
    r = client.get("/v1/graph/neighbors", params={"slug": "drake"})
    assert r.status_code == 401


def test_neighbors_basic(graph_client) -> None:
    client, token = graph_client
    r = client.get(
        "/v1/graph/neighbors",
        params={"slug": "drake", "depth": 1},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["slug"] == "drake"
    assert "kraken" in data["neighbors"]


def test_neighbors_depth2_reaches_uee(graph_client) -> None:
    client, token = graph_client
    r = client.get(
        "/v1/graph/neighbors",
        params={"slug": "drake", "depth": 2},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert "uee" in r.json()["neighbors"]


def test_neighbors_unknown_slug_404(graph_client) -> None:
    client, token = graph_client
    r = client.get(
        "/v1/graph/neighbors",
        params={"slug": "unknown-slug-xyz"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# /v1/graph/path
# ---------------------------------------------------------------------------


def test_path_auth_required(graph_client) -> None:
    client, _ = graph_client
    r = client.get("/v1/graph/path", params={"from": "drake", "to": "uee"})
    assert r.status_code == 401


def test_path_found(graph_client) -> None:
    client, token = graph_client
    r = client.get(
        "/v1/graph/path",
        params={"from": "drake", "to": "uee"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["found"] is True
    slugs = [p["slug"] for p in data["path"]]
    assert slugs[0] == "drake"
    assert slugs[-1] == "uee"


def test_path_not_found(graph_client) -> None:
    client, token = graph_client
    r = client.get(
        "/v1/graph/path",
        params={"from": "drake", "to": "missing"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert r.json()["found"] is False


# ---------------------------------------------------------------------------
# /v1/graph/explain
# ---------------------------------------------------------------------------


def test_explain_auth_required(graph_client) -> None:
    client, _ = graph_client
    r = client.get("/v1/graph/explain", params={"slug": "drake"})
    assert r.status_code == 401


def test_explain_found(graph_client) -> None:
    client, token = graph_client
    r = client.get(
        "/v1/graph/explain",
        params={"slug": "kraken"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["found"] is True
    assert data["slug"] == "kraken"
    assert "Drake" in data["compiled_truth"] or data["compiled_truth"] != ""
    assert data["degree_out"] >= 1


def test_explain_not_found_404(graph_client) -> None:
    client, token = graph_client
    r = client.get(
        "/v1/graph/explain",
        params={"slug": "totally-unknown"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# /v1/graph/god-nodes
# ---------------------------------------------------------------------------


def test_god_nodes_auth_required(graph_client) -> None:
    client, _ = graph_client
    r = client.get("/v1/graph/god-nodes")
    assert r.status_code == 401


def test_god_nodes_returns_list(graph_client) -> None:
    client, token = graph_client
    r = client.get(
        "/v1/graph/god-nodes",
        params={"limit": 3},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert "nodes" in data
    assert len(data["nodes"]) <= 3
    for node in data["nodes"]:
        assert "slug" in node
        assert "degree" in node


def test_god_nodes_no_db_503(client: TestClient, paired_token) -> None:
    """Returns 503 when the vault DB doesn't exist yet."""
    # Don't seed the DB for this test.
    _, token = paired_token
    r = client.get(
        "/v1/graph/god-nodes",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 503
