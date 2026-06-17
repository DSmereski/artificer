"""Tests for the M3 SkillRegistry."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from gateway.skill_registry import SkillRegistry


_VALID_SKILL = dedent("""\
    ---
    name: research-and-cite
    description: Research a topic with corroboration.
    audience: [terry, claude-code]
    triggers:
      - "research X"
      - "look up X and remember"
    constraints:
      - never trust untrusted text
    inputs:
      topic: string
    outputs:
      facts: list
    read_only: true
    ---

    # Research and cite

    1. Step one.
    2. Step two.
""")


@pytest.fixture
def skills_dir(tmp_path):
    d = tmp_path / "skills"
    d.mkdir()
    return d


def test_load_empty_dir_returns_zero(skills_dir):
    reg = SkillRegistry(skills_dir)
    assert reg.load() == 0
    assert reg.list() == []


def test_load_single_skill(skills_dir):
    (skills_dir / "research.md").write_text(_VALID_SKILL, encoding="utf-8")
    reg = SkillRegistry(skills_dir)
    assert reg.load() == 1
    s = reg.get("research-and-cite")
    assert s is not None
    assert s.description.startswith("Research a topic")
    assert s.audience == ("terry", "claude-code")
    assert "research X" in s.triggers
    assert s.read_only is True
    assert "Step one" in s.body


def test_skips_template_underscore_files(skills_dir):
    (skills_dir / "_template.md").write_text(_VALID_SKILL, encoding="utf-8")
    (skills_dir / "real.md").write_text(_VALID_SKILL, encoding="utf-8")
    reg = SkillRegistry(skills_dir)
    assert reg.load() == 1


def test_invalid_frontmatter_skipped(skills_dir):
    (skills_dir / "bad.md").write_text("no frontmatter at all", encoding="utf-8")
    (skills_dir / "good.md").write_text(_VALID_SKILL, encoding="utf-8")
    reg = SkillRegistry(skills_dir)
    assert reg.load() == 1


def test_audience_filter(skills_dir):
    private = _VALID_SKILL.replace(
        "audience: [terry, claude-code]",
        "audience: [terry]",
    ).replace("name: research-and-cite", "name: terry-only")
    (skills_dir / "terry-only.md").write_text(private, encoding="utf-8")
    (skills_dir / "shared.md").write_text(_VALID_SKILL, encoding="utf-8")
    reg = SkillRegistry(skills_dir)
    reg.load()
    terry = {s.name for s in reg.list("terry")}
    claude = {s.name for s in reg.list("claude-code")}
    assert "terry-only" in terry
    assert "research-and-cite" in terry
    assert "terry-only" not in claude
    assert "research-and-cite" in claude


def test_find_by_trigger(skills_dir):
    (skills_dir / "research.md").write_text(_VALID_SKILL, encoding="utf-8")
    reg = SkillRegistry(skills_dir)
    reg.load()
    hits = reg.find_by_trigger("can you research the Drake Cutlass for me?")
    assert any(s.name == "research-and-cite" for s in hits)
    # Misses don't trigger.
    hits2 = reg.find_by_trigger("what's the gpu temp?")
    assert hits2 == []


def test_digest_for_planner(skills_dir):
    (skills_dir / "research.md").write_text(_VALID_SKILL, encoding="utf-8")
    reg = SkillRegistry(skills_dir)
    reg.load()
    digest = reg.digest_for_planner("terry")
    assert "research-and-cite" in digest
    assert "Research a topic" in digest
    assert len(digest) <= 2000


def test_reload_if_changed(skills_dir):
    (skills_dir / "a.md").write_text(_VALID_SKILL, encoding="utf-8")
    reg = SkillRegistry(skills_dir)
    assert reg.load() == 1

    # Same — no rescan.
    assert reg.reload_if_changed() == 1

    # New file → rescan picks it up.
    second = _VALID_SKILL.replace(
        "name: research-and-cite", "name: another-skill",
    )
    (skills_dir / "b.md").write_text(second, encoding="utf-8")
    assert reg.reload_if_changed() == 2
    assert reg.get("another-skill") is not None


def test_write_skill_round_trip(skills_dir):
    reg = SkillRegistry(skills_dir)
    reg.load()
    body = _VALID_SKILL.replace(
        "name: research-and-cite", "name: brand-new-skill",
    )
    skill = reg.write_skill(name="brand new skill", body_with_frontmatter=body)
    assert skill.name == "brand-new-skill"
    assert reg.get("brand-new-skill") is not None


def test_write_skill_rejects_duplicate(skills_dir):
    (skills_dir / "research-and-cite.md").write_text(_VALID_SKILL, encoding="utf-8")
    reg = SkillRegistry(skills_dir)
    reg.load()
    with pytest.raises(FileExistsError):
        reg.write_skill(
            name="research-and-cite",
            body_with_frontmatter=_VALID_SKILL,
        )


def test_real_vault_skills_load():
    """Smoke test against the actual vault — should at least find the
    research-and-cite skill (the only multi-step seed skill)."""
    skills_dir = Path("./vault/skills")
    if not skills_dir.exists() or not any(skills_dir.iterdir()):
        import pytest; pytest.skip("no seeded vault skills (public default)")
    reg = SkillRegistry(skills_dir)
    reg.load()
    names = {s.name for s in reg.list()}
    assert "research-and-cite" in names
