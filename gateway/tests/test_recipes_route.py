"""Tests for the /v1/recipes REST routes."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from gateway.recipe_store import RecipeStore


_RECIPE = dedent("""\
    ---
    type: image-recipe
    source_url: https://civitai.com/images/128405012
    image_id: 128405012
    audience: [terry, claude-code]
    tags: [image-recipe, civitai]
    sampler: Euler
    steps: 7
    cfg: 5.0
    seed: 1234567
    triggered_imports: ["civitai:620406"]
    positive: c0wg1rl, lighting
    negative: watermark, text
    ---

    body
""")


def _seed_recipe(vault: Path, image_id: int) -> None:
    refs = vault / "references"
    refs.mkdir(parents=True, exist_ok=True)
    (refs / f"civitai-image-{image_id}.md").write_text(_RECIPE.replace("128405012", str(image_id)).replace("128405012", str(image_id)), encoding="utf-8")


@pytest.fixture
def recipe_client(client, tmp_config):
    """Seed a recipe_store + a stub image_shim into the conftest client."""
    _seed_recipe(tmp_config.vault_path, 128405012)
    st = client.app.state.ai_team
    st.recipe_store = RecipeStore(tmp_config.vault_path)
    # Stub image_shim with a recording AsyncMock so /test calls don't
    # spawn real processes.
    fake_job = MagicMock()
    fake_job.id = "job-xyz"
    fake_job.state = "running"
    fake_job.prompt = "c0wg1rl, lighting"
    fake_job.result_ids = []
    fake_job.error = None
    fake_shim = MagicMock()
    fake_shim.enqueue = AsyncMock(return_value=fake_job)
    st.image_shim = fake_shim
    return client


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_list_recipes(recipe_client, paired_token):
    _, token = paired_token
    r = recipe_client.get("/v1/recipes", headers=_auth(token))
    assert r.status_code == 200
    body = r.json()
    assert "recipes" in body
    assert any(rec["image_id"] == 128405012 for rec in body["recipes"])


def test_get_recipe(recipe_client, paired_token):
    _, token = paired_token
    r = recipe_client.get("/v1/recipes/128405012", headers=_auth(token))
    assert r.status_code == 200
    body = r.json()
    assert body["image_id"] == 128405012
    assert body["sampler"] == "Euler"
    assert body["steps"] == 7
    assert body["cfg"] == 5.0
    assert body["positive"].startswith("c0wg1rl")


def test_get_recipe_404(recipe_client, paired_token):
    _, token = paired_token
    r = recipe_client.get("/v1/recipes/999", headers=_auth(token))
    assert r.status_code == 404


def test_test_recipe_enqueues_image(recipe_client, paired_token):
    _, token = paired_token
    r = recipe_client.post(
        "/v1/recipes/128405012/test", headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["job_id"] == "job-xyz"
    # Image shim should have been called with the saved settings.
    shim = recipe_client.app.state.ai_team.image_shim
    assert shim.enqueue.called
    kw = shim.enqueue.call_args.kwargs
    assert kw["prompt"].startswith("c0wg1rl")
    assert kw["negative_prompt"] == "watermark, text"
    assert kw["steps"] == 7
    assert kw["guidance"] == 5.0
    assert kw["seed"] == 1234567


def test_test_recipe_404(recipe_client, paired_token):
    _, token = paired_token
    r = recipe_client.post("/v1/recipes/999/test", headers=_auth(token))
    assert r.status_code == 404


def test_delete_recipe(recipe_client, paired_token, tmp_config):
    _, token = paired_token
    r = recipe_client.delete("/v1/recipes/128405012", headers=_auth(token))
    assert r.status_code == 200
    assert r.json() == {"deleted": 128405012}
    # Subsequent fetch returns 404.
    r2 = recipe_client.get("/v1/recipes/128405012", headers=_auth(token))
    assert r2.status_code == 404
    # And the file is gone.
    assert not (tmp_config.vault_path / "references" / "civitai-image-128405012.md").exists()


def test_delete_recipe_404(recipe_client, paired_token):
    _, token = paired_token
    r = recipe_client.delete("/v1/recipes/999", headers=_auth(token))
    assert r.status_code == 404


def test_recipe_routes_require_auth(recipe_client):
    r = recipe_client.get("/v1/recipes")
    assert r.status_code in (401, 403)
    r = recipe_client.post("/v1/recipes/1/test")
    assert r.status_code in (401, 403)


# ---------------------------------------------------------------- video


_VIDEO_RECIPE = dedent("""\
    ---
    type: image-recipe
    source_url: https://civitai.com/images/777
    image_id: 777
    audience: [terry, claude-code]
    tags: [image-recipe, civitai]
    sampler: Euler
    steps: 7
    cfg: 5.0
    seed: 999
    triggered_imports: ["civitai:2409202"]
    positive: dancing scene
    negative: 色调艳丽，过曝，静态
    recipe_kind: video
    ---

    body
