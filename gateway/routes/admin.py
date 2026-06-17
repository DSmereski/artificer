"""/admin/* — vanilla HTML control panel for the hive worker pool.

Phase 1 ships only the nodes page. Static files live in
`gateway/static/admin/`. Auth happens client-side via the Bearer token
the user pastes on first load (stored in sessionStorage). The browser
sends it on /v1/nodes calls; the gateway enforces it the same way
every other owner endpoint does.

Defence in depth:
- Origin gate: only loopback / RFC1918 / tailscale CGNAT may reach
  /admin/*. Stops a misconfigured public bind from also exposing the
  UI.
- CSP: locks script/style/connect to self; frame-ancestors none.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from gateway.deps import require_device, state
from shared.openapi_tool_factory import from_openapi


router = APIRouter(prefix="/admin", tags=["hive-admin"])

_STATIC = Path(__file__).resolve().parent.parent / "static" / "admin"

_ALLOWED_CIDRS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("100.64.0.0/10"),  # tailscale CGNAT
    ipaddress.ip_network("fd00::/8"),        # tailscale ULA
)

_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    # nodes.html ships an inline <style> block; allow inline styles
    # but nothing else inline.
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'none'; "
    "form-action 'none'"
)


def _admin_origin_allowed(host: str | None) -> bool:
    """True iff `host` is loopback, RFC1918, tailscale CGNAT, or the
    TestClient sentinel. Unknown / unparseable hosts are blocked.
    """
    if not host:
        return False
    if host == "testclient":
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(ip in net for net in _ALLOWED_CIDRS)


def _require_admin_origin(request: Request) -> None:
    host = request.client.host if request.client else None
    if not _admin_origin_allowed(host):
        raise HTTPException(
            status_code=403,
            detail="admin restricted to local/private network",
        )


def _serve(name: str) -> FileResponse:
    path = _STATIC / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="not found")
    resp = FileResponse(path)
    resp.headers["Content-Security-Policy"] = _CSP
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    return resp


@router.get("/", include_in_schema=False, dependencies=[Depends(_require_admin_origin)])
def admin_index() -> FileResponse:
    return _serve("index.html")


@router.get("/nodes", include_in_schema=False, dependencies=[Depends(_require_admin_origin)])
def admin_nodes() -> FileResponse:
    return _serve("nodes.html")


@router.get("/nodes.js", include_in_schema=False, dependencies=[Depends(_require_admin_origin)])
def admin_nodes_js() -> FileResponse:
    return _serve("nodes.js")


@router.get("/jobs", include_in_schema=False, dependencies=[Depends(_require_admin_origin)])
def admin_jobs() -> FileResponse:
    return _serve("jobs.html")


@router.get("/jobs.js", include_in_schema=False, dependencies=[Depends(_require_admin_origin)])
def admin_jobs_js() -> FileResponse:
    return _serve("jobs.js")


# Phase E.2 — OpenAPI import. Owner-only POST that parses an OpenAPI 3
# document into operation descriptors and persists the parsed metadata
# under `<state_dir>/openapi/<namespace>.json`. Idempotent on
# `(sha256(spec), namespace)`; differing spec for an existing namespace
# returns 409 so the caller has to pick a fresh namespace rather than
# silently overwrite a surface that other tools may already reference.
_NAMESPACE_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _spec_hash(spec: dict) -> str:
    canonical = json.dumps(spec, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _operation_to_dict(op) -> dict:
    d = asdict(op)
    # parameters dataclasses → list[dict]; tuples → lists
    d["parameters"] = [asdict(p) for p in op.parameters]
    d["request_body_keys"] = list(op.request_body_keys)
    return d


@router.post(
    "/openapi/import",
    include_in_schema=False,
    dependencies=[Depends(_require_admin_origin), Depends(require_device)],
)
def admin_openapi_import(
    request: Request,
    body: dict = Body(...),
) -> dict:
    namespace = body.get("namespace")
    spec = body.get("spec")
    if not isinstance(namespace, str) or not _NAMESPACE_RE.match(namespace):
        raise HTTPException(
            status_code=400,
            detail="namespace must match [A-Za-z0-9_-]+",
        )
    if not isinstance(spec, dict):
        raise HTTPException(status_code=400, detail="spec must be a JSON object")

    try:
        factory = from_openapi(spec, namespace=namespace)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"invalid spec: {exc}") from exc

    spec_hash = _spec_hash(spec)
    state_dir: Path = state(request).config.state_dir
    out_dir = state_dir / "openapi"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{namespace}.json"

    idempotent = False
    if out_file.is_file():
        try:
            existing = json.loads(out_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = None
        if isinstance(existing, dict) and existing.get("hash") == spec_hash:
            idempotent = True
        else:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"namespace {namespace!r} already imported with a "
                    "different spec; pick a new namespace"
                ),
            )

    ops_dicts = [_operation_to_dict(op) for op in factory.operations]
    record = {
        "namespace": namespace,
        "hash": spec_hash,
        "server_url": factory.server_url,
        "operations": ops_dicts,
    }
    if not idempotent:
        out_file.write_text(json.dumps(record, indent=2), encoding="utf-8")

    return {
        "namespace": namespace,
        "hash": spec_hash,
        "server_url": factory.server_url,
        "operation_count": len(ops_dicts),
        "operations": [
            {
                "operation_id": o["operation_id"],
                "method": o["method"],
                "path": o["path"],
                "summary": o["summary"],
            }
            for o in ops_dicts
        ],
        "idempotent": idempotent,
    }
