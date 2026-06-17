"""QA acceptance tests for the Knowledge Management REST API (T-0357).

Independent of the hive-authored ``test_knowledge_api.py``; each test maps to a
stated acceptance criterion and asserts request/response serialization matches
``src.models.knowledge``.
"""

import pytest
from fastapi.testclient import TestClient

from src.app import create_app
from src.models.knowledge import LinkType
from src.routes import knowledge_routes


@pytest.fixture
def client():
    # Fresh in-memory service per test so state never leaks between cases.
    knowledge_routes._service = knowledge_routes.KnowledgeService()
    return TestClient(create_app())


# --- AC 1: POST /knowledge creates a KnowledgeFile ---------------------------


def test_post_knowledge_creates_file_with_201_and_serialized_model(client):
    resp = client.post(
        "/api/v1/knowledge",
        json={
            "title": "Runbook",
            "content_hash": "deadbeef",
            "description": "ops runbook",
            "tags": ["ops", "oncall"],
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    # Response mirrors the KnowledgeFile data model.
    assert body["file_id"]
    assert body["title"] == "Runbook"
    assert body["content_hash"] == "deadbeef"
    assert body["description"] == "ops runbook"
    assert body["tags"] == ["ops", "oncall"]
    assert body["links"] == []
    # Persisted in the backing service under the returned id.
    stored = knowledge_routes.get_service().get_file(body["file_id"])
    assert stored is not None and stored.title == "Runbook"


def test_post_knowledge_rejects_blank_title(client):
    # title has min_length=1; empty string must fail validation (422).
    resp = client.post("/api/v1/knowledge", json={"title": ""})
    assert resp.status_code == 422


# --- AC 2: GET /knowledge/{id} returns 200 with file_id and title ------------


def test_get_knowledge_returns_200_with_file_id_and_title(client):
    created = client.post("/api/v1/knowledge", json={"title": "Spec Doc"}).json()
    resp = client.get(f"/api/v1/knowledge/{created['file_id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, dict)
    assert body["file_id"] == created["file_id"]
    assert body["title"] == "Spec Doc"


def test_get_unknown_file_returns_404(client):
    resp = client.get("/api/v1/knowledge/missing-id")
    assert resp.status_code == 404


# --- AC 3: POST /knowledge/{id}/link-tool returns 201 for a valid tool -------


def test_link_tool_returns_201_for_valid_tool(client):
    created = client.post("/api/v1/knowledge", json={"title": "Guide"}).json()
    resp = client.post(
        f"/api/v1/knowledge/{created['file_id']}/link-tool",
        json={"tool_id": "tool-7", "link_type": "requires"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["tool_id"] == "tool-7"
    assert body["knowledge_file_id"] == created["file_id"]
    assert body["link_type"] == LinkType.REQUIRES.value
    assert body["status"] == "active"


def test_link_tool_default_link_type_is_requires(client):
    created = client.post("/api/v1/knowledge", json={"title": "Guide"}).json()
    resp = client.post(
        f"/api/v1/knowledge/{created['file_id']}/link-tool",
        json={"tool_id": "tool-9"},
    )
    assert resp.status_code == 201
    assert resp.json()["link_type"] == "requires"


def test_put_link_tool_alias_also_returns_201(client):
    # Spec lists PUT /{id}/link-tool; route serves both POST and PUT.
    created = client.post("/api/v1/knowledge", json={"title": "Guide"}).json()
    resp = client.put(
        f"/api/v1/knowledge/{created['file_id']}/link-tool",
        json={"tool_id": "tool-put", "link_type": "optional"},
    )
    assert resp.status_code == 201
    assert resp.json()["link_type"] == "optional"


def test_link_tool_on_missing_file_returns_404(client):
    resp = client.post(
        "/api/v1/knowledge/no-such-file/link-tool",
        json={"tool_id": "t-1"},
    )
    assert resp.status_code == 404


# --- Cross-cutting: retrieve reflects linked tools ---------------------------


def test_get_after_link_includes_link_in_response(client):
    created = client.post("/api/v1/knowledge", json={"title": "Guide"}).json()
    fid = created["file_id"]
    client.post(f"/api/v1/knowledge/{fid}/link-tool", json={"tool_id": "tool-A"})
    client.post(
        f"/api/v1/knowledge/{fid}/link-tool",
        json={"tool_id": "tool-B", "link_type": "extends"},
    )
    body = client.get(f"/api/v1/knowledge/{fid}").json()
    tool_ids = [link["tool_id"] for link in body["links"]]
    assert tool_ids == ["tool-A", "tool-B"]
    assert body["links"][1]["link_type"] == "extends"
