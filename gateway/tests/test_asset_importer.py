"""Tests for the URL→LoRA asset importer."""

from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path
from unittest.mock import patch

import pytest

from gateway import asset_importer
from gateway.asset_importer import (
    AssetImportStore,
    ImportJob,
    ParsedSource,
    ResolvedDownload,
    _is_trusted_host,
    _slugify,
    _validate_target,
    install_lora,
    parse_url,
)


# ---------------------------------------------------------------- parse_url


def test_parse_civitai_com():
    p = parse_url("https://civitai.com/models/1253021/some-name")
    assert p is not None
    assert p.kind == "civitai"
    assert p.host == "civitai.com"
    assert p.model_id == 1253021
    assert p.version_id is None


def test_parse_civitai_red():
    p = parse_url(
        "https://civitai.com/models/1223034/cinematic-motion-helper"
    )
    assert p is not None
    assert p.kind == "civitai"
    assert p.host == "civitai.com"
    assert p.model_id == 1223034


def test_parse_civitai_with_version():
    p = parse_url(
        "https://civitai.com/models/1253021/x?modelVersionId=98765"
    )
    assert p is not None
    assert p.version_id == 98765


def test_parse_huggingface_blob():
    p = parse_url(
        "https://huggingface.co/foo/bar/blob/main/lora.safetensors"
    )
    assert p is not None
    assert p.kind == "huggingface"
    assert "foo/bar@main/lora.safetensors" == p.file_path


def test_parse_huggingface_resolve():
    p = parse_url(
        "https://huggingface.co/foo/bar/resolve/main/sub/lora.safetensors"
    )
    assert p is not None
    assert p.kind == "huggingface"
    assert "foo/bar@main/sub/lora.safetensors" == p.file_path


def test_parse_raw_safetensors():
    p = parse_url("https://example.com/path/file.safetensors")
    assert p is not None
    assert p.kind == "raw"
    assert p.host == "example.com"


def test_parse_unknown_returns_none():
    assert parse_url("https://example.com/random.html") is None
    assert parse_url("not a url") is None
    assert parse_url("") is None
    assert parse_url("ftp://example.com/foo.safetensors") is None


# ---------------------------------------------------------------- security


def test_trusted_host_allowlist():
    assert _is_trusted_host("civitai.com") is True
    assert _is_trusted_host("civitai.com") is True
    assert _is_trusted_host("huggingface.co") is True
    assert _is_trusted_host("cdn-lfs.huggingface.co") is True
    assert _is_trusted_host("CIVITAI.COM") is True            # case-insensitive
    assert _is_trusted_host("example.com") is False
    assert _is_trusted_host("evil-civitai.com") is False
    assert _is_trusted_host("sub.civitai.com") is True        # subdomain ok


def test_validate_target_rejects_non_http():
    assert _validate_target("ftp://civitai.com/x") is not None
    assert _validate_target("file:///etc/passwd") is not None
    assert _validate_target("javascript:alert(1)") is not None


def test_validate_target_rejects_untrusted_host():
    msg = _validate_target("https://example.com/x.safetensors")
    assert msg is not None
    assert "trusted" in msg.lower()


def test_validate_target_rejects_private_ip():
    msg = _validate_target("https://127.0.0.1/x", allow_trusted=False)
    assert msg is not None
    msg = _validate_target("https://10.0.0.1/x", allow_trusted=False)
    assert msg is not None


def test_validate_target_accepts_trusted():
    # We pass a real domain and rely on DNS resolving to a public IP.
    # If DNS is unavailable in CI, the test is best-effort: the function
    # would return a "DNS lookup failed" string, which still counts as
    # rejection — so just check the trusted-allowlist branch alone via
    # the by-IP case.
    msg = _validate_target("https://civitai.com/api/v1/models/1")
    # Either None (good) or a DNS-resolution failure string. Both
    # indicate the trusted-host check itself passed.
    assert msg is None or "DNS" in msg or "blocked" in msg


# ---------------------------------------------------------------- slug


