"""Read-side graph traversal over entity_page relationships.

The `entity_page` table's `relationships` JSON column holds confidence-
tagged directed edges written live by the chat coordinator (graphify-
shaped: [{target_slug, label, confidence}, ...]). This module loads
those edges into an in-memory adjacency structure and exposes pure
functions for graph queries: neighbours, shortest path, explanation,
and high-degree ("god") nodes.

No new heavy dependencies: adjacency is a plain dict-of-lists; BFS is
hand-rolled. networkx is NOT used (it is not in requirements.txt).
"""

from __future__ import annotations

import json
import sqlite3
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator


# Confidence ordering for edge weight: EXTRACTED > INFERRED > AMBIGUOUS.
# Lower weight = preferred in shortest-path search so high-confidence edges
# dominate.
_CONF_WEIGHT: dict[str, float] = {
    "EXTRACTED": 1.0,
    "INFERRED": 2.0,
    "AMBIGUOUS": 4.0,
}
_DEFAULT_WEIGHT = 3.0


@dataclass
class Edge:
    target: str
    label: str
    confidence: str
    weight: float


@dataclass
class EntityNode:
    slug: str
    kind: str
    title: str
    compiled_truth: str
    edges: list[Edge] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class GraphQueryResult:
    """Generic result container for graph queries."""
    slugs: list[str]
    # Human-readable description of why these slugs were chosen.
    explanation: str = ""
    # Edge labels along the path (parallel list to slugs[1:] for paths).
    labels: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_graph(conn: sqlite3.Connection) -> dict[str, EntityNode]:
    """Read all entity_page rows and build an in-memory adjacency map.

    Returns a dict keyed by slug. Edges are loaded from the JSON
    `relationships` column. Missing or malformed rows are skipped.
    """
    rows = conn.execute(
        """SELECT id, kind, title, compiled_truth, relationships
           FROM entity_page"""
    ).fetchall()

    nodes: dict[str, EntityNode] = {}
    for row in rows:
        slug = str(row["id"])
        nodes[slug] = EntityNode(
            slug=slug,
            kind=str(row["kind"] or ""),
            title=str(row["title"] or ""),
            compiled_truth=str(row["compiled_truth"] or ""),
        )

    for row in rows:
        slug = str(row["id"])
        raw = row["relationships"]
        if not raw:
            continue
        try:
            rels = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(rels, list):
            continue
        for rel in rels:
            if not isinstance(rel, dict):
                continue
            target = str(rel.get("target_slug", "")).strip()
            if not target:
                continue
            label = str(rel.get("label", "")).strip()
            conf = str(rel.get("confidence", "INFERRED")).upper()
            weight = _CONF_WEIGHT.get(conf, _DEFAULT_WEIGHT)
            nodes[slug].edges.append(Edge(
                target=target, label=label, confidence=conf, weight=weight,
            ))

    return nodes


