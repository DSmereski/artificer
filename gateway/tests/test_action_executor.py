"""Tests for the ActionExecutor."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.action_executor import ActionExecutor, _aspect_dims


# Body that comfortably clears the vault_quality threshold (≥80 chars,
# ≥12 informative tokens, ≥0.30 info ratio). Reused in tests where the
# point is verifying audience/dedup/autolink, not quality.
_OK_BODY = (
    "This test note carries enough informative tokens about a topic "
    "to clear the vault quality gate without tripping the link-list filter."
)


# ---------------------------------------------------------------- aspect


def test_aspect_dims_known():
    assert _aspect_dims("portrait") == (768, 1344)
    assert _aspect_dims("landscape") == (1344, 768)
    assert _aspect_dims("square") == (1024, 1024)
    assert _aspect_dims("ultrawide") == (1536, 640)


def test_aspect_dims_fallback():
    assert _aspect_dims("nonsense") == (1024, 1024)


# ---------------------------------------------------------------- vault_learn


@pytest.mark.asyncio
async def test_vault_learn_happy():
    fake_client = MagicMock()
    fake_client.learn = AsyncMock(return_value={"ok": True, "path": "knowledge/x.md"})
    ex = ActionExecutor(vault_client_factory=lambda: fake_client)
    receipts = await ex.execute_all([{
        "verb": "vault_learn",
        "payload": {"category": "knowledge", "title": "Some Topic", "body": _OK_BODY},
    }])
    assert len(receipts) == 1
    assert receipts[0].ok is True
    assert "knowledge/x.md" in receipts[0].detail


@pytest.mark.asyncio
async def test_vault_learn_audience_clamp():
    fake_client = MagicMock()
    fake_client.learn = AsyncMock(return_value={"ok": True, "path": "x"})
    ex = ActionExecutor(vault_client_factory=lambda: fake_client)
    await ex.execute_all([{
        "verb": "vault_learn",
        "payload": {
            "category": "knowledge", "title": "Audience Topic", "body": _OK_BODY,
            "audience": ["all", "hive", "claude-code"],
        },
    }], device_audience=["hive"])
    call = fake_client.learn.await_args
    # Device audience [hive] clamps the marker's [all, hive, claude-code]
    # to just [hive].
    assert call.kwargs["audience"] == ["hive"]


@pytest.mark.asyncio
async def test_vault_learn_missing_fields():
    ex = ActionExecutor(vault_client_factory=lambda: MagicMock())
    [r] = await ex.execute_all([{"verb": "vault_learn",
                                 "payload": {"title": "x"}}])
    assert r.ok is False
    assert "missing" in r.detail


@pytest.mark.asyncio
async def test_vault_learn_no_factory_configured():
    ex = ActionExecutor()
    [r] = await ex.execute_all([{
        "verb": "vault_learn",
        "payload": {"category": "x", "title": "y", "body": "z"},
    }])
    assert r.ok is False


# ---------------------------------------------------------------- vault dedup


def _seed_existing_note(folder, slug: str, title: str) -> None:
    """Drop a minimal frontmatter+body markdown into the folder."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{slug}.md").write_text(
        f"---\ntype: knowledge\ntitle: {title}\n---\n\nbody.\n",
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_vault_learn_dedups_into_similar_existing(tmp_path):
    """Re-titling 'Kraken Star Citizen Spaceship' lands in the existing
    'Kraken Star Citizen Ship' file (Jaccard 0.6)."""
    vault = tmp_path / "vault"
    folder = vault / "knowledge" / "2026" / "04"
    _seed_existing_note(folder, "kraken-star-citizen-ship",
                        "Kraken Star Citizen Ship")

    captured: dict[str, Any] = {}

    async def _capture_learn(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"ok": True, "path": "knowledge/2026/04/kraken-star-citizen-ship.md"}

    fake_client = MagicMock()
    fake_client.learn = _capture_learn
    ex = ActionExecutor(
        vault_client_factory=lambda: fake_client,
        vault_path=vault,
    )
    [r] = await ex.execute_all([{
        "verb": "vault_learn",
        "payload": {
            "category": "knowledge",
            "title": "Kraken Star Citizen Spaceship",
            "body": (
                "More details on the Kraken: it's a Drake capital ship "
                "with multiple hangars and a dropship complement. "
                "Source: starcitizen.tools."
            ),
        },
    }])
    assert r.ok is True
    # Title was overridden to the existing one so the daemon merges.
    assert captured["title"] == "Kraken Star Citizen Ship"
    assert "merged" in r.detail
    assert r.payload["merged_from_title"] == "Kraken Star Citizen Spaceship"


@pytest.mark.asyncio
async def test_vault_learn_does_not_dedup_unrelated(tmp_path):
    """Different topics (Jaccard < 0.55) are not merged."""
    vault = tmp_path / "vault"
    folder = vault / "knowledge" / "2026" / "04"
    _seed_existing_note(folder, "kraken-cryptocurrency-exchange",
                        "Kraken Cryptocurrency Exchange")

    captured: dict[str, Any] = {}

    async def _capture_learn(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"ok": True, "path": "knowledge/x.md"}

    fake_client = MagicMock()
    fake_client.learn = _capture_learn
    ex = ActionExecutor(
        vault_client_factory=lambda: fake_client,
        vault_path=vault,
    )
    [r] = await ex.execute_all([{
        "verb": "vault_learn",
        "payload": {
            "category": "knowledge",
            "title": "Kraken Star Citizen Ship",
            "body": (
                "Ship details for the Kraken: a Drake capital carrier "
                "designed for fleet operations and dropship deployment."
            ),
        },
    }])
    assert r.ok is True
    # Title preserved — different topic.
    assert captured["title"] == "Kraken Star Citizen Ship"
    assert "merged" not in r.detail


@pytest.mark.asyncio
async def test_vault_learn_autolinks_to_existing_titles(tmp_path):
    """Body mentioning an existing note's title gets a [[wikilink]]."""
    vault = tmp_path / "vault"
    folder = vault / "knowledge" / "2026" / "04"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "drake-cutlass.md").write_text(
        "---\ntype: knowledge\ntitle: Drake Cutlass\naudience: [hive]\n---\n\nbody",
        encoding="utf-8",
    )

    captured: dict[str, Any] = {}

    async def _capture_learn(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"ok": True, "path": "knowledge/2026/04/kraken.md"}

    fake_client = MagicMock()
    fake_client.learn = _capture_learn
    ex = ActionExecutor(
        vault_client_factory=lambda: fake_client,
        vault_path=vault,
    )
    [r] = await ex.execute_all([{
        "verb": "vault_learn",
        "payload": {
            "category": "knowledge",
            "title": "Kraken",
            "body": (
                "The Kraken is built by Drake. It dwarfs smaller ships in "
                "the Drake fleet — bigger than the Drake Cutlass and "
                "designed for fleet operations."
            ),
            "audience": ["hive"],
        },
    }], device_audience=["hive"])
    assert r.ok is True
    # First mention auto-linked.
    assert "[[Drake Cutlass]]" in captured["body"]
    # Second mention left alone (cap of one per title to avoid spam).
    assert captured["body"].count("[[Drake Cutlass]]") == 1
    assert r.payload["linked_titles"] == ["Drake Cutlass"]