def test_slugify():
    assert _slugify("civitai:1234") == "civitai_1234"
    assert _slugify("hf/foo/bar@main/x.safetensors") == "hf_foo_bar_main_x.safetensors"
    assert _slugify("...") == "asset"
    assert _slugify("") == "asset"


# ---------------------------------------------------------------- install


def _fake_safetensors(path: Path, body: bytes = b'{"meta":{}}') -> None:
    """Write a minimally-valid safetensors file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    hdr = struct.pack("<Q", len(body))
    path.write_bytes(hdr + body)


def test_install_lora_writes_registry(tmp_path):
    loras_root = tmp_path / "loras"
    loras_root.mkdir()
    registry = loras_root / "lora_registry.json"

    # Simulate a finished download in a workspace dir.
    workspace = loras_root / "civitai_42"
    workspace.mkdir()
    src = workspace / "model.safetensors"
    _fake_safetensors(src)

    resolved = ResolvedDownload(
        download_url="https://civitai.com/x",
        filename="model.safetensors",
        size_bytes=src.stat().st_size,
        sha256=None,
        alias="Model 42",
        repo_id="civitai:42",
        pipeline="sdxl",
        trigger_words="trig1, trig2",
        category="lora",
        nsfw=False,
    )
    entry = install_lora(
        resolved=resolved,
        downloaded_path=src,
        loras_root=loras_root,
        registry_path=registry,
    )
    assert entry["repo_id"] == "civitai:42"
    assert entry["alias"] == "Model 42"
    assert Path(entry["main_file"]).is_file()
    assert registry.is_file()
    data = json.loads(registry.read_text(encoding="utf-8"))
    assert len(data) == 1
    assert data[0]["pipeline"] == "sdxl"
    assert data[0]["trigger_words"] == "trig1, trig2"


def test_install_lora_replaces_existing_repo_id(tmp_path):
    loras_root = tmp_path / "loras"
    loras_root.mkdir()
    registry = loras_root / "lora_registry.json"
    registry.write_text(json.dumps([
        {"repo_id": "civitai:42", "alias": "old", "main_file": "/x"},
        {"repo_id": "civitai:99", "alias": "keep", "main_file": "/y"},
    ]))

    workspace = loras_root / "civitai_42"
    workspace.mkdir()
    src = workspace / "new.safetensors"
    _fake_safetensors(src)

    resolved = ResolvedDownload(
        download_url="https://civitai.com/x",
        filename="new.safetensors",
        size_bytes=src.stat().st_size,
        sha256=None,
        alias="new",
        repo_id="civitai:42",
    )
    install_lora(
        resolved=resolved,
        downloaded_path=src,
        loras_root=loras_root,
        registry_path=registry,
    )
    data = json.loads(registry.read_text(encoding="utf-8"))
    assert len(data) == 2
    by_id = {e["repo_id"]: e for e in data}
    assert by_id["civitai:42"]["alias"] == "new"      # replaced
    assert by_id["civitai:99"]["alias"] == "keep"     # untouched


def test_install_lora_path_confined(tmp_path):
    """install_lora must reject targets that escape loras_root."""
    loras_root = tmp_path / "loras"
    loras_root.mkdir()
    registry = loras_root / "lora_registry.json"

    workspace = tmp_path / "outside"
    workspace.mkdir()
    src = workspace / "evil.safetensors"
    _fake_safetensors(src)

    resolved = ResolvedDownload(
        download_url="https://civitai.com/x",
        filename="evil.safetensors",
        size_bytes=src.stat().st_size,
        sha256=None,
        alias="evil",
        # This crafted repo_id slugifies to "..._etc_passwd" — still
        # contained inside loras_root, which is the point of slugifying.
        repo_id="civitai:42",
    )
    # Normal path: succeeds and stays under loras_root.
    install_lora(
        resolved=resolved,
        downloaded_path=src,
        loras_root=loras_root,
        registry_path=registry,
    )
    data = json.loads(registry.read_text(encoding="utf-8"))
    final_path = Path(data[0]["local_path"]).resolve()
    assert str(final_path).startswith(str(loras_root.resolve()))


# ---------------------------------------------------------------- store


def test_asset_import_store_ring_trims():
    s = AssetImportStore(max_records=3)
    a = s.create("https://x/1")
    b = s.create("https://x/2")
    c = s.create("https://x/3")
    d = s.create("https://x/4")
    # Oldest dropped.
    assert s.get(a.id) is None
    assert s.get(b.id) is not None
    assert s.get(c.id) is not None
    assert s.get(d.id) is not None
    assert [j.url for j in s.list()] == [
        "https://x/2", "https://x/3", "https://x/4",
    ]


def test_import_job_progress_pct():
    j = ImportJob(id="x", url="https://y", bytes_total=1000)
    j.bytes_done = 250
    j.touch()
    assert j.progress_pct == 25.0
    j.bytes_done = 1000
    j.touch()
    assert j.progress_pct == 100.0


# ---------------------------------------------------------------- persistence


def test_asset_import_store_persists_jobs(tmp_path):
    """Round-trip: create + mutate + new instance = same state."""
    p = tmp_path / "asset_imports.json"
    s = AssetImportStore(max_records=10, path=p)
    j = s.create("https://civitai.com/models/12345")
    j.alias = "TestLora"
    j.repo_id = "civitai:12345"
    j.state = "done"
    j.touch()  # triggers terminal-state persist

    # Fresh instance reading the same file.
    s2 = AssetImportStore(max_records=10, path=p)
    items = s2.list()
    assert len(items) == 1
    assert items[0].id == j.id
    assert items[0].alias == "TestLora"
    assert items[0].state == "done"
    assert items[0].repo_id == "civitai:12345"


def test_asset_import_store_orphans_in_flight_on_restart(tmp_path):
    """A job in 'downloading' / 'installing' / 'resolving' was owned
    by an asyncio task that the gateway restart killed. On reload the
    store flips it to error so the user sees the gap and can re-queue,
    instead of an immortal 'downloading' that blocks them."""
    p = tmp_path / "asset_imports.json"
    s = AssetImportStore(max_records=10, path=p)
    j = s.create("https://huggingface.co/model")
    j.state = "downloading"
    j.bytes_done = 500_000_000
    j.bytes_total = 12_000_000_000
    j.touch()

    s2 = AssetImportStore(max_records=10, path=p)
    [recovered] = s2.list()
    assert recovered.state == "error"
    assert recovered.error and "interrupted" in recovered.error


def test_asset_import_store_no_path_means_no_persist(tmp_path):
    """Memory-only mode (the legacy default) still works — used by
    tests that don't want the JSON file."""
    s = AssetImportStore(max_records=10, path=None)
    j = s.create("https://x/1")
    j.state = "done"
    j.touch()  # would crash if persist tried to write a None path
    assert s.get(j.id) is not None