def _open_db(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Pure query functions
# ---------------------------------------------------------------------------


def neighbors(
    slug: str,
    graph: dict[str, EntityNode],
    *,
    depth: int = 1,
) -> list[str]:
    """Return slugs reachable from `slug` within `depth` hops.

    Excludes `slug` itself. Traverses edges in BOTH directions (outbound
    from slug, and inbound edges that point TO slug) so the graph behaves
    as undirected for neighbour discovery.

    Returns a deduplicated list ordered by BFS discovery order (closest
    first).
    """
    if slug not in graph:
        return []

    # Build a reverse-edge index so inbound edges are traversable.
    inbound: dict[str, list[str]] = {}
    for src, node in graph.items():
        for edge in node.edges:
            inbound.setdefault(edge.target, []).append(src)

    visited: set[str] = {slug}
    queue: deque[tuple[str, int]] = deque([(slug, 0)])
    result: list[str] = []

    while queue:
        current, d = queue.popleft()
        if d >= depth:
            continue
        # Outbound neighbours.
        for edge in graph.get(current, EntityNode(current, "", "", "")).edges:
            if edge.target not in visited:
                visited.add(edge.target)
                result.append(edge.target)
                queue.append((edge.target, d + 1))
        # Inbound neighbours.
        for src in inbound.get(current, []):
            if src not in visited:
                visited.add(src)
                result.append(src)
                queue.append((src, d + 1))

    return result


def shortest_path(
    a: str,
    b: str,
    graph: dict[str, EntityNode],
) -> GraphQueryResult:
    """Find the shortest path from `a` to `b` using BFS.

    Prefers higher-confidence edges (EXTRACTED < INFERRED < AMBIGUOUS)
    via a simple greedy priority that de-queues extracted edges before
    inferred ones within the same BFS layer. Falls back to undirected
    traversal (follows inbound edges too) so isolated subgraphs are still
    reachable.

    Returns a `GraphQueryResult` whose `slugs` is the node sequence from
    `a` to `b` inclusive, and `labels` is the edge-label sequence
    (len == len(slugs) - 1). Returns empty slugs when no path exists.
    """
    if a not in graph or b not in graph:
        return GraphQueryResult(slugs=[], explanation="one or both slugs not in graph")
    if a == b:
        return GraphQueryResult(slugs=[a], explanation="same node")

    # Build reverse-edge index.
    inbound: dict[str, list[tuple[str, str, float]]] = {}
    for src, node in graph.items():
        for edge in node.edges:
            inbound.setdefault(edge.target, []).append((src, edge.label, edge.weight))

    # BFS with predecessor tracking. Queue entries: (slug, accumulated_weight).
    # predecessor[slug] = (prev_slug, edge_label)
    predecessor: dict[str, tuple[str, str]] = {}
    visited: set[str] = {a}
    # Sort edges by weight so high-confidence edges are explored first.
    def _sorted_edges(slug: str) -> Iterator[Edge]:
        return iter(sorted(
            graph.get(slug, EntityNode(slug, "", "", "")).edges,
            key=lambda e: e.weight,
        ))

    queue: deque[str] = deque([a])

    while queue:
        current = queue.popleft()
        # Outbound edges.
        for edge in _sorted_edges(current):
            if edge.target not in visited:
                visited.add(edge.target)
                predecessor[edge.target] = (current, edge.label)
                if edge.target == b:
                    return _reconstruct(a, b, predecessor)
                queue.append(edge.target)
        # Inbound edges (undirected fallback).
        for src, label, _ in sorted(inbound.get(current, []), key=lambda x: x[2]):
            if src not in visited:
                visited.add(src)
                predecessor[src] = (current, label)
                if src == b:
                    return _reconstruct(a, b, predecessor)
                queue.append(src)

    return GraphQueryResult(slugs=[], explanation=f"no path from {a!r} to {b!r}")


def _reconstruct(
    a: str, b: str, predecessor: dict[str, tuple[str, str]],
) -> GraphQueryResult:
    path: list[str] = []
    labels: list[str] = []
    cur = b
    while cur != a:
        prev, label = predecessor[cur]
        path.append(cur)
        labels.append(label)
        cur = prev
    path.append(a)
    path.reverse()
    labels.reverse()
    hops = len(path) - 1
    return GraphQueryResult(
        slugs=path,
        labels=labels,
        explanation=f"path of {hops} hop(s)",
    )


def explain(slug: str, graph: dict[str, EntityNode]) -> dict:
    """Return a human-readable explanation for `slug`.

    Includes the entity's compiled_truth and a summary of its immediate
    edges (outbound only, grouped by confidence tier).
    """
    node = graph.get(slug)
    if node is None:
        return {"slug": slug, "found": False}
    by_conf: dict[str, list[str]] = {}
    for edge in node.edges:
        tier = edge.confidence
        desc = f"{edge.label} → {edge.target}"
        by_conf.setdefault(tier, []).append(desc)
    return {
        "slug": slug,
        "found": True,
        "kind": node.kind,
        "title": node.title,
        "compiled_truth": node.compiled_truth,
        "edges_by_confidence": by_conf,
        "degree_out": len(node.edges),
    }


def god_nodes(
    graph: dict[str, EntityNode],
    *,
    limit: int = 10,
) -> list[dict]:
    """Return the `limit` highest-degree nodes (total in+out edges).

    "God nodes" are highly connected entities that are likely central to
    the knowledge graph — good candidates for the `/v1/graph/god-nodes`
    endpoint to surface as a starting point for exploration.
    """
    # Count in-degree too.
    in_degree: dict[str, int] = {s: 0 for s in graph}
    for node in graph.values():
        for edge in node.edges:
            if edge.target in in_degree:
                in_degree[edge.target] += 1

    scored = []
    for slug, node in graph.items():
        out = len(node.edges)
        in_ = in_degree.get(slug, 0)
        scored.append({
            "slug": slug,
            "title": node.title,
            "kind": node.kind,
            "degree": out + in_,
            "degree_out": out,
            "degree_in": in_,
        })

    scored.sort(key=lambda x: x["degree"], reverse=True)
    return scored[:limit]


# ---------------------------------------------------------------------------
# Convenience loader (filesystem path variant)
# ---------------------------------------------------------------------------


def load_graph_from_db(db_path: str | Path) -> dict[str, EntityNode]:
    """Open `db_path` and return a loaded graph. Caller must ensure the
    path points to a vault.db with an `entity_page` table."""
    conn = _open_db(db_path)
    try:
        return load_graph(conn)
    finally:
        conn.close()
