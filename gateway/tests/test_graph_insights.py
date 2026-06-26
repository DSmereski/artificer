"""Tests for GET /v1/graph/insights.

Verifies that the endpoint correctly identifies:
- isolated nodes (no edges in or out)
- bridge nodes (articulation points whose removal splits the graph)

Uses the same _seed_db helper pattern as test_graph_routes.py.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gateway.routes.graph import (
    _count_components,
    _find_bridges,
    _find_isolated,
)
from vault_writer.graph_query import EntityNode, Edge


# ---------------------------------------------------------------------------
# DB seed helper (mirrors test_graph_routes.py)
# ---------------------------------------------------------------------------


def _seed_db(vault_path: Path, entities: list[dict]) -> None:
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
            "(id, kind, title, compiled_truth, relationships) VALUES (?, ?, ?, ?, ?)",
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
# Unit tests: pure helper functions (no HTTP)
# ---------------------------------------------------------------------------


def _make_graph(spec: dict[str, list[tuple[str, str]]]) -> dict[str, EntityNode]:
    """Build a graph dict from {slug: [(target, label), ...]}."""
    nodes = {slug: EntityNode(slug=slug, kind="test", title=slug, compiled_truth="") for slug in spec}
    for slug, edges in spec.items():
        for target, label in edges:
            nodes[slug].edges.append(
                Edge(target=target, label=label, confidence="EXTRACTED", weight=1.0)
            )
    return nodes


def test_find_isolated_no_edges() -> None:
    """A node with no outbound or inbound edges is isolated."""
    graph = _make_graph({"alone": [], "a": [("b", "rel")], "b": []})
    isolated = _find_isolated(graph)
    slugs = [e["slug"] for e in isolated]
    assert "alone" in slugs
    assert "a" not in slugs
    assert "b" not in slugs  # b has an inbound edge from a


def test_find_isolated_all_connected() -> None:
    graph = _make_graph({"a": [("b", "r")], "b": [("c", "r")], "c": [("a", "r")]})
    assert _find_isolated(graph) == []


def test_count_components_single() -> None:
    adj = {"a": {"b"}, "b": {"a"}}
    assert _count_components({"a", "b"}, adj) == 1


def test_count_components_two() -> None:
    adj = {"a": {"b"}, "b": {"a"}, "c": {"d"}, "d": {"c"}}
    assert _count_components({"a", "b", "c", "d"}, adj) == 2


def test_find_bridges_linear_chain() -> None:
    """In A-B-C, node B is a bridge (removing it disconnects A from C)."""
    graph = _make_graph({"a": [("b", "r")], "b": [("c", "r")], "c": []})
    bridges = _find_bridges(graph)
    slugs = [e["slug"] for e in bridges]
    assert "b" in slugs


def test_find_bridges_cycle_no_bridges() -> None:
    """In a cycle A→B→C→A, no node is a bridge."""
    graph = _make_graph({
        "a": [("b", "r")],
        "b": [("c", "r")],
        "c": [("a", "r")],
    })
    bridges = _find_bridges(graph)
    assert bridges == []


def test_find_bridges_isolated_node_not_a_bridge() -> None:
    """An isolated node is not reported as a bridge (removing it changes nothing)."""
    graph = _make_graph({"lone": [], "x": [("y", "r")], "y": []})
    bridges = _find_bridges(graph)
    slugs = [e["slug"] for e in bridges]
    assert "lone" not in slugs


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def insights_client(client: TestClient, paired_token):
    """Client with a seeded graph: isolated node + known bridge."""
    vault_path = client.app.state.ai_team.config.vault_path
    _seed_db(vault_path, [
        # Linear chain: alpha → beta → gamma (beta is the bridge)
        {
            "id": "alpha",
            "kind": "faction",
            "title": "Alpha",
            "relationships": [
                {"target_slug": "beta", "label": "connects", "confidence": "EXTRACTED"},
            ],
        },
        {
            "id": "beta",
            "kind": "faction",
            "title": "Beta",
            "relationships": [
                {"target_slug": "gamma", "label": "connects", "confidence": "EXTRACTED"},
            ],
        },
        {
            "id": "gamma",
            "kind": "faction",
            "title": "Gamma",
            "relationships": [],
        },
        # Isolated node: no edges at all
        {
            "id": "orphan",
            "kind": "character",
            "title": "Orphan Node",
            "relationships": [],
        },
    ])
    return client, paired_token[1]


# ---------------------------------------------------------------------------
# /v1/graph/insights route tests
# ---------------------------------------------------------------------------


def test_insights_auth_required(insights_client) -> None:
    client, _ = insights_client
    r = client.get("/v1/graph/insights")
    assert r.status_code == 401


def test_insights_returns_isolated_node(insights_client) -> None:
    client, token = insights_client
    r = client.get(
        "/v1/graph/insights",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert "isolated" in data
    isolated_slugs = [n["slug"] for n in data["isolated"]]
    assert "orphan" in isolated_slugs, f"expected 'orphan' in isolated, got {isolated_slugs}"


def test_insights_does_not_report_connected_as_isolated(insights_client) -> None:
    client, token = insights_client
    r = client.get(
        "/v1/graph/insights",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    isolated_slugs = [n["slug"] for n in r.json()["isolated"]]
    # alpha, beta, gamma are all connected
    for slug in ("alpha", "beta", "gamma"):
        assert slug not in isolated_slugs, f"{slug} was incorrectly reported as isolated"


def test_insights_returns_bridge_node(insights_client) -> None:
    """In the alpha→beta→gamma chain, beta is an articulation point."""
    client, token = insights_client
    r = client.get(
        "/v1/graph/insights",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert "bridges" in data
    bridge_slugs = [n["slug"] for n in data["bridges"]]
    assert "beta" in bridge_slugs, f"expected 'beta' in bridges, got {bridge_slugs}"


def test_insights_bridge_split_count(insights_client) -> None:
    """Bridge node 'beta' should report split >= 2."""
    client, token = insights_client
    r = client.get(
        "/v1/graph/insights",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    bridges = r.json()["bridges"]
    beta = next((b for b in bridges if b["slug"] == "beta"), None)
    assert beta is not None
    assert beta["split"] >= 2


def test_insights_response_shape(insights_client) -> None:
    """Response has isolated, bridges, and notes fields."""
    client, token = insights_client
    r = client.get(
        "/v1/graph/insights",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert "isolated" in data
    assert "bridges" in data
    assert "notes" in data
    assert isinstance(data["notes"], list)


def test_insights_no_db_503(client: TestClient, paired_token) -> None:
    """Returns 503 when vault DB is absent (mirrors god-nodes behaviour)."""
    _, token = paired_token
    r = client.get(
        "/v1/graph/insights",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 503