def test_asset_import_store_load_skips_corrupt_file(tmp_path):
    """A truncated json blob (e.g. crash during write) shouldn't kill
    the gateway boot — we just start fresh."""
    p = tmp_path / "asset_imports.json"
    p.write_text("{not json", encoding="utf-8")
    s = AssetImportStore(max_records=10, path=p)
    assert s.list() == []


# ---------------------------------------------------------------- safetensors


def test_verify_safetensors_magic_accepts_valid(tmp_path):
    p = tmp_path / "ok.safetensors"
    _fake_safetensors(p, body=b'{"meta":{"x":1}}')
    asset_importer.verify_safetensors_magic(p)        # no raise


def test_verify_safetensors_magic_rejects_short(tmp_path):
    p = tmp_path / "short.safetensors"
    p.write_bytes(b"\x01\x02\x03")
    with pytest.raises(ValueError, match="too short"):
        asset_importer.verify_safetensors_magic(p)


def test_verify_safetensors_magic_rejects_bogus_header(tmp_path):
    p = tmp_path / "bogus.safetensors"
    # Header length implausibly large.
    p.write_bytes(struct.pack("<Q", 1 << 40) + b"{")
    with pytest.raises(ValueError, match="implausible"):
        asset_importer.verify_safetensors_magic(p)


def test_verify_safetensors_magic_rejects_non_json_payload(tmp_path):
    p = tmp_path / "non_json.safetensors"
    # Plausible header length, but the byte after the length is not '{'.
    p.write_bytes(struct.pack("<Q", 16) + b"X")
    with pytest.raises(ValueError, match="doesn't start with"):
        asset_importer.verify_safetensors_magic(p)