@pytest.mark.asyncio
async def test_vault_learn_autolink_skips_audience_denied(tmp_path):
    """Don't link to a note the caller's audience can't see."""
    vault = tmp_path / "vault"
    folder = vault / "knowledge" / "2026" / "04"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "secret.md").write_text(
        "---\ntype: knowledge\ntitle: Secret Sauce\naudience: [claude-code]\n---\n\nshhh",
        encoding="utf-8",
    )

    captured: dict[str, Any] = {}

    async def _capture_learn(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"ok": True, "path": "knowledge/x.md"}

    fake_client = MagicMock()
    fake_client.learn = _capture_learn
    ex = ActionExecutor(
        vault_client_factory=lambda: fake_client,
        vault_path=vault,
    )
    [r] = await ex.execute_all([{
        "verb": "vault_learn",
        "payload": {
            "category": "knowledge",
            "title": "Recipe Notes",
            "body": (
                "Recipe notes: the Secret Sauce should not be linked here "
                "because it's a different audience. Cooking method follows."
            ),
            "audience": ["hive"],
        },
    }], device_audience=["hive"])
    assert r.ok is True
    # No wikilink — hive-audience caller can't see claude-code-only notes.
    assert "[[Secret Sauce]]" not in captured["body"]
    assert r.payload.get("linked_titles", []) == []


