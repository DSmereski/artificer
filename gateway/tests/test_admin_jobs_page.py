"""Smoke tests for /admin/jobs* — page + JS served."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_admin_jobs_page_loads(client: TestClient) -> None:
    r = client.get("/admin/jobs")
    assert r.status_code == 200
    assert "<table" in r.text
    assert "/admin/jobs.js" in r.text


def test_admin_jobs_js_served(client: TestClient) -> None:
    r = client.get("/admin/jobs.js")
    assert r.status_code == 200
    assert "fetch" in r.text


def test_admin_index_links_to_jobs(client: TestClient) -> None:
    r = client.get("/admin/")
    assert r.status_code == 200
    assert "/admin/jobs" in r.text
