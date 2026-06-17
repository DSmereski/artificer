"""Tests for the app store routes (/v1/appstore)."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gateway.app import create_app
from gateway.config import Config


# A minimal valid APK is just a ZIP — the route checks for the "PK" magic.
_FAKE_APK = b"PK\x03\x04" + b"\x00" * 512
# Minimal PNG magic for icon-upload tests.
_FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 128


@pytest.fixture
def appstore_config(tmp_config: Config, tmp_path: Path) -> Config:
    """tmp_config with the appstore paths redirected into the temp dir so
    tests never touch the real state dir."""
    store = tmp_path / "appstore"
    return dataclasses.replace(
        tmp_config,
        appstore_apk_dir=str(store / "apks"),
        appstore_catalog_path=str(store / "catalog.json"),
        appstore_public_base_url="http://127.0.0.1:8766",
    )


@pytest.fixture
def appstore_client(appstore_config: Config) -> TestClient:
    return TestClient(create_app(appstore_config))


@pytest.fixture
def device_token(appstore_client: TestClient) -> str:
    """Mint a device token directly so upload's bearer path can be exercised
    (TestClient's default host is non-loopback, so a token is required)."""
    token = "test-publisher-token"
    appstore_client.app.state.ai_team.devices.add(name="pytest-publisher", token=token)
    return token


def _upload(client: TestClient, app_id: str, token: str | None, **fields):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    form = {
        "name": fields.get("name", "Test App"),
        "packageId": fields.get("packageId", "com.example.test"),
        "version": fields.get("version", "1.0.0"),
        "description": fields.get("description", "a test app"),
        "category": fields.get("category", "Games"),
    }
    return client.post(
        f"/v1/appstore/{app_id}/upload",
        data=form,
        files={"apk": ("app-release.apk", _FAKE_APK, "application/vnd.android.package-archive")},
        headers=headers,
    )


def test_empty_catalog_returns_list(appstore_client: TestClient):
    r = appstore_client.get("/v1/appstore")
    assert r.status_code == 200
    assert r.json() == []


def test_upload_without_token_is_rejected(appstore_client: TestClient):
    r = _upload(appstore_client, "example-project", token=None)
    assert r.status_code == 401


def test_upload_with_token_then_appears_in_catalog(
    appstore_client: TestClient, device_token: str,
):
    r = _upload(appstore_client, "example-project", token=device_token, version="1.2.0")
    assert r.status_code == 200, r.text
    entry = r.json()
    assert entry["id"] == "example-project"
    assert entry["version"] == "1.2.0"
    # apkUrl must point at the tailnet base, not loopback, so the phone can fetch.
    assert entry["apkUrl"] == "http://127.0.0.1:8766/v1/appstore/example-project/apk"

    listing = appstore_client.get("/v1/appstore").json()
    assert [e["id"] for e in listing] == ["example-project"]

    one = appstore_client.get("/v1/appstore/example-project")
    assert one.status_code == 200
    assert one.json()["version"] == "1.2.0"


def test_apk_download_returns_bytes(
    appstore_client: TestClient, device_token: str,
):
    _upload(appstore_client, "example-project", token=device_token)
    r = appstore_client.get("/v1/appstore/example-project/apk")
    assert r.status_code == 200
    assert r.content == _FAKE_APK
    assert r.headers["content-type"] == "application/vnd.android.package-archive"


def test_non_apk_upload_rejected(appstore_client: TestClient, device_token: str):
    r = appstore_client.post(
        "/v1/appstore/example-project/upload",
        data={"name": "x", "packageId": "p", "version": "1.0.0"},
        files={"apk": ("evil.txt", b"not a zip", "text/plain")},
        headers={"Authorization": f"Bearer {device_token}"},
    )
    assert r.status_code == 400


def test_bad_app_id_is_404(appstore_client: TestClient):
    # Uppercase + underscore fail the slug regex.
    assert appstore_client.get("/v1/appstore/BAD_ID").status_code == 404
    assert appstore_client.get("/v1/appstore/BAD_ID/apk").status_code == 404


def test_missing_apk_on_disk_is_404(appstore_client: TestClient):
    assert appstore_client.get("/v1/appstore/nope/apk").status_code == 404


def test_icon_upload_sets_catalog_icon_and_serves(
    appstore_client: TestClient, device_token: str,
):
    _upload(appstore_client, "example-project", token=device_token)
    r = appstore_client.post(
        "/v1/appstore/example-project/icon",
        files={"icon": ("shot.png", _FAKE_PNG, "image/png")},
        headers={"Authorization": f"Bearer {device_token}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["icon"].endswith("/v1/appstore/example-project/icon")

    # Catalog entry now points at the icon URL.
    entry = appstore_client.get("/v1/appstore/example-project").json()
    assert entry["icon"].endswith("/v1/appstore/example-project/icon")

    # And the bytes are served.
    img = appstore_client.get("/v1/appstore/example-project/icon")
    assert img.status_code == 200
    assert img.content == _FAKE_PNG
    assert img.headers["content-type"] == "image/png"


def test_backdrop_upload_sets_screenshot_and_serves(
    appstore_client: TestClient, device_token: str,
):
    _upload(appstore_client, "example-project", token=device_token)
    r = appstore_client.post(
        "/v1/appstore/example-project/backdrop",
        files={"backdrop": ("shot.png", _FAKE_PNG, "image/png")},
        headers={"Authorization": f"Bearer {device_token}"},
    )
    assert r.status_code == 200, r.text
    entry = appstore_client.get("/v1/appstore/example-project").json()
    assert entry["screenshots"] == [r.json()["backdrop"]]
    img = appstore_client.get("/v1/appstore/example-project/backdrop")
    assert img.status_code == 200 and img.content == _FAKE_PNG


def test_icon_for_unknown_app_404(appstore_client: TestClient, device_token: str):
    r = appstore_client.post(
        "/v1/appstore/ghost/icon",
        files={"icon": ("shot.png", _FAKE_PNG, "image/png")},
        headers={"Authorization": f"Bearer {device_token}"},
    )
    assert r.status_code == 404


def test_icon_rejects_non_image(appstore_client: TestClient, device_token: str):
    _upload(appstore_client, "example-project", token=device_token)
    r = appstore_client.post(
        "/v1/appstore/example-project/icon",
        files={"icon": ("x.bin", b"not an image", "application/octet-stream")},
        headers={"Authorization": f"Bearer {device_token}"},
    )
    assert r.status_code == 400


def test_missing_icon_is_404(appstore_client: TestClient, device_token: str):
    _upload(appstore_client, "example-project", token=device_token)
    assert appstore_client.get("/v1/appstore/example-project/icon").status_code == 404


def test_republish_replaces_entry(
    appstore_client: TestClient, device_token: str,
):
    _upload(appstore_client, "example-project", token=device_token, version="1.0.0")
    _upload(appstore_client, "example-project", token=device_token, version="1.1.0")
    listing = appstore_client.get("/v1/appstore").json()
    assert len(listing) == 1
    assert listing[0]["version"] == "1.1.0"