@pytest.mark.asyncio
async def test_vault_learn_autolink_preserves_existing_wikilinks(tmp_path):
    """Existing [[Foo]] wikilinks in the body must not be re-wrapped."""
    vault = tmp_path / "vault"
    folder = vault / "knowledge" / "2026" / "04"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "drake.md").write_text(
        "---\ntype: knowledge\ntitle: Drake\naudience: [hive]\n---\n\nbody",
        encoding="utf-8",
    )

    captured: dict[str, Any] = {}

    async def _capture_learn(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"ok": True, "path": "knowledge/x.md"}

    fake_client = MagicMock()
    fake_client.learn = _capture_learn
    ex = ActionExecutor(
        vault_client_factory=lambda: fake_client,
        vault_path=vault,
    )
    [r] = await ex.execute_all([{
        "verb": "vault_learn",
        "payload": {
            "category": "knowledge",
            "title": "Notes",
            "body": (
                "Already mentioned [[Drake]] before. Drake also makes the "
                "Cutlass and several other ships in the fleet roster."
            ),
            "audience": ["hive"],
        },
    }], device_audience=["hive"])
    assert r.ok is True
    body = captured["body"]
    # Existing wikilink kept.
    assert "[[Drake]]" in body
    # Bare second mention also got linked (so the user's "always link
    # when possible" intent is honored).
    assert body.count("[[Drake]]") == 2


@pytest.mark.asyncio
async def test_vault_learn_dedup_skipped_for_journal(tmp_path):
    """Append-style categories (journal/session/person) skip the dedup;
    the daemon already has merge semantics for them."""
    vault = tmp_path / "vault"
    folder = vault / "journals"
    _seed_existing_note(folder, "hive", "Hive")

    captured: dict[str, Any] = {}

    async def _capture_learn(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"ok": True, "path": "journals/hive.md"}

    fake_client = MagicMock()
    fake_client.learn = _capture_learn
    ex = ActionExecutor(
        vault_client_factory=lambda: fake_client,
        vault_path=vault,
    )
    [r] = await ex.execute_all([{
        "verb": "vault_learn",
        "payload": {
            "category": "journal",
            "title": "Hive Status Check",
            "body": "all good",
            "author": "hive",
        },
    }])
    assert r.ok is True
    # Title untouched — journal is append-style, no dedup ran.
    assert captured["title"] == "Hive Status Check"


# ---------------------------------------------------------------- image_render


@pytest.mark.asyncio
async def test_image_render_happy():
    fake_shim = MagicMock()
    fake_job = MagicMock()
    fake_job.id = "job-abc"
    fake_shim.enqueue = AsyncMock(return_value=fake_job)
    fake_store = MagicMock()
    ex = ActionExecutor(image_shim=fake_shim, image_build_store=fake_store)
    [r] = await ex.execute_all([{
        "verb": "image_render",
        "payload": {"prompt": "an elf", "aspect": "portrait", "loras": ["A"]},
    }], device_id="dev1")
    assert r.ok is True
    assert r.payload["job_id"] == "job-abc"
    fake_shim.enqueue.assert_awaited_once()
    kwargs = fake_shim.enqueue.await_args.kwargs
    assert kwargs["prompt"] == "an elf"
    assert kwargs["width"] == 768 and kwargs["height"] == 1344
    assert kwargs["lora_overrides"] == ["A"]
    fake_store.clear.assert_called_once_with("dev1")