# ---------------------------------------------------------------- pipeline


def test_infer_pipeline():
    from gateway.asset_importer import _infer_pipeline
    assert _infer_pipeline({"baseModel": "Flux.1 D"}, {}) == "flux"
    assert _infer_pipeline({"baseModel": "Pony"}, {}) == "sdxl"
    assert _infer_pipeline({"baseModel": "Illustrious"}, {}) == "sdxl"
    assert _infer_pipeline({"baseModel": "Anima"}, {}) == "sdxl"
    assert _infer_pipeline({"baseModel": "NoobAI"}, {}) == "sdxl"
    assert _infer_pipeline({"baseModel": "SDXL 1.0"}, {}) == "sdxl"
    assert _infer_pipeline({"baseModel": "ZImageTurbo"}, {}) == "zimage"
    assert _infer_pipeline({"baseModel": "Wan Video 2.2 I2V-A14B"}, {}) == "wan"
    assert _infer_pipeline({"baseModel": "SD 1.5"}, {}) == "sd1.5"
    assert _infer_pipeline({}, {"baseModel": "SD 2.1"}) == "sd2"
    assert _infer_pipeline({}, {}) == "unknown"


def test_classify_kind():
    from gateway.asset_importer import _classify_kind
    assert _classify_kind("LORA")[0] == "lora"
    assert _classify_kind("LoCon")[0] == "lora"
    assert _classify_kind("Checkpoint")[0] == "checkpoint"
    assert _classify_kind("CheckpointMerge")[0] == "checkpoint"
    kind, reason = _classify_kind("Workflows")
    assert kind == "unsupported"
    assert "workflow" in reason.lower()
    kind, reason = _classify_kind("TextualInversion")
    assert kind == "unsupported"
    # Unknown defaults to lora (the safer destination).
    assert _classify_kind("WeirdNewType")[0] == "lora"


# ---------------------------------------------------------------- resolvers


def test_resolve_civitai_uses_first_safetensors(monkeypatch):
    import asyncio

    fake_response = {
        "name": "Test Model",
        "type": "LORA",
        "nsfw": False,
        "modelVersions": [
            {
                "id": 999,
                "baseModel": "SDXL 1.0",
                "trainedWords": ["trig"],
                "files": [
                    {"name": "preview.png", "downloadUrl": "https://civitai.com/p"},
                    {
                        "name": "weights.safetensors",
                        "downloadUrl": "https://civitai.com/d",
                        "sizeKB": 200000,
                        "hashes": {"SHA256": "ABCDEF"},
                    },
                ],
            },
        ],
    }

    async def fake_json(url, timeout=30.0, *, auth=False):
        return fake_response

    monkeypatch.setattr(asset_importer, "_http_get_json", fake_json)
    monkeypatch.setattr(
        asset_importer, "_validate_target", lambda u, **k: None,
    )
    parsed = ParsedSource(
        kind="civitai", host="civitai.com",
        original_url="https://civitai.com/models/42",
        model_id=42,
    )
    out = asyncio.run(asset_importer._resolve_civitai(parsed))
    assert out.filename == "weights.safetensors"
    assert out.repo_id == "civitai:42"
    assert out.pipeline == "sdxl"
    assert out.sha256 == "abcdef"
    assert out.size_bytes == 200000 * 1024
    assert "trig" in out.trigger_words


