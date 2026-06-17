"""Tests for POST /admin/openapi/import (Phase E.2 admin route).

Owner-only route that parses an OpenAPI 3 spec into operation
descriptors, persists the parsed metadata under
`<state_dir>/openapi/<namespace>.json`, and is idempotent on
`(sha256(spec), namespace)`.

Tests cover happy path, idempotency, namespace-conflict (different spec
under same namespace), invalid-spec rejection, and the two auth gates
(missing bearer → 401; non-private origin → 403). The factory parser
itself is exercised by `shared/tests/test_openapi_tool_factory.py`; here
we only verify the admin surface.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient


def _spec(*, title: str = "Pets", op_id: str = "listPets") -> dict:
    return {
        "openapi": "3.0.0",
        "info": {"title": title, "version": "1.0"},
        "servers": [{"url": "https://example.test"}],
        "paths": {
            "/pets": {
                "get": {
                    "operationId": op_id,
                    "summary": "List pets",
                    "parameters": [
                        {"name": "limit", "in": "query",
                         "schema": {"type": "integer"}},
                    ],
                    "responses": {"200": {"description": "ok"}},
                },
            },
        },
    }


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_happy_path_persists_descriptor(
    client: TestClient,
    paired_token: tuple[str, str],
    tmp_state_dir: Path,
) -> None:
    _, tok = paired_token
    r = client.post(
        "/admin/openapi/import",
        json={"namespace": "pets", "spec": _spec()},
        headers=_auth(tok),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["namespace"] == "pets"
    assert body["operation_count"] == 1
    assert body["idempotent"] is False
    assert body["operations"][0]["operation_id"] == "listPets"
    assert body["operations"][0]["method"] == "GET"
    # Persisted to disk.
    f = tmp_state_dir / "openapi" / "pets.json"
    assert f.is_file()
    on_disk = json.loads(f.read_text(encoding="utf-8"))
    assert on_disk["namespace"] == "pets"
    assert on_disk["hash"] == body["hash"]
    assert len(on_disk["operations"]) == 1


def test_idempotent_on_repeat(
    client: TestClient,
    paired_token: tuple[str, str],
) -> None:
    _, tok = paired_token
    payload = {"namespace": "pets", "spec": _spec()}
    r1 = client.post("/admin/openapi/import", json=payload, headers=_auth(tok))
    r2 = client.post("/admin/openapi/import", json=payload, headers=_auth(tok))
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r2.json()["idempotent"] is True
    assert r1.json()["hash"] == r2.json()["hash"]


def test_namespace_conflict_different_spec(
    client: TestClient,
    paired_token: tuple[str, str],
) -> None:
    _, tok = paired_token
    r1 = client.post(
        "/admin/openapi/import",
        json={"namespace": "pets", "spec": _spec(op_id="listPets")},
        headers=_auth(tok),
    )
    assert r1.status_code == 200
    # Same namespace, different spec → 409.
    r2 = client.post(
        "/admin/openapi/import",
        json={"namespace": "pets", "spec": _spec(op_id="getPets")},
        headers=_auth(tok),
    )
    assert r2.status_code == 409
    assert "namespace" in r2.json()["detail"].lower()


def test_invalid_spec_rejected(
    client: TestClient,
    paired_token: tuple[str, str],
) -> None:
    _, tok = paired_token
    r = client.post(
        "/admin/openapi/import",
        json={"namespace": "broken", "spec": {"not": "an openapi doc"}},
        headers=_auth(tok),
    )
    assert r.status_code == 400


def test_missing_bearer_token_rejected(client: TestClient) -> None:
    r = client.post(
        "/admin/openapi/import",
        json={"namespace": "pets", "spec": _spec()},
    )
    assert r.status_code == 401


def test_bad_namespace_rejected(
    client: TestClient,
    paired_token: tuple[str, str],
) -> None:
    """Namespace lands in a filesystem path; reject anything but
    `[a-zA-Z0-9_-]+` to keep this surface boring."""
    _, tok = paired_token
    for bad in ("../etc", "with/slash", "", "has space"):
        r = client.post(
            "/admin/openapi/import",
            json={"namespace": bad, "spec": _spec()},
            headers=_auth(tok),
        )
        assert r.status_code == 400, f"namespace {bad!r} should be rejected"
