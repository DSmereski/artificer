"""EV2 — Evolve lane routes: /evolve/suggest + /evolve/go.

suggest returns + persists ranked candidates; go builds the top candidate through
the decompose pipeline on the EXISTING project, creates a goal + tickets, and
never flips push_allowed. Planner + analyzer are mocked (offline).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from gateway.crew_board.store import CrewBoardStore, Project
from gateway.crew_board.evolve import Candidate
from gateway.routes.board import _BOARD_TOKEN


def _install_crew_store(client: TestClient, tmp_path: Path) -> CrewBoardStore:
    store = CrewBoardStore(tmp_path / "evolve_routes.db")
    store.upsert_project(Project(
        slug="test-proj", path=str(tmp_path / "test-proj"), name="Test Project",
        enabled=True, push_allowed=False, test_cmd="python -m pytest -q"))
    client.app.state.crew_store = store
    return store


def _fake_plan_response(plan: dict) -> tuple[str, int, int]:
    return json.dumps(plan), 100, 100


_H = {"x-board-token": _BOARD_TOKEN}


def test_evolve_suggest_returns_and_persists(client: TestClient, tmp_path: Path) -> None:
    store = _install_crew_store(client, tmp_path)
    cands = [
        Candidate("Add AI engine", "build minimax", "core feature", ["repo-gap"], 0.9, ["x"]),
        Candidate("Polish UI", "tidy", "nice", ["product-idea"], 0.5, ["y"]),
    ]
    with patch("gateway.crew_board.evolve.analyze_next", AsyncMock(return_value=cands)):
        r = client.post("/board/projects/test-proj/evolve/suggest", headers=_H)
    assert r.status_code == 200, r.text
    data = r.json()
    assert [c["title"] for c in data["candidates"]] == ["Add AI engine", "Polish UI"]
    # Persisted for a later Go.
    raw = store.get_meta("evolve:test-proj")
    assert raw and "Add AI engine" in raw


def test_evolve_suggest_unknown_project_404(client: TestClient, tmp_path: Path) -> None:
    _install_crew_store(client, tmp_path)
    r = client.post("/board/projects/nope/evolve/suggest", headers=_H)
    assert r.status_code == 404


def test_evolve_go_builds_top_candidate_no_push(client: TestClient, tmp_path: Path) -> None:
    store = _install_crew_store(client, tmp_path)
    # Seed the cached Suggest result so Go uses it (no analyzer call).
    store.set_meta("evolve:test-proj", json.dumps({"candidates": [
        {"title": "Add feature Y", "body": "do it", "rationale": "r",
         "source": ["repo-gap"], "score": 0.9, "checklist": ["f.py exists"]},
    ]}))
    plan = {"project_name": "test-proj", "tickets": [
        {"title": "T1", "body": "b", "criteria": ["c1"], "files": ["f.py"], "depends_on": []},
    ]}
    with patch("gateway.helpers.base.OllamaInvoker",
               return_value=AsyncMock(chat=AsyncMock(return_value=_fake_plan_response(plan)))):
        r = client.post("/board/projects/test-proj/evolve/go", json={}, headers=_H)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["created"] == 1
    assert d["evolved_from"] == "Add feature Y"
    assert d["project_slug"] == "test-proj"
    assert d["scaffolded"] is False
    # Never pushes: existing project's push_allowed stays False.
    assert store.get_project("test-proj").push_allowed is False
    # Tickets landed on the project.
    tasks = store.list_tasks()
    assert tasks and all(t.project_slug == "test-proj" for t in tasks)


def test_evolve_go_refuses_active_project(client: TestClient, tmp_path: Path) -> None:
    store = _install_crew_store(client, tmp_path)
    store.create_task(title="busy", project_slug="test-proj", created_by="owner")
    r = client.post("/board/projects/test-proj/evolve/go", json={}, headers=_H)
    assert r.status_code == 409