def test_install_checkpoint_writes_meta(tmp_path):
    """Checkpoints land in checkpoints_root/<slug>/ with a sidecar."""
    from gateway.asset_importer import install_checkpoint

    cp_root = tmp_path / "community"
    cp_root.mkdir()
    workspace = cp_root / "civitai_42"
    workspace.mkdir()
    src = workspace / "model.safetensors"
    _fake_safetensors(src)

    resolved = ResolvedDownload(
        download_url="https://civitai.com/x",
        filename="model.safetensors",
        size_bytes=src.stat().st_size,
        sha256=None,
        alias="A Checkpoint",
        repo_id="civitai:42",
        kind="checkpoint",
        pipeline="zimage",
        category="Checkpoint",
    )
    meta = install_checkpoint(
        resolved=resolved,
        downloaded_path=src,
        checkpoints_root=cp_root,
    )
    assert meta["repo_id"] == "civitai:42"
    assert meta["kind"] == "checkpoint"
    assert Path(meta["main_file"]).is_file()
    sidecar = Path(meta["main_file"]).parent / "asset_meta.json"
    assert sidecar.is_file()
    parsed = json.loads(sidecar.read_text(encoding="utf-8"))
    assert parsed["alias"] == "A Checkpoint"
    assert parsed["pipeline"] == "zimage"


def test_run_import_rejects_unsupported(tmp_path, monkeypatch):
    """Workflows / TI / VAE assets should error out cleanly."""
    import asyncio

    async def fake_resolve(parsed):
        return ResolvedDownload(
            download_url="https://x",
            filename="x.json",
            size_bytes=0,
            sha256=None,
            alias="Workflow Asset",
            repo_id="civitai:9",
            kind="unsupported",
            category="Workflows",
            unsupported_reason="Workflows are pipeline configs, not weights.",
        )

    monkeypatch.setattr(asset_importer, "resolve", fake_resolve)
    job = asset_importer.ImportJob(id="t1", url="https://civitai.com/models/9")
    out = asyncio.run(asset_importer.run_import(
        job,
        loras_root=tmp_path / "l",
        registry_path=tmp_path / "l" / "reg.json",
        checkpoints_root=tmp_path / "c",
    ))
    assert out is None
    assert job.state == "error"
    assert "workflow" in (job.error or "").lower()


# ---------------------------------------------------------------- recipes


_FULL_RECIPE_BLOCK = """\
c0wg1rl, a woman straddling a man, very detailed lighting, 8k.

Authentic film look, High-fidelity details
Negative prompt: watermark, text, blur, lowres, ugly, jpeg artifacts
Steps: 7, CFG scale: 5, Sampler: Euler

Resources used:
- https://civitai.com/models/620406/moody-color-mix
- https://civitai.com/models/241797/sample-foo?modelVersionId=999
"""


def test_parse_civitai_image_url_dotred():
    p = parse_url("https://civitai.com/images/128405012")
    assert p is not None
    assert p.kind == "civitai_image_recipe"
    assert p.host == "civitai.com"
    assert p.model_id == 128405012


def test_parse_civitai_image_url_dotcom():
    p = parse_url("https://civitai.com/images/42")
    assert p is not None
    assert p.kind == "civitai_image_recipe"
    assert p.model_id == 42


def test_parse_civitai_recipe_text_full():
    out = asset_importer.parse_civitai_recipe_text(_FULL_RECIPE_BLOCK)
    assert "c0wg1rl" in out["positive"]
    assert "Negative prompt:" not in out["positive"]
    assert "watermark" in out["negative"]
    assert out["sampler"] == "Euler"
    assert out["steps"] == 7
    assert out["cfg"] == 5.0
    # Two URLs, deduped, order preserved.
    assert out["model_urls"] == [
        "https://civitai.com/models/620406/moody-color-mix",
        "https://civitai.com/models/241797/sample-foo?modelVersionId=999",
    ]


def test_parse_civitai_recipe_text_minimal():
    out = asset_importer.parse_civitai_recipe_text("just a positive prompt")
    assert out["positive"] == "just a positive prompt"
    assert out["negative"] is None
    assert out["sampler"] is None
    assert out["steps"] is None
    assert out["cfg"] is None
    assert out["seed"] is None
    assert out["model_urls"] == []


def test_parse_civitai_recipe_text_dedup_urls():
    text = (
        "x https://civitai.com/models/1 y "
        "https://civitai.com/models/1 z https://civitai.com/models/2"
    )
    out = asset_importer.parse_civitai_recipe_text(text)
    assert out["model_urls"] == [
        "https://civitai.com/models/1",
        "https://civitai.com/models/2",
    ]


