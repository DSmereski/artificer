"""Tests for vault_writer.graph_query — pure-function graph traversal."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from vault_writer.graph_query import (
    Edge,
    EntityNode,
    explain,
    god_nodes,
    load_graph,
    neighbors,
    shortest_path,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db(tmp_path: Path) -> sqlite3.Connection:
    """Minimal in-memory DB with entity_page table."""
    db = tmp_path / "vault.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE entity_page (
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
    conn.commit()
    return conn


def _insert(conn: sqlite3.Connection, slug: str, title: str, rels: list[dict]) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO entity_page (id, kind, title, compiled_truth, relationships) "
        "VALUES (?, 'character', ?, '', ?)",
        (slug, title, json.dumps(rels)),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# load_graph
# ---------------------------------------------------------------------------


def test_load_graph_empty(tmp_path: Path) -> None:
    conn = _make_db(tmp_path)
    graph = load_graph(conn)
    assert graph == {}
    conn.close()


def test_load_graph_basic(tmp_path: Path) -> None:
    conn = _make_db(tmp_path)
    _insert(conn, "drake", "Drake Interplanetary", [
        {"target_slug": "aurora", "label": "manufactures", "confidence": "EXTRACTED"},
    ])
    _insert(conn, "aurora", "Aurora MR", [])
    graph = load_graph(conn)
    conn.close()
    assert "drake" in graph
    assert "aurora" in graph
    assert len(graph["drake"].edges) == 1
    assert graph["drake"].edges[0].target == "aurora"
    assert graph["drake"].edges[0].confidence == "EXTRACTED"
    assert graph["aurora"].edges == []


def test_load_graph_malformed_rels_skipped(tmp_path: Path) -> None:
    conn = _make_db(tmp_path)
    # Corrupt JSON — should not raise, just produce zero edges.
    conn.execute(
        "INSERT INTO entity_page (id, kind, title, relationships) "
        "VALUES ('bad', 'x', 'Bad', 'NOT JSON')"
    )
    conn.commit()
    graph = load_graph(conn)
    conn.close()
    assert graph["bad"].edges == []


# ---------------------------------------------------------------------------
# neighbors
# ---------------------------------------------------------------------------


def _triangle_graph() -> dict[str, EntityNode]:
    """A → B → C, also A → C directly."""
    return {
        "A": EntityNode("A", "x", "A", "", [
            Edge("B", "knows", "EXTRACTED", 1.0),
            Edge("C", "knows", "INFERRED", 2.0),
        ]),
        "B": EntityNode("B", "x", "B", "", [
            Edge("C", "knows", "EXTRACTED", 1.0),
        ]),
        "C": EntityNode("C", "x", "C", "", []),
    }


def test_neighbors_depth1(tmp_path: Path) -> None:
    g = _triangle_graph()
    result = neighbors("A", g, depth=1)
    assert set(result) == {"B", "C"}


def test_neighbors_depth2_includes_indirect(tmp_path: Path) -> None:
    g = {
        "A": EntityNode("A", "x", "A", "", [Edge("B", "r", "EXTRACTED", 1.0)]),
        "B": EntityNode("B", "x", "B", "", [Edge("C", "r", "EXTRACTED", 1.0)]),
        "C": EntityNode("C", "x", "C", "", []),
    }
    result = neighbors("A", g, depth=2)
    assert set(result) == {"B", "C"}


def test_neighbors_inbound_traversal(tmp_path: Path) -> None:
    """Neighbors should follow inbound edges too (undirected)."""
    g = {
        "A": EntityNode("A", "x", "A", "", []),
        "B": EntityNode("B", "x", "B", "", [Edge("A", "r", "EXTRACTED", 1.0)]),
        "C": EntityNode("C", "x", "C", "", []),
    }
    result = neighbors("A", g, depth=1)
    assert "B" in result  # B points to A, so A's inbound neighbor is B


def test_neighbors_unknown_slug(tmp_path: Path) -> None:
    g = _triangle_graph()
    assert neighbors("UNKNOWN", g) == []


def test_neighbors_no_self_in_result(tmp_path: Path) -> None:
    g = _triangle_graph()
    result = neighbors("A", g, depth=1)
    assert "A" not in result


# ---------------------------------------------------------------------------
# shortest_path
# ---------------------------------------------------------------------------


def test_shortest_path_direct(tmp_path: Path) -> None:
    g = _triangle_graph()
    r = shortest_path("A", "B", g)
    assert r.slugs == ["A", "B"]
    assert r.found if hasattr(r, "found") else bool(r.slugs)


def test_shortest_path_two_hops(tmp_path: Path) -> None:
    g = {
        "A": EntityNode("A", "x", "A", "", [Edge("B", "r1", "EXTRACTED", 1.0)]),
        "B": EntityNode("B", "x", "B", "", [Edge("C", "r2", "EXTRACTED", 1.0)]),
        "C": EntityNode("C", "x", "C", "", []),
    }
    r = shortest_path("A", "C", g)
    assert r.slugs == ["A", "B", "C"]
    assert r.labels == ["r1", "r2"]


def test_shortest_path_no_path(tmp_path: Path) -> None:
    g = {
        "A": EntityNode("A", "x", "A", "", []),
        "B": EntityNode("B", "x", "B", "", []),
    }
    r = shortest_path("A", "B", g)
    assert r.slugs == []


def test_shortest_path_same_node(tmp_path: Path) -> None:
    g = _triangle_graph()
    r = shortest_path("A", "A", g)
    assert r.slugs == ["A"]


def test_shortest_path_prefers_high_confidence(tmp_path: Path) -> None:
    """Path through EXTRACTED edge should be preferred over AMBIGUOUS."""
    g = {
        "A": EntityNode("A", "x", "A", "", [
            Edge("B", "via_b", "EXTRACTED", 1.0),
            Edge("C", "via_c", "AMBIGUOUS", 4.0),
        ]),
        "B": EntityNode("B", "x", "B", "", [Edge("D", "b_d", "EXTRACTED", 1.0)]),
        "C": EntityNode("C", "x", "C", "", [Edge("D", "c_d", "EXTRACTED", 1.0)]),
        "D": EntityNode("D", "x", "D", "", []),
    }
    r = shortest_path("A", "D", g)
    # Both paths are length 2; EXTRACTED path A→B→D should win.
    assert r.slugs[1] == "B"


def test_shortest_path_unknown_slug(tmp_path: Path) -> None:
    g = _triangle_graph()
    r = shortest_path("A", "UNKNOWN", g)
    assert r.slugs == []


# ---------------------------------------------------------------------------
# explain
# ---------------------------------------------------------------------------


def test_explain_found(tmp_path: Path) -> None:
    g = {
        "kraken": EntityNode("kraken", "ship", "Kraken", "A Drake capital ship.", [
            Edge("drake", "manufactured_by", "EXTRACTED", 1.0),
        ]),
        "drake": EntityNode("drake", "org", "Drake Interplanetary", "", []),
    }
    info = explain("kraken", g)
    assert info["found"] is True
    assert info["title"] == "Kraken"
    assert info["compiled_truth"] == "A Drake capital ship."
    assert "EXTRACTED" in info["edges_by_confidence"]
    assert info["degree_out"] == 1


def test_explain_not_found(tmp_path: Path) -> None:
    g: dict = {}
    info = explain("missing", g)
    assert info["found"] is False


# ---------------------------------------------------------------------------
# god_nodes
# ---------------------------------------------------------------------------


def test_god_nodes_returns_highest_degree(tmp_path: Path) -> None:
    g = {
        "hub": EntityNode("hub", "x", "Hub", "", [
            Edge("a", "r", "EXTRACTED", 1.0),
            Edge("b", "r", "EXTRACTED", 1.0),
            Edge("c", "r", "EXTRACTED", 1.0),
        ]),
        "a": EntityNode("a", "x", "A", "", []),
        "b": EntityNode("b", "x", "B", "", []),
        "c": EntityNode("c", "x", "C", "", []),
    }
    top = god_nodes(g, limit=1)
    assert top[0]["slug"] == "hub"
    assert top[0]["degree_out"] == 3


def test_god_nodes_counts_indegree(tmp_path: Path) -> None:
    """A node that is pointed TO by many others should also rank highly."""
    g = {
        "a": EntityNode("a", "x", "A", "", [Edge("target", "r", "EXTRACTED", 1.0)]),
        "b": EntityNode("b", "x", "B", "", [Edge("target", "r", "EXTRACTED", 1.0)]),
        "c": EntityNode("c", "x", "C", "", [Edge("target", "r", "EXTRACTED", 1.0)]),
        "target": EntityNode("target", "x", "Target", "", []),
    }
    top = god_nodes(g, limit=1)
    assert top[0]["slug"] == "target"
    assert top[0]["degree_in"] == 3


def test_god_nodes_limit(tmp_path: Path) -> None:
    g = {str(i): EntityNode(str(i), "x", str(i), "", []) for i in range(20)}
    top = god_nodes(g, limit=5)
    assert len(top) == 5