@pytest.mark.asyncio
async def test_image_render_missing_prompt():
    ex = ActionExecutor(image_shim=MagicMock())
    [r] = await ex.execute_all([{"verb": "image_render", "payload": {}}])
    assert r.ok is False
    assert "prompt" in r.detail


# ---------------------------------------------------------------- ntfy_push


@pytest.mark.asyncio
async def test_ntfy_push_happy():
    fake_ntfy = MagicMock()
    fake_ntfy.enabled = True
    fake_ntfy.publish = AsyncMock()
    ex = ActionExecutor(ntfy=fake_ntfy)
    [r] = await ex.execute_all([{
        "verb": "ntfy_push",
        "payload": {"title": "T", "message": "hello"},
    }])
    assert r.ok is True
    fake_ntfy.publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_ntfy_disabled_returns_error():
    fake_ntfy = MagicMock()
    fake_ntfy.enabled = False
    ex = ActionExecutor(ntfy=fake_ntfy)
    [r] = await ex.execute_all([{
        "verb": "ntfy_push", "payload": {"message": "x"},
    }])
    assert r.ok is False


# ---------------------------------------------------------------- create_skill


@pytest.mark.asyncio
async def test_create_skill_writes(tmp_path):
    from gateway.skill_registry import SkillRegistry
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    reg = SkillRegistry(skills_dir)
    reg.load()

    body = (
        "---\nname: new-skill\ndescription: A test skill.\n"
        "audience: [hive]\n---\n\n# New skill\n\n"
        "1. Step one (this body must be ≥100 chars to pass the rubric).\n"
        "2. Step two.\n"
    )
    ex = ActionExecutor(skill_registry=reg)
    [r] = await ex.execute_all([{
        "verb": "create_skill",
        "payload": {"name": "new-skill", "body": body},
    }])
    assert r.ok is True
    assert reg.get("new-skill") is not None


@pytest.mark.asyncio
async def test_create_skill_short_body_rejected(tmp_path):
    from gateway.skill_registry import SkillRegistry
    reg = SkillRegistry(tmp_path / "skills")
    reg.load()
    ex = ActionExecutor(skill_registry=reg)
    [r] = await ex.execute_all([{
        "verb": "create_skill",
        "payload": {"name": "tiny", "body": "tiny"},
    }])
    assert r.ok is False
    assert "rubric" in r.detail


# ---------------------------------------------------------------- image_build_update


@pytest.mark.asyncio
async def test_image_build_update_persists(tmp_path):
    from gateway.image_build_state import ImageBuildStore
    store = ImageBuildStore(tmp_path)
    ex = ActionExecutor(image_build_store=store)
    [r] = await ex.execute_all([{
        "verb": "image_build_update",
        "payload": {"subject": "elf", "aspect": "portrait"},
    }], device_id="dev1")
    assert r.ok is True
    assert "subject" in r.payload["changed"]
    assert store.get("dev1").subject == "elf"


# ---------------------------------------------------------------- unknown


@pytest.mark.asyncio
async def test_unknown_verb_returns_error():
    ex = ActionExecutor()
    [r] = await ex.execute_all([{"verb": "nuke_kernel", "payload": {}}])
    assert r.ok is False
    assert "unknown" in r.detail


