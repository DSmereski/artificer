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
