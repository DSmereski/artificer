"""Smoke test: AppState gets Dispatcher + Scheduler on startup."""

from __future__ import annotations

from fastapi.testclient import TestClient

from gateway.worker_pool.dispatcher import Dispatcher
from gateway.worker_pool.scheduler import Scheduler


def test_app_state_has_dispatcher_and_scheduler(client: TestClient) -> None:
    st = client.app.state.ai_team
    assert isinstance(st.dispatcher, Dispatcher)
    assert isinstance(st.scheduler, Scheduler)
