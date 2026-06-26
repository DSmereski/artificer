"""Graph read-side routes: traversal over entity_page relationships.

All endpoints are read-only and require a valid device Bearer token.
They load the entity_page graph from the vault DB on each request —
the DB is small (O(hundreds) of entities), so no in-process cache is
needed. For production deployments with thousands of entities, a
TTL cache can be added transparently.

Endpoints
---------
GET /v1/graph/neighbors?slug=<slug>[&depth=1]
    Entities reachable from <slug> within <depth> hops (default 1).

GET /v1/graph/path?from=<slug>&to=<slug>
    Shortest path between two entities (BFS, prefers high-confidence edges).

GET /v1/graph/explain?slug=<slug>
    compiled_truth + edge summary for a single entity.

GET /v1/graph/god-nodes[?limit=10]
    Highest-degree entities in the graph.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from gateway.deps import require_device, require_device_or_loopback, state

router = APIRouter(prefix="/v1/graph", tags=["graph"])
log = logging.getLogger("gateway.graph")


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class NeighborsResponse(BaseModel):
    slug: str
    depth: int
    neighbors: list[str]


class PathNode(BaseModel):
    slug: str
    label: str  # edge label leading TO this node ("" for the start node)


class PathResponse(BaseModel):
    from_slug: str
    to_slug: str
    found: bool
    path: list[PathNode]
    explanation: str


class ExplainEdge(BaseModel):
    target: str
    label: str
    confidence: str


class ExplainResponse(BaseModel):
    slug: str
    found: bool
    kind: str = ""
    title: str = ""
    compiled_truth: str = ""
    edges: list[ExplainEdge] = []
    degree_out: int = 0


class GodNodeEntry(BaseModel):
    slug: str
    title: str
    kind: str
    degree: int
    degree_out: int
    degree_in: int


class GodNodesResponse(BaseModel):
    nodes: list[GodNodeEntry]


class IsolatedNodeEntry(BaseModel):
    slug: str
    title: str
    kind: str


class BridgeNodeEntry(BaseModel):
    slug: str
    title: str
    kind: str
    # Number of connected components the graph splits into if this node
    # (and its edges) are removed. A true articulation point has split >= 2.
    split: int


class InsightsResponse(BaseModel):
    isolated: list[IsolatedNodeEntry]
    bridges: list[BridgeNodeEntry]
    # A human-readable note when optional data (e.g. community data) is
    # unavailable so callers know the response is partial.
    notes: list[str]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _db_path(request: Request) -> Path:
    """Resolve the vault.db path from app config."""
    st = state(request)
    vault_path = st.config.vault_path
    return vault_path / ".vault-writer" / "vault.db"


def _load(request: Request):
    """Load the entity graph from the vault DB.

    Raises 503 when the DB file does not exist yet, or when it exists but
    the entity_page table has not been created (daemon not started / no
    entity pages written yet). 503 is preferable to 500 because it signals
    a configuration / readiness issue rather than a runtime bug.
    """
    import sqlite3 as _sqlite3
    from vault_writer.graph_query import load_graph_from_db
    db = _db_path(request)
    if not db.exists():
        raise HTTPException(
            status_code=503,
            detail="vault DB not found — is the vault-writer daemon running?",
        )
    try:
        return load_graph_from_db(db)
    except _sqlite3.OperationalError as exc:
        # "no such table: entity_page" means the daemon hasn't initialised
        # the schema yet — surface as 503 (service not ready).
        err = str(exc).lower()
        if "no such table" in err:
            raise HTTPException(
                status_code=503,
                detail="entity_page table not found — vault-writer daemon not started",
            ) from exc
        log.error("graph load failed (sql): %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"graph load error: {exc}") from exc
    except Exception as exc:
        log.error("graph load failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"graph load error: {exc}") from exc


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/neighbors", response_model=NeighborsResponse)
def graph_neighbors(
    slug: str = Query(..., min_length=1, max_length=200),
    depth: int = Query(default=1, ge=1, le=4),
    device=Depends(require_device_or_loopback),
    request: Request = None,
) -> NeighborsResponse:
    """Return entities reachable from `slug` within `depth` hops."""
    from vault_writer.graph_query import neighbors
    graph = _load(request)
    if slug not in graph:
        raise HTTPException(status_code=404, detail=f"entity {slug!r} not found")
    result = neighbors(slug, graph, depth=depth)
    return NeighborsResponse(slug=slug, depth=depth, neighbors=result)


@router.get("/path", response_model=PathResponse)
def graph_path(
    from_slug: str = Query(..., alias="from", min_length=1, max_length=200),
    to_slug: str = Query(..., alias="to", min_length=1, max_length=200),
    device=Depends(require_device_or_loopback),
    request: Request = None,
) -> PathResponse:
    """Find the shortest path between two entities."""
    from vault_writer.graph_query import shortest_path
    graph = _load(request)
    result = shortest_path(from_slug, to_slug, graph)
    path_nodes: list[PathNode] = []
    if result.slugs:
        path_nodes.append(PathNode(slug=result.slugs[0], label=""))
        for slug, label in zip(result.slugs[1:], result.labels):
            path_nodes.append(PathNode(slug=slug, label=label))
    return PathResponse(
        from_slug=from_slug,
        to_slug=to_slug,
        found=bool(result.slugs),
        path=path_nodes,
        explanation=result.explanation,
    )


@router.get("/explain", response_model=ExplainResponse)
def graph_explain(
    slug: str = Query(..., min_length=1, max_length=200),
    device=Depends(require_device_or_loopback),
    request: Request = None,
) -> ExplainResponse:
    """Return compiled_truth and edge summary for a single entity."""
    from vault_writer.graph_query import explain
    graph = _load(request)
    info = explain(slug, graph)
    if not info.get("found"):
        raise HTTPException(status_code=404, detail=f"entity {slug!r} not found")
    edges: list[ExplainEdge] = []
    node = graph.get(slug)
    if node:
        for edge in node.edges:
            edges.append(ExplainEdge(
                target=edge.target, label=edge.label, confidence=edge.confidence,
            ))
    return ExplainResponse(
        slug=slug,
        found=True,
        kind=info.get("kind", ""),
        title=info.get("title", ""),
        compiled_truth=info.get("compiled_truth", ""),
        edges=edges,
        degree_out=info.get("degree_out", 0),
    )


@router.get("/god-nodes", response_model=GodNodesResponse)
def graph_god_nodes(
    limit: int = Query(default=10, ge=1, le=100),
    device=Depends(require_device_or_loopback),
    request: Request = None,
) -> GodNodesResponse:
    """Return the highest-degree entities in the graph."""
    from vault_writer.graph_query import god_nodes
    graph = _load(request)
    entries = god_nodes(graph, limit=limit)
    return GodNodesResponse(
        nodes=[GodNodeEntry(**e) for e in entries]
    )


# ---------------------------------------------------------------------------
# /v1/graph/insights helpers
# ---------------------------------------------------------------------------


def _find_isolated(graph: dict) -> list[dict]:
    """Nodes with no outbound AND no inbound edges."""
    has_inbound: set[str] = set()
    for node in graph.values():
        for edge in node.edges:
            has_inbound.add(edge.target)

    isolated = []
    for slug, node in graph.items():
        if not node.edges and slug not in has_inbound:
            isolated.append({"slug": slug, "title": node.title, "kind": node.kind})
    return isolated


def _count_components(slugs: set[str], adj: dict[str, set[str]]) -> int:
    """Count connected components among `slugs` using the adjacency map."""
    visited: set[str] = set()
    count = 0
    for start in slugs:
        if start in visited:
            continue
        count += 1
        stack = [start]
        while stack:
            cur = stack.pop()
            if cur in visited:
                continue
            visited.add(cur)
            for nb in adj.get(cur, set()):
                if nb in slugs and nb not in visited:
                    stack.append(nb)
    return count


def _find_bridges(graph: dict, *, max_nodes: int = 2000) -> list[dict]:
    """Articulation-point detection via component-split counting.

    For each node, temporarily remove it from the undirected adjacency and
    count connected components. A node is a bridge iff the component count
    rises. Bounded to `max_nodes` to keep latency predictable on large graphs.

    Returns list of dicts with slug, title, kind, split.
    """
    all_slugs = set(graph.keys())
    if len(all_slugs) > max_nodes:
        # Sample the most connected nodes only.
        all_slugs = set(
            sorted(all_slugs, key=lambda s: len(graph[s].edges), reverse=True)[:max_nodes]
        )

    # Build undirected adjacency (outbound + inbound).
    adj: dict[str, set[str]] = {s: set() for s in all_slugs}
    for slug in all_slugs:
        for edge in graph[slug].edges:
            if edge.target in all_slugs:
                adj[slug].add(edge.target)
                adj[edge.target].add(slug)

    base_components = _count_components(all_slugs, adj)

    bridges = []
    for slug in all_slugs:
        remaining = all_slugs - {slug}
        # Temporarily remove this node's edges from the adjacency view.
        reduced_adj = {s: adj[s] - {slug} for s in remaining}
        split = _count_components(remaining, reduced_adj)
        if split > base_components:
            node = graph[slug]
            bridges.append({
                "slug": slug,
                "title": node.title,
                "kind": node.kind,
                "split": split,
            })

    bridges.sort(key=lambda x: x["split"], reverse=True)
    return bridges


@router.get("/insights", response_model=InsightsResponse)
def graph_insights(
    device=Depends(require_device_or_loopback),
    request: Request = None,
) -> InsightsResponse:
    """Return structural insights about the entity knowledge graph.

    - **isolated**: nodes with no edges at all (likely stub entries or
      orphaned pages that haven't been connected yet).
    - **bridges**: articulation points — nodes whose removal would split
      the graph into more connected components (high structural risk if
      deleted or unreliable).
    - **notes**: advisory strings when optional analyses are skipped.
    """
    graph = _load(request)
    notes: list[str] = []

    isolated_raw = _find_isolated(graph)
    isolated = [IsolatedNodeEntry(**e) for e in isolated_raw]

    bridges_raw = _find_bridges(graph)
    bridges = [BridgeNodeEntry(**e) for e in bridges_raw]

    if len(graph) > 2000:
        notes.append(
            "bridge detection sampled the top-2000 highest-degree nodes; "
            "some articulation points in low-degree periphery may be missing"
        )

    return InsightsResponse(isolated=isolated, bridges=bridges, notes=notes)
