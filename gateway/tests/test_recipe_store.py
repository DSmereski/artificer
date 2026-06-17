"""Tests for the saved-recipe vault store."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from gateway.recipe_store import Recipe, RecipeStore


_VALID_RECIPE = dedent("""\
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
    triggered_imports: ["civitai:620406", "civitai:241797"]
    positive: c0wg1rl, lighting
    negative: watermark, text
    ---

    body content here
""")


def _seed(vault: Path, image_id: int) -> None:
    refs = vault / "references"
    refs.mkdir(parents=True, exist_ok=True)
    body = _VALID_RECIPE.replace(
        "image_id: 128405012", f"image_id: {image_id}",
    ).replace(
        "/images/128405012", f"/images/{image_id}",
    )
    (refs / f"civitai-image-{image_id}.md").write_text(body, encoding="utf-8")


def test_list_returns_recipes(tmp_path):
    _seed(tmp_path, 100)
    _seed(tmp_path, 200)
    store = RecipeStore(tmp_path)
    recipes = store.list()
    ids = sorted(r.image_id for r in recipes)
    assert ids == [100, 200]


def test_list_empty_when_no_dir(tmp_path):
    store = RecipeStore(tmp_path)
    assert store.list() == []


def test_get_round_trips(tmp_path):
    _seed(tmp_path, 128405012)
    store = RecipeStore(tmp_path)
    r = store.get(128405012)
    assert r is not None
    assert r.image_id == 128405012
    assert r.source_url == "https://civitai.com/images/128405012"
    assert r.sampler == "Euler"
    assert r.steps == 7
    assert r.cfg == 5.0
    assert r.seed == 1234567
    assert r.positive.startswith("c0wg1rl")
    assert "watermark" in r.negative
    assert r.triggered_imports == ["civitai:620406", "civitai:241797"]


def test_get_missing_returns_none(tmp_path):
    store = RecipeStore(tmp_path)
    assert store.get(999) is None


def test_delete_unlinks(tmp_path):
    _seed(tmp_path, 42)
    store = RecipeStore(tmp_path)
    assert store.delete(42) is True
    assert store.get(42) is None
    # Idempotent — second delete returns False rather than raising.
    assert store.delete(42) is False


def test_corrupt_frontmatter_skipped(tmp_path):
    """A note with broken YAML frontmatter shouldn't crash list()."""
    refs = tmp_path / "references"
    refs.mkdir(parents=True, exist_ok=True)
    # Missing closing `---`
    (refs / "civitai-image-99.md").write_text(
        "---\nimage_id: 99\nbroken yaml: [\n",
        encoding="utf-8",
    )
    # Valid alongside.
    _seed(tmp_path, 100)
    store = RecipeStore(tmp_path)
    listed = store.list()
    assert any(r.image_id == 100 for r in listed)


def test_to_json_shape(tmp_path):
    _seed(tmp_path, 128405012)
    store = RecipeStore(tmp_path)
    j = store.get(128405012).to_json()
    for key in (
        "image_id", "source_url", "path",
        "sampler", "steps", "cfg", "seed",
        "positive", "negative", "triggered_imports",
    ):
        assert key in j
    assert isinstance(j["triggered_imports"], list)
