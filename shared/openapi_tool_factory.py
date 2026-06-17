"""OpenAPI 3 spec → callable operation descriptors.

Lightweight factory that turns an OpenAPI 3 document into a list of
`OpenAPIOperation` descriptors. Each descriptor:

  - knows its method, URL template, parameter shapes
  - validates a kwargs dict against the spec (path/query/body shapes)
  - builds an `httpx.Request` ready to send (caller owns the client)

Why a descriptor + request, not "just call it"? The hive's existing
risky-action gate (critic + user-confirm) needs to inspect the call
*before* it fires. Returning a Request gives the synthesizer
something to surface for confirmation; the actual `client.send()`
happens behind the `[saas_call]` verb (Phase B) or wherever the user
opts in.

This module deliberately avoids generating runtime Python classes or
writing files. The plan's "persist generated helpers to
gateway/helpers/_generated/" path is wrong for this codebase —
helpers here are catalog-driven, not file-discovered. Wiring the
operations into the helper pool is the consumer's job (admin route,
follow-up commit). What this module gives you is the parser + the
typed call surface, which is the load-bearing part.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin

import httpx


@dataclass(frozen=True)
class OpenAPIParameter:
    """One parameter on an operation."""
    name: str
    location: str   # "path" | "query" | "header"
    required: bool
    schema_type: str = "string"  # crude — "string" | "integer" | "number" | "boolean"


@dataclass(frozen=True)
class OpenAPIOperation:
    """One callable operation extracted from an OpenAPI doc."""
    operation_id: str
    method: str               # "GET" | "POST" | ...
    path: str                 # template with `{name}` placeholders
    summary: str = ""
    parameters: tuple[OpenAPIParameter, ...] = ()
    request_body_required: bool = False
    request_body_keys: tuple[str, ...] = ()  # top-level JSON keys
    server_url: str = ""

    def validate(self, args: dict[str, Any]) -> list[str]:
        """Return a list of validation error strings; empty = ok."""
        errs: list[str] = []
        seen = set(args)
        for p in self.parameters:
            if p.required and p.name not in args:
                errs.append(f"missing required {p.location} param {p.name!r}")
            if p.name in args:
                v = args[p.name]
                if not _matches_type(v, p.schema_type):
                    errs.append(
                        f"param {p.name!r} expected {p.schema_type}, "
                        f"got {type(v).__name__}",
                    )
                seen.discard(p.name)
        if self.request_body_required and "body" not in args:
            errs.append("missing required body")
        if "body" in args:
            b = args["body"]
            if not isinstance(b, dict):
                errs.append("body must be a dict")
        return errs

    def build_request(
        self, args: dict[str, Any], *, headers: dict[str, str] | None = None,
    ) -> httpx.Request:
        """Construct an `httpx.Request` from validated args. Raises
        `ValueError` if validation fails — caller must `validate()`
        first if they want a structured error list."""
        errs = self.validate(args)
        if errs:
            raise ValueError(f"validation failed: {'; '.join(errs)}")

        # Substitute path params.
        path = self.path
        query: dict[str, Any] = {}
        hdrs = dict(headers or {})
        for p in self.parameters:
            if p.name not in args:
                continue
            v = args[p.name]
            if p.location == "path":
                path = path.replace("{" + p.name + "}", str(v))
            elif p.location == "query":
                query[p.name] = v
            elif p.location == "header":
                hdrs[p.name] = str(v)

        url = urljoin(self.server_url + "/", path.lstrip("/"))
        body = args.get("body") if self.request_body_required or "body" in args else None
        return httpx.Request(
            method=self.method.upper(), url=url,
            params=query or None,
            json=body, headers=hdrs or None,
        )


def _matches_type(v: Any, expected: str) -> bool:
    if expected == "string":
        return isinstance(v, str)
    if expected == "integer":
        return isinstance(v, int) and not isinstance(v, bool)
    if expected == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    if expected == "boolean":
        return isinstance(v, bool)
    # Unknown spec type → permissive (don't block on schema we
    # didn't bother modelling).
    return True


@dataclass(frozen=True)
class OpenAPIFactory:
    """Result of parsing one OpenAPI document."""
    namespace: str
    operations: tuple[OpenAPIOperation, ...] = field(default_factory=tuple)
    server_url: str = ""

    def operation(self, operation_id: str) -> OpenAPIOperation:
        for op in self.operations:
            if op.operation_id == operation_id:
                return op
        raise KeyError(operation_id)

    def list_ids(self) -> list[str]:
        return [op.operation_id for op in self.operations]


def from_openapi(
    spec: dict[str, Any], *, namespace: str,
    server_url_override: str | None = None,
) -> OpenAPIFactory:
    """Parse an OpenAPI 3 doc into a factory.

    Skips operations missing `operationId` (we use it as the unique
    helper key) and skips paths/methods we don't know how to call.
    """
    if not isinstance(spec, dict):
        raise TypeError("spec must be a dict")
    if "paths" not in spec or not isinstance(spec["paths"], dict):
        raise ValueError("spec missing 'paths' object")

    server_url = server_url_override or _pick_server(spec)
    ops: list[OpenAPIOperation] = []

    for path, methods in spec["paths"].items():
        if not isinstance(methods, dict):
            continue
        path_level_params = _parse_params(methods.get("parameters") or [])
        for method, op in methods.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                continue
            if not isinstance(op, dict):
                continue
            opid = op.get("operationId")
            if not opid or not isinstance(opid, str):
                continue
            params = path_level_params + _parse_params(op.get("parameters") or [])
            body = op.get("requestBody") or {}
            body_required = bool(body.get("required"))
            body_keys = _body_top_keys(body)
            ops.append(OpenAPIOperation(
                operation_id=opid,
                method=method.upper(),
                path=path,
                summary=str(op.get("summary") or "")[:200],
                parameters=tuple(params),
                request_body_required=body_required,
                request_body_keys=tuple(body_keys),
                server_url=server_url,
            ))

    # Detect duplicate operation IDs — these would collide if a
    # consumer indexes by ID.
    seen: set[str] = set()
    for op in ops:
        if op.operation_id in seen:
            raise ValueError(f"duplicate operationId: {op.operation_id!r}")
        seen.add(op.operation_id)

    return OpenAPIFactory(
        namespace=namespace,
        operations=tuple(ops),
        server_url=server_url,
    )


def _pick_server(spec: dict[str, Any]) -> str:
    servers = spec.get("servers") or []
    if servers and isinstance(servers, list) and isinstance(servers[0], dict):
        return str(servers[0].get("url") or "")
    return ""


def _parse_params(raw: list[Any]) -> list[OpenAPIParameter]:
    out: list[OpenAPIParameter] = []
    for r in raw:
        if not isinstance(r, dict):
            continue
        name = r.get("name")
        loc = r.get("in")
        if not name or loc not in {"path", "query", "header"}:
            continue
        schema = r.get("schema") or {}
        st = str(schema.get("type") or "string")
        out.append(OpenAPIParameter(
            name=str(name), location=str(loc),
            required=bool(r.get("required") or loc == "path"),
            schema_type=st,
        ))
    return out


def _body_top_keys(body: dict[str, Any]) -> list[str]:
    content = body.get("content") or {}
    if not isinstance(content, dict):
        return []
    json_body = content.get("application/json") or {}
    schema = json_body.get("schema") or {}
    props = schema.get("properties") or {}
    if isinstance(props, dict):
        return [str(k) for k in props.keys()]
    return []


__all__ = [
    "OpenAPIFactory",
    "OpenAPIOperation",
    "OpenAPIParameter",
    "from_openapi",
]
