"""API tests for the Knowledge Management REST routes."""

import pytest
from fastapi.testclient import TestClient

from src.app import create_app
from src.routes import knowledge_routes


@pytest.fixture
def client():
    # Fresh service per test so state never leaks between cases.
    knowledge_routes._service = knowledge_routes.KnowledgeService()
    return TestClient(create_app())


def test_post_knowledge_creates_file(client):
    """AC: test_knowledge_api creates a KnowledgeFile via POST /knowledge."""
    resp = client.post(
        "/api/v1/knowledge",
        json={"title": "Onboarding Notes", "content_hash": "abc123"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["title"] == "Onboarding Notes"
    assert body["file_id"]


def test_get_knowledge_returns_file_id_and_title(client):
    """AC: GET /api/v1/knowledge returns 200 with file_id and title."""
    created = client.post("/api/v1/knowledge", json={"title": "Spec"}).json()
    resp = client.get(f"/api/v1/knowledge/{created['file_id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["file_id"] == created["file_id"]
    assert body["title"] == "Spec"


def test_link_tool_returns_201(client):
    """AC: POST /api/v1/knowledge/{id}/link-tool returns 201 for a valid tool."""
    created = client.post("/api/v1/knowledge", json={"title": "Guide"}).json()
    resp = client.post(
        f"/api/v1/knowledge/{created['file_id']}/link-tool",
        json={"tool_id": "tool-42", "link_type": "requires"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["tool_id"] == "tool-42"
    assert body["knowledge_file_id"] == created["file_id"]
    assert body["link_type"] == "requires"


def test_link_tool_then_get_includes_link(client):
    created = client.post("/api/v1/knowledge", json={"title": "Guide"}).json()
    client.post(
        f"/api/v1/knowledge/{created['file_id']}/link-tool",
        json={"tool_id": "tool-1"},
    )
    body = client.get(f"/api/v1/knowledge/{created['file_id']}").json()
    assert len(body["links"]) == 1
    assert body["links"][0]["tool_id"] == "tool-1"


def test_get_missing_file_returns_404(client):
    assert client.get("/api/v1/knowledge/does-not-exist").status_code == 404


def test_link_tool_missing_file_returns_404(client):
    resp = client.post(
        "/api/v1/knowledge/nope/link-tool",
        json={"tool_id": "t-1"},
    )
    assert resp.status_code == 404