@pytest.mark.asyncio
async def test_executor_recovers_from_action_exception():
    fake_client = MagicMock()
    fake_client.learn = AsyncMock(side_effect=RuntimeError("kaboom"))
    ex = ActionExecutor(vault_client_factory=lambda: fake_client)
    [r] = await ex.execute_all([{
        "verb": "vault_learn",
        "payload": {
            "category": "knowledge",
            "title": "Recovery Test",
            "body": _OK_BODY,
        },
    }])
    assert r.ok is False
    assert "kaboom" in r.detail


# ---------------------------------------------------------------- vault_forget


@pytest.mark.asyncio
async def test_vault_forget_by_paths(tmp_path):
    """Explicit `paths: [...]` deletes only those files, confined to vault."""
    vault = tmp_path / "vault"
    (vault / "knowledge" / "2026" / "04").mkdir(parents=True)
    a = vault / "knowledge" / "2026" / "04" / "drake-cutlass.md"
    b = vault / "knowledge" / "2026" / "04" / "keep-this.md"
    a.write_text("---\ntype: knowledge\n---\nfacts about cutlass\n")
    b.write_text("---\ntype: knowledge\n---\ndo not delete me\n")

    ex = ActionExecutor(vault_path=vault)
    [r] = await ex.execute_all([{
        "verb": "vault_forget",
        "payload": {"paths": ["knowledge/2026/04/drake-cutlass.md"]},
    }])
    assert r.ok is True
    assert "drake-cutlass.md" in r.detail
    assert not a.exists()
    assert b.exists()    # untouched


@pytest.mark.asyncio
async def test_vault_forget_by_query(tmp_path):
    """Query-based delete matches notes whose filename contains all tokens."""
    vault = tmp_path / "vault"
    kn = vault / "knowledge" / "2026" / "04"
    kn.mkdir(parents=True)
    a = kn / "drake-cutlass-black-research.md"
    b = kn / "drake-cutlass-specs.md"
    c = kn / "constellation-andromeda.md"
    for p in (a, b, c):
        p.write_text("body")

    ex = ActionExecutor(vault_path=vault)
    [r] = await ex.execute_all([{
        "verb": "vault_forget",
        "payload": {"query": "drake cutlass"},
    }])
    assert r.ok is True
    assert not a.exists()
    assert not b.exists()
    assert c.exists()    # constellation note kept


@pytest.mark.asyncio
async def test_vault_forget_path_traversal_blocked(tmp_path):
    """Paths that resolve outside the vault are silently dropped."""
    vault = tmp_path / "vault"
    vault.mkdir()
    outside = tmp_path / "outside" / "secret.md"
    outside.parent.mkdir()
    outside.write_text("DO NOT DELETE")

    ex = ActionExecutor(vault_path=vault)
    [r] = await ex.execute_all([{
        "verb": "vault_forget",
        "payload": {"paths": ["../outside/secret.md"]},
    }])
    assert r.ok is False
    assert outside.exists()    # the traversal was blocked


@pytest.mark.asyncio
async def test_vault_forget_paths_cannot_target_canon(tmp_path):
    """canon/ and ops/ are read-only — `paths` arg must not delete from
    them, even though they resolve inside the vault root. This pins the
    fix for the security review's HIGH finding: a prompt-injected note
    pointing the synthesizer at `canon/hive.md` would otherwise pass."""
    vault = tmp_path / "vault"
    (vault / "canon").mkdir(parents=True)
    (vault / "ops" / "escalations").mkdir(parents=True)
    canon_note = vault / "canon" / "hive.md"
    ops_note = vault / "ops" / "escalations" / "bug.md"
    canon_note.write_text("---\ntype: canon\n---\nground truth")
    ops_note.write_text("---\ntype: escalation\n---\nopen bug")

    ex = ActionExecutor(vault_path=vault)
    [r] = await ex.execute_all([{
        "verb": "vault_forget",
        "payload": {"paths": [
            "canon/hive.md",
            "ops/escalations/bug.md",
        ]},
    }])
    # Both targets dropped → no targets matched → ok=False.
    assert r.ok is False
    assert canon_note.exists()
    assert ops_note.exists()