""")


def _seed_video_recipe(vault: Path) -> None:
    refs = vault / "references"
    refs.mkdir(parents=True, exist_ok=True)
    (refs / "civitai-image-777.md").write_text(_VIDEO_RECIPE, encoding="utf-8")


def test_test_recipe_video_requires_seed(recipe_client, paired_token, tmp_config):
    """Video recipe with last_rendered mode but no last-rendered image
    must return 400 with a clear message."""
    _seed_video_recipe(tmp_config.vault_path)
    fake_video_shim = MagicMock()
    fake_video_shim.enqueue = AsyncMock()
    recipe_client.app.state.ai_team.video_shim = fake_video_shim
    _, token = paired_token
    r = recipe_client.post(
        "/v1/recipes/777/test",
        headers=_auth(token),
        json={"seed_mode": "last_rendered"},
    )
    assert r.status_code == 400
    assert "last-rendered" in r.json()["detail"]


def test_test_recipe_video_uploaded_mode(recipe_client, paired_token, tmp_config):
    """Video recipe with seed_mode=uploaded uses the supplied media_id."""
    _seed_video_recipe(tmp_config.vault_path)
    _, token = paired_token

    # Seed a fake "uploaded reference" file so _resolve_uploaded_reference finds it.
    uploads = tmp_config.state_dir / "media-uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    (uploads / "abc123.jpg").write_bytes(b"\xff\xd8\xff\xd9")    # tiny stub

    # Stub video_shim onto the app state.
    fake_video_job = MagicMock()
    fake_video_job.id = "vid-1"
    fake_video_job.state = "running"
    fake_video_job.prompt = "dancing scene"
    fake_video_job.result_id = None
    fake_video_job.duration_s = 0.0
    fake_video_job.error = None
    fake_video_shim = MagicMock()
    fake_video_shim.enqueue = AsyncMock(return_value=fake_video_job)
    recipe_client.app.state.ai_team.video_shim = fake_video_shim

    r = recipe_client.post(
        "/v1/recipes/777/test",
        headers=_auth(token),
        json={"seed_mode": "uploaded", "seed_media_id": "abc123"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "video"
    assert body["job_id"] == "vid-1"
    # Video shim called with the resolved seed path.
    assert fake_video_shim.enqueue.called
    kw = fake_video_shim.enqueue.call_args.kwargs
    assert kw["prompt"] == "dancing scene"
    assert kw["seed_image_path"].endswith("abc123.jpg")
    assert kw["seed"] == 999


def test_test_recipe_video_unknown_seed_mode(recipe_client, paired_token, tmp_config):
    _seed_video_recipe(tmp_config.vault_path)
    _, token = paired_token
    fake_shim = MagicMock()
    fake_shim.enqueue = AsyncMock()
    recipe_client.app.state.ai_team.video_shim = fake_shim
    r = recipe_client.post(
        "/v1/recipes/777/test",
        headers=_auth(token),
        json={"seed_mode": "wat"},
    )
    assert r.status_code == 400
    assert "seed_mode" in r.json()["detail"]


def test_test_recipe_still_path_unchanged(recipe_client, paired_token):
    """Make sure the still recipe's response shape includes kind=still."""
    _, token = paired_token
    r = recipe_client.post(
        "/v1/recipes/128405012/test", headers=_auth(token),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "still"
    assert body["job_id"] == "job-xyz"
