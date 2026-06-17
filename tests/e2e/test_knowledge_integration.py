"""E2E integration tests for the Knowledge File + Tool Link lifecycle.

Drives the full lifecycle through the public REST surface:
create a KnowledgeFile, link multiple tools, retrieve the file with all
associated links, and unlink a tool.
"""

import pytest
from fastapi.testclient import TestClient

from src.app import create_app
from src.routes import knowledge_routes


@pytest.fixture
def client():
    # Fresh service per test so state never leaks between cases.
    knowledge_routes._service = knowledge_routes.KnowledgeService()
    return TestClient(create_app())


def test_e2e_knowledge(client):
    """Full lifecycle: create file, link two tools, retrieve, unlink one.

    AC:
      - creates a KnowledgeFile and links two tools
      - GET /api/v1/knowledge/{id} returns 200 with an array of linked tools
      - POST /api/v1/knowledge/{id}/unlink-tool returns 204 when unlinking
    """
    # 1. Create a KnowledgeFile.
    create_resp = client.post(
        "/api/v1/knowledge",
        json={"title": "Deploy Runbook", "content_hash": "deadbeef"},
    )
    assert create_resp.status_code == 201
    file_id = create_resp.json()["file_id"]
    assert file_id

    # 2. Link two tools to the file.
    link_a = client.post(
        f"/api/v1/knowledge/{file_id}/link-tool",
        json={"tool_id": "tool-deploy", "link_type": "requires"},
    )
    assert link_a.status_code == 201

    link_b = client.post(
        f"/api/v1/knowledge/{file_id}/link-tool",
        json={"tool_id": "tool-rollback", "link_type": "optional"},
    )
    assert link_b.status_code == 201

    # 3. Retrieve the file with all associated links.
    get_resp = client.get(f"/api/v1/knowledge/{file_id}")
    assert get_resp.status_code == 200
    body = get_resp.json()
    links = body["links"]
    assert isinstance(links, list)
    assert len(links) == 2
    linked_tool_ids = {link["tool_id"] for link in links}
    assert linked_tool_ids == {"tool-deploy", "tool-rollback"}

    # 4. Unlink one tool — returns 204.
    unlink_resp = client.post(
        f"/api/v1/knowledge/{file_id}/unlink-tool",
        json={"tool_id": "tool-rollback"},
    )
    assert unlink_resp.status_code == 204

    # 5. The remaining link survives; the unlinked one is gone.
    after = client.get(f"/api/v1/knowledge/{file_id}").json()
    remaining_ids = {link["tool_id"] for link in after["links"]}
    assert remaining_ids == {"tool-deploy"}


def test_unlink_missing_link_returns_404(client):
    """Unlinking a tool that was never linked is a 404, not a silent 204."""
    file_id = client.post(
        "/api/v1/knowledge", json={"title": "Spec"}
    ).json()["file_id"]
    resp = client.post(
        f"/api/v1/knowledge/{file_id}/unlink-tool",
        json={"tool_id": "never-linked"},
    )
    assert resp.status_code == 404


def test_unlink_missing_file_returns_404(client):
    resp = client.post(
        "/api/v1/knowledge/does-not-exist/unlink-tool",
        json={"tool_id": "t-1"},
    )
    assert resp.status_code == 404
