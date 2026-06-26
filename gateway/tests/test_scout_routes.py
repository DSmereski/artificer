"""Scout route tests.

`_snapshot` is a module-level function in gateway.routes.scout; we
monkeypatch it to avoid calling nvidia-smi / shutil on the host.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from gateway.routes import scout as scout_route


def _fake_snapshot() -> scout_route.ScoutStatus:
    return scout_route.ScoutStatus(
        gpus=[
            scout_route.GPUInfo(
                index=0, name="RTX test", temp_c=45,
                vram_used_mb=1024, vram_total_mb=16384,
                vram_used_pct=6.25, utilization_pct=12, game=None,
            )
        ],
        disks=[
            scout_route.DiskInfo(
                drive="C:\\", free_gb=500.0, total_gb=1000.0, used_pct=50.0,
            )
        ],
        bots=[
            scout_route.BotHeartbeat(
                name="Hive", is_running=True, pid=1234, uptime_seconds=60.0,
            )
        ],
    )


def test_scout_status_happy_path(
    client: TestClient, paired_token: tuple[str, str], monkeypatch
) -> None:
    monkeypatch.setattr(scout_route, "_snapshot", _fake_snapshot)
    _, token = paired_token
    r = client.get("/v1/scout/status", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["gpus"][0]["temp_c"] == 45
    assert data["disks"][0]["free_gb"] == 500.0
    assert data["bots"][0]["name"] == "Hive"


def test_scout_status_requires_auth(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(scout_route, "_snapshot", _fake_snapshot)
    r = client.get("/v1/scout/status")
    assert r.status_code == 401


def test_scout_history_appended(
    client: TestClient, paired_token: tuple[str, str], monkeypatch
) -> None:
    monkeypatch.setattr(scout_route, "_snapshot", _fake_snapshot)
    _, token = paired_token
    h = {"Authorization": f"Bearer {token}"}
    assert client.get("/v1/scout/status", headers=h).status_code == 200
    assert client.get("/v1/scout/status", headers=h).status_code == 200

    r = client.get("/v1/scout/history?limit=10", headers=h)
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) >= 2
    assert rows[0]["gpus"][0]["temp_c"] == 45
    assert "ts" in rows[0]