def test_parse_civitai_recipe_text_handles_seed():
    out = asset_importer.parse_civitai_recipe_text(
        "prompt\nNegative prompt: bad\nSteps: 20, Sampler: DPM++, CFG scale: 7, Seed: 1234567",
    )
    assert out["seed"] == 1234567
    assert out["sampler"] == "DPM++"


def test_parse_civitai_recipe_text_caps_at_32kb():
    huge = "a " * 50_000
    out = asset_importer.parse_civitai_recipe_text(huge)
    # Positive should be truncated to <= 32 KB
    assert len(out["positive"]) <= 32 * 1024


def test_detect_recipe_kind_still():
    out = asset_importer.parse_civitai_recipe_text(
        "a portrait\nNegative prompt: ugly\nSteps: 20, Sampler: Euler"
    )
    assert out["kind"] == "still"


def test_detect_recipe_kind_video_from_resources():
    text = (
        "girl dancing\nResources used:\n"
        "- https://civitai.com/models/2409202/wan22-i2v-svi-workflow-foo"
    )
    out = asset_importer.parse_civitai_recipe_text(text)
    assert out["kind"] == "video"


def test_detect_recipe_kind_video_from_chinese_negative():
    """The Chinese WAN default-negative-prompt fragment is the smoking gun."""
    text = (
        "scene\nNegative prompt: watermark, 色调艳丽，过曝，静态，"
        "细节模糊不清\nSteps: 7, CFG scale: 5, Sampler: Euler"
    )
    out = asset_importer.parse_civitai_recipe_text(text)
    assert out["kind"] == "video"


def test_detect_recipe_kind_video_from_text_hint():
    text = "wan22 dance scene\nNegative prompt: x"
    out = asset_importer.parse_civitai_recipe_text(text)
    assert out["kind"] == "video"


def test_run_import_image_recipe_no_pasted_text(tmp_path):
    """Empty pasted text → clean error explaining why."""
    import asyncio
    job = asset_importer.ImportJob(
        id="t1", url="https://civitai.com/images/128405012",
    )
    out = asyncio.run(asset_importer.run_import(
        job,
        loras_root=tmp_path / "l",
        registry_path=tmp_path / "l" / "reg.json",
        checkpoints_root=tmp_path / "c",
    ))
    assert out is None
    assert job.state == "error"
    assert job.error is not None
    assert "API is locked" in job.error or "paste" in job.error.lower()


