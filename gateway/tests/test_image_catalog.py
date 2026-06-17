"""Unit tests for gateway.image_catalog."""

from __future__ import annotations

from gateway.image_catalog import (
    ASPECT_RATIOS,
    ImageCatalog,
    LoraEntry,
    PresetEntry,
    compact_catalog_block,
    parse_image_payload,
    resolve_aspect,
    resolve_lora_aliases,
    resolve_preset,
)


# ---------------------------------------------------------------- parse_image_payload


def test_parse_empty():
    assert parse_image_payload("") is None
    assert parse_image_payload("   ") is None


def test_parse_plain_text():
    out = parse_image_payload("a cyberpunk city in neon rain")
    assert out == {"prompt": "a cyberpunk city in neon rain"}


def test_parse_json_full():
    raw = '{"prompt":"a dragon","aspect":"portrait","loras":["Real Beauty"],"negative":"blurry","count":2,"steps":30,"guidance":4.5,"enhance":false}'
    out = parse_image_payload(raw)
    assert out == {
        "prompt": "a dragon",
        "aspect": "portrait",
        "loras": ["Real Beauty"],
        "negative": "blurry",
        "count": 2,
        "steps": 30,
        "guidance": 4.5,
        "enhance": False,
    }


def test_parse_json_partial():
    out = parse_image_payload('{"prompt":"x","preset":"Quick Draft"}')
    assert out == {"prompt": "x", "preset": "Quick Draft"}


def test_parse_json_empty_loras_preserved():
    out = parse_image_payload('{"prompt":"x","loras":[]}')
    # Explicit [] means "opt out of auto-pick"; must survive the round-trip.
    assert out["loras"] == []


def test_parse_json_malformed_falls_back():
    raw = '{"prompt":"x", broken'
    out = parse_image_payload(raw)
    assert out == {"prompt": raw}


def test_parse_json_unknown_keys_ignored():
    out = parse_image_payload('{"prompt":"x","evil_field":{"nested":true}}')
    assert out == {"prompt": "x"}


def test_parse_json_array_not_dict_treated_as_plain():
    out = parse_image_payload('[1, 2, 3]')
    # Not a dict — falls back to plain text.
    assert out == {"prompt": "[1, 2, 3]"}


# ---------------------------------------------------------------- aspect resolver


def test_resolve_aspect_known():
    assert resolve_aspect("portrait") == (768, 1344)
    assert resolve_aspect("PORTRAIT") == (768, 1344)  # case-insensitive


def test_resolve_aspect_unknown():
    assert resolve_aspect("nonsense") is None
    assert resolve_aspect(None) is None
    assert resolve_aspect("") is None


def test_all_aspects_round_trip():
    for name, (w, h) in ASPECT_RATIOS.items():
        assert resolve_aspect(name) == (w, h)


# ---------------------------------------------------------------- preset resolver


def _sample_catalog() -> ImageCatalog:
    return ImageCatalog(
        loras=[
            LoraEntry("Real Beauty", "sdxl", "photorealistic portrait", 0.8, "people", False),
            LoraEntry("Lighting Slider", "sdxl", "", 1.0, "enhancement", False),
            LoraEntry("NSFW Thing", "sdxl", "explicit", 1.0, "nsfw", True),
        ],
        presets=[
            PresetEntry(
                name="Quick Draft", category="photography",
                width=512, height=512, steps=15, guidance=4.0,
                negative="blurry", loras=[], nsfw=False, tags=["quick"],
            ),
            PresetEntry(
                name="NSFW Preset", category="nsfw",
                width=1024, height=1024, steps=30, guidance=4.0,
                negative="", loras=[], nsfw=True, tags=[],
            ),
        ],
        loaded=True,
    )


def test_resolve_preset_hit():
    cat = _sample_catalog()
    p = resolve_preset(cat, "Quick Draft")
    assert p is not None and p.width == 512 and p.steps == 15


def test_resolve_preset_miss():
    cat = _sample_catalog()
    assert resolve_preset(cat, "Missing") is None
    assert resolve_preset(cat, None) is None


def test_resolve_preset_case_insensitive():
    cat = _sample_catalog()
    assert resolve_preset(cat, "quick draft") is not None


# ---------------------------------------------------------------- lora resolver


def test_resolve_lora_aliases_known_and_unknown():
    cat = _sample_catalog()
    out = resolve_lora_aliases(["Real Beauty", "Does Not Exist"], cat)
    assert len(out) == 1
    assert out[0]["strength"] == 0.8
    assert "Real Beauty" in out[0]["choice"]
    assert "trigger: photorealistic portrait" in out[0]["choice"]


def test_resolve_lora_aliases_no_trigger():
    cat = _sample_catalog()
    out = resolve_lora_aliases(["Lighting Slider"], cat)
    assert len(out) == 1
    assert "trigger:" not in out[0]["choice"]


def test_resolve_lora_aliases_case_insensitive():
    cat = _sample_catalog()
    out = resolve_lora_aliases(["real beauty"], cat)
    assert len(out) == 1


# ---------------------------------------------------------------- compact block


def test_compact_block_empty_catalog():
    assert compact_catalog_block(ImageCatalog()) == ""


def test_compact_block_excludes_nsfw_by_default():
    cat = _sample_catalog()
    block = compact_catalog_block(cat)
    assert "Real Beauty" in block
    assert "NSFW Thing" not in block
    assert "NSFW Preset" not in block


def test_compact_block_respects_max_chars():
    # Build a catalog with many LoRAs; ensure output stays under budget.
    loras = [
        LoraEntry(f"Alias-{i}", "sdxl", "trigger " * 30, 1.0, "test", False)
        for i in range(60)
    ]
    cat = ImageCatalog(loras=loras, presets=[], loaded=True)
    block = compact_catalog_block(cat, max_chars=1500)
    assert len(block) <= 1500


def test_compact_block_has_usage_hint():
    cat = _sample_catalog()
    block = compact_catalog_block(cat)
    assert "[GENERATE_IMAGE]" in block
    assert "Aspects:" in block


def test_compact_block_lists_presets_and_aspects():
    cat = _sample_catalog()
    block = compact_catalog_block(cat)
    assert "Quick Draft" in block
    assert "portrait" in block