@pytest.mark.asyncio
async def test_vault_forget_no_targets(tmp_path):
    """Query with no matches returns ok=False with a clear detail."""
    vault = tmp_path / "vault"
    (vault / "knowledge").mkdir(parents=True)
    ex = ActionExecutor(vault_path=vault)
    [r] = await ex.execute_all([{
        "verb": "vault_forget",
        "payload": {"query": "nothing here"},
    }])
    assert r.ok is False
    assert "no matching" in r.detail


@pytest.mark.asyncio
async def test_vault_forget_caps_at_20(tmp_path):
    """Safety cap: never delete more than 20 in one action."""
    vault = tmp_path / "vault"
    kn = vault / "knowledge"
    kn.mkdir(parents=True)
    for i in range(30):
        (kn / f"drake-{i:02d}.md").write_text("x")
    ex = ActionExecutor(vault_path=vault)
    [r] = await ex.execute_all([{
        "verb": "vault_forget",
        "payload": {"query": "drake"},
    }])
    assert r.ok is True
    remaining = list(kn.glob("drake-*.md"))
    assert len(remaining) == 10    # 30 - 20 cap


# ---------------------------------------------------------------- audit fixes


@pytest.mark.asyncio
async def test_vault_forget_filters_by_device_audience(tmp_path):
    """A device with audience=['claude-code'] cannot delete notes
    whose audience is ['hive'] — fail-closed protection."""
    vault = tmp_path / "vault"
    kn = vault / "knowledge"
    kn.mkdir(parents=True)
    (kn / "hive-only.md").write_text(
        "---\naudience: [hive]\n---\n\nHive's note\n",
    )
    (kn / "claude-only.md").write_text(
        "---\naudience: [claude-code]\n---\n\nCC's note\n",
    )
    (kn / "shared.md").write_text(
        "---\naudience: [hive, claude-code]\n---\n\nshared\n",
    )

    ex = ActionExecutor(vault_path=vault)
    [r] = await ex.execute_all([{
        "verb": "vault_forget",
        "payload": {"query": "md"},      # match all
    }], device_audience=["claude-code"])
    # claude-only AND shared get deleted; hive-only survives.
    assert r.ok is True
    assert (kn / "hive-only.md").exists(), "hive-only should survive"
    assert not (kn / "claude-only.md").exists(), "claude-only should be deleted"
    assert not (kn / "shared.md").exists(), "shared should be deleted"


@pytest.mark.asyncio
async def test_vault_forget_audience_all_passes(tmp_path):
    """Notes with audience=['all'] are deletable by any device."""
    vault = tmp_path / "vault"
    kn = vault / "knowledge"
    kn.mkdir(parents=True)
    (kn / "world.md").write_text(
        "---\naudience: [all]\n---\n\nworld stuff\n",
    )

    ex = ActionExecutor(vault_path=vault)
    [r] = await ex.execute_all([{
        "verb": "vault_forget",
        "payload": {"query": "world"},
    }], device_audience=["claude-code"])
    assert r.ok is True
    assert not (kn / "world.md").exists()


@pytest.mark.asyncio
async def test_vault_forget_no_audience_passes_legacy(tmp_path):
    """When the caller doesn't supply device_audience (legacy path),
    audience filtering is skipped — keeps existing tests + chat
    flows that haven't plumbed the field through."""
    vault = tmp_path / "vault"
    kn = vault / "knowledge"
    kn.mkdir(parents=True)
    (kn / "hive-only.md").write_text(
        "---\naudience: [hive]\n---\n\nHive's note\n",
    )

    ex = ActionExecutor(vault_path=vault)
    # No device_audience kwarg — should NOT filter.
    [r] = await ex.execute_all([{
        "verb": "vault_forget",
        "payload": {"query": "hive"},
    }])
    assert r.ok is True
    assert not (kn / "hive-only.md").exists()