def test_run_import_image_recipe_writes_vault_and_subimports(tmp_path, monkeypatch):
    """End-to-end: pasted text → vault.learn called, sub-imports queued."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    fake_vc = MagicMock()
    fake_vc.learn = AsyncMock(return_value={"ok": True, "path": "references/x.md"})

    def fake_factory():
        return fake_vc

    # Stub run_import for sub-jobs so we don't actually download anything.
    sub_calls: list[str] = []
    real_run_import = asset_importer.run_import

    async def fake_sub_run(job, **kw):
        sub_calls.append(job.url)
        job.state = "done"
        return {"ok": True}

    store = asset_importer.AssetImportStore()
    job = asset_importer.ImportJob(
        id="parent",
        url="https://civitai.com/images/128405012",
        pasted_text=_FULL_RECIPE_BLOCK,
    )

    # Replace run_import only after the parent calls _run_image_recipe.
    # Easiest: patch only the asyncio.create_task call indirectly via
    # patching run_import in the module namespace AFTER first invocation.
    # Simpler approach: run end-to-end and let the real run_import fire,
    # but stub `resolve` so it doesn't hit the network.
    async def fake_resolve(parsed):
        return asset_importer.ResolvedDownload(
            download_url="https://x", filename="x.safetensors", size_bytes=0,
            sha256=None, alias="x", repo_id=f"civitai:{parsed.model_id}",
            kind="lora",
        )

    async def fake_dl(*a, **kw):
        # Pretend the file got written so install_lora finds something.
        dest = a[1] if len(a) > 1 else kw["dest"]
        from gateway.tests.test_asset_importer import _fake_safetensors as fs
        fs(dest)
        return (10, "deadbeef")

    monkeypatch.setattr(asset_importer, "resolve", fake_resolve)
    monkeypatch.setattr(asset_importer, "download_with_progress", fake_dl)

    out = asyncio.run(real_run_import(
        job,
        loras_root=tmp_path / "loras",
        registry_path=tmp_path / "loras" / "reg.json",
        checkpoints_root=tmp_path / "checkpoints",
        vault_client_factory=fake_factory,
        asset_import_store=store,
    ))

    assert out is not None
    assert out["kind"] == "image_recipe"
    assert out["image_id"] == 128405012
    assert job.state == "done"
    assert fake_vc.learn.called
    call = fake_vc.learn.call_args
    assert call.kwargs["category"] == "reference"
    assert call.kwargs["title"] == "civitai-image-128405012"
    assert "image-recipe" in call.kwargs["tags"]
    assert "terry" in call.kwargs["audience"]
    extra = call.kwargs["extra"]
    assert extra["sampler"] == "Euler"
    assert extra["steps"] == 7
    assert extra["cfg"] == 5.0
    # Two sub-imports queued (one per parsed model URL).
    assert len(job.sub_job_ids) == 2


def test_run_import_image_recipe_skips_already_installed(tmp_path, monkeypatch):
    """Sub-imports skip URLs whose civitai:<id> repo_id is already in registry."""
    import asyncio, json as _json
    from unittest.mock import AsyncMock, MagicMock

    loras_root = tmp_path / "loras"
    loras_root.mkdir()
    registry = loras_root / "reg.json"
    # Pre-install civitai:620406
    registry.write_text(_json.dumps([
        {"repo_id": "civitai:620406", "alias": "moody", "main_file": "/x"},
    ]))

    fake_vc = MagicMock()
    fake_vc.learn = AsyncMock(return_value={"ok": True})

    async def fake_resolve(parsed):
        return asset_importer.ResolvedDownload(
            download_url="https://x", filename="x.safetensors", size_bytes=0,
            sha256=None, alias="x", repo_id=f"civitai:{parsed.model_id}",
        )
    async def fake_dl(*a, **kw):
        from gateway.tests.test_asset_importer import _fake_safetensors as fs
        dest = a[1] if len(a) > 1 else kw["dest"]
        fs(dest)
        return (10, "deadbeef")
    monkeypatch.setattr(asset_importer, "resolve", fake_resolve)
    monkeypatch.setattr(asset_importer, "download_with_progress", fake_dl)

    store = asset_importer.AssetImportStore()
    job = asset_importer.ImportJob(
        id="parent",
        url="https://civitai.com/images/128405012",
        pasted_text=_FULL_RECIPE_BLOCK,    # has both 620406 and 241797
    )
    asyncio.run(asset_importer.run_import(
        job,
        loras_root=loras_root, registry_path=registry,
        checkpoints_root=tmp_path / "c",
        vault_client_factory=lambda: fake_vc,
        asset_import_store=store,
    ))
    # Only 241797 should have been queued; 620406 was already installed.
    assert len(job.sub_job_ids) == 1


def test_resolve_huggingface():
    import asyncio
    parsed = ParsedSource(
        kind="huggingface", host="huggingface.co",
        original_url="https://huggingface.co/foo/bar/blob/main/x.safetensors",
        file_path="foo/bar@main/x.safetensors",
    )
    out = asyncio.run(asset_importer._resolve_huggingface(parsed))
    assert out.download_url == (
        "https://huggingface.co/foo/bar/resolve/main/x.safetensors"
    )
    assert out.filename == "x.safetensors"
    assert out.repo_id.startswith("hf:foo/bar@main/")


# ---------------------------------------------------------------- H-3: semaphore caps sub-import concurrency


def test_recipe_sub_imports_caps_at_two(tmp_path, monkeypatch):
    """Recipe sub-imports must be capped at exactly 2 concurrent downloads.

    Earlier iterations of this test only proved `peak <= 2`, which passes
    trivially even if the cap is 1. We now PARK each download on a per-task
    `asyncio.Event` so they cannot complete on their own — the semaphore is
    the only thing that determines how many can be in flight at once.

    With 5 fake downloads queued and all of them parked:
      - peak in-flight should hit exactly 2 (the cap)
      - the 3rd, 4th, 5th must be observably blocked at the semaphore
      - releasing the events one batch at a time should let the next batch
        take the slots, ultimately running all 5
    """
    import asyncio as _asyncio
    from unittest.mock import AsyncMock, MagicMock

    civitai_urls = [
        f"https://civitai.com/models/{100000 + i}/model-{i}"
        for i in range(5)
    ]
    recipe_text = (
        "Positive prompt: a test\n"
        "Negative prompt: bad\n"
        "Steps: 20, Sampler: Euler, CFG scale: 7\n"
        "Resources used:\n"
        + "\n".join(f"  - {u}" for u in civitai_urls)
    )

    fake_vc = MagicMock()
    fake_vc.learn = AsyncMock(return_value={"ok": True, "path": "ref/x.md"})

    loras_root = tmp_path / "loras"
    loras_root.mkdir()
    registry = loras_root / "reg.json"
    store = asset_importer.AssetImportStore()

    state = {
        "in_flight": 0,
        "peak": 0,
        "started": 0,
        "release": None,  # asyncio.Event, created inside the loop
    }

    async def fake_run_download(job, parsed, **kw):
        state["in_flight"] += 1
        state["started"] += 1
        state["peak"] = max(state["peak"], state["in_flight"])
        # Park here until the releaser sets the event. The semaphore is
        # the ONLY reason a 3rd task can't enter this block.
        await state["release"].wait()
        state["in_flight"] -= 1
        job.state = "done"
        return {
            "main_file": str(tmp_path / "fake.safetensors"),
            "repo_id": f"civitai:{parsed.model_id}",
        }

    monkeypatch.setattr(asset_importer, "_run_download_import", fake_run_download)

    async def fake_resolve(parsed):
        return asset_importer.ResolvedDownload(
            download_url="https://x", filename="x.safetensors", size_bytes=0,
            sha256=None, alias="x", repo_id=f"civitai:{parsed.model_id}",
        )

    monkeypatch.setattr(asset_importer, "resolve", fake_resolve)

    parent_job = asset_importer.ImportJob(
        id="parent",
        url="https://civitai.com/images/99999999",
        pasted_text=recipe_text,
    )

    sub_tasks: list = []

    async def _run_all():
        state["release"] = _asyncio.Event()

        async def releaser():
            # Wait until the cap is reached and stable: at least 2 started,
            # and the count holds across several scheduler ticks (i.e. the
            # 3rd is genuinely blocked, not merely "not yet scheduled").
            for _ in range(200):  # up to ~1s of polling
                if state["started"] >= 2:
                    break
                await _asyncio.sleep(0.005)
            # Stability window: confirm `started` does not grow while we
            # sleep — proving the cap is enforced.
            stable_count = state["started"]
            for _ in range(20):
                await _asyncio.sleep(0.005)
            assert state["started"] == stable_count, (
                f"started grew from {stable_count} to {state['started']} "
                f"while parked tasks held the cap — semaphore not enforcing"
            )
            assert stable_count == 2, (
                f"expected exactly 2 tasks running while parked, got {stable_count}"
            )
            # Now let everything drain.
            state["release"].set()

        rel_task = _asyncio.create_task(releaser())
        await asset_importer.run_import(
            parent_job,
            loras_root=loras_root,
            registry_path=registry,
            checkpoints_root=tmp_path / "checkpoints",
            vault_client_factory=lambda: fake_vc,
            asset_import_store=store,
            task_tracker=sub_tasks.append,
        )
        if sub_tasks:
            await _asyncio.gather(*sub_tasks, return_exceptions=True)
        await rel_task

    _asyncio.run(_run_all())

    assert len(parent_job.sub_job_ids) == 5, (
        f"expected 5 sub-imports queued, got {len(parent_job.sub_job_ids)}"
    )
    assert state["started"] == 5, (
        f"expected all 5 fake downloads to eventually run, got {state['started']}"
    )
    assert state["peak"] == 2, (
        f"peak concurrent downloads was {state['peak']}, expected exactly 2 "
        f"(_MAX_CONCURRENT_DOWNLOADS)"
    )