@pytest.mark.asyncio
async def test_image_render_refuses_synthesizer_reference_path():
    """Synthesizer-emitted reference_path is a file-read disclosure
    vector (PIL.open(arbitrary path)). Must be refused outright."""
    fake_shim = MagicMock()
    fake_shim.enqueue = AsyncMock()
    ex = ActionExecutor(image_shim=fake_shim)
    [r] = await ex.execute_all([{
        "verb": "image_render",
        "payload": {
            "prompt": "x",
            "reference_path": "/tmp/.ssh/id_ed25519",
        },
    }])
    assert r.ok is False
    assert "reference_path" in r.detail
    # And the shim was NEVER called.
    assert fake_shim.enqueue.called is False


@pytest.mark.asyncio
async def test_image_render_accepts_reference_media_id(tmp_path):
    """The safe replacement: media_id resolved through
    _resolve_uploaded_reference."""
    sd = tmp_path
    uploads = sd / "media-uploads"
    uploads.mkdir()
    (uploads / "abc12345.jpg").write_bytes(b"\xff\xd8\xff\xd9")  # tiny

    fake_job = MagicMock()
    fake_job.id = "job-99"
    fake_shim = MagicMock()
    fake_shim.enqueue = AsyncMock(return_value=fake_job)
    ex = ActionExecutor(image_shim=fake_shim, state_dir=sd)
    [r] = await ex.execute_all([{
        "verb": "image_render",
        "payload": {"prompt": "x", "reference_media_id": "abc12345"},
    }])
    assert r.ok is True
    kw = fake_shim.enqueue.call_args.kwargs
    assert kw["reference_path"].endswith("abc12345.jpg")


@pytest.mark.asyncio
async def test_image_render_rejects_unknown_reference_media_id(tmp_path):
    sd = tmp_path
    fake_shim = MagicMock()
    fake_shim.enqueue = AsyncMock()
    ex = ActionExecutor(image_shim=fake_shim, state_dir=sd)
    [r] = await ex.execute_all([{
        "verb": "image_render",
        "payload": {"prompt": "x", "reference_media_id": "doesnotexist"},
    }])
    assert r.ok is False
    assert "unknown reference_media_id" in r.detail


# ---------------------------------------------------------------- entity_page_update slug validation (H-2)


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_slug", [
    "../passwd",
    "foo bar",
    "",
    "A" * 81,
    "../etc/passwd",
    "slug\nwith\nnewlines",
    '{"json": "injection"}',
    "UPPERCASE",
    "has space",
    "-" * 81,
])
async def test_entity_page_update_rejects_invalid_slug(bad_slug: str) -> None:
    fake_client = MagicMock()
    fake_client.entity_page_update = AsyncMock(return_value={"ok": True})
    ex = ActionExecutor(vault_client_factory=lambda: fake_client)
    [r] = await ex.execute_all([{
        "verb": "entity_page_update",
        "payload": {"id": bad_slug, "title": "Test Title", "kind": "concept"},
    }])
    assert r.ok is False
    assert r.detail == "invalid slug — must match ^[a-z0-9_-]{1,80}$"
    fake_client.entity_page_update.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("good_slug", [
    "penguin",
    "the-gallery-refactor",
    "thread_42",
    "a",
    "z" * 80,
    "abc-123_xyz",
])
async def test_entity_page_update_accepts_valid_slug(good_slug: str) -> None:
    fake_client = MagicMock()
    fake_client.entity_page_update = AsyncMock(return_value={"ok": True})
    ex = ActionExecutor(vault_client_factory=lambda: fake_client)
    [r] = await ex.execute_all([{
        "verb": "entity_page_update",
        "payload": {"id": good_slug, "title": "Test Title", "kind": "concept"},
    }])
    assert r.ok is True
    fake_client.entity_page_update.assert_called_once()
