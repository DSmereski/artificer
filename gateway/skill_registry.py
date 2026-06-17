"""SkillRegistry — load + search + digest the vault skills.

Skills are markdown files in `Ai-Team-Vault/skills/`. Each has YAML
frontmatter (Claude-Code-compatible) plus a body of numbered steps.

Both Claude Code and Terry's hive consume the SAME files: Claude via
the symlinked path under `~/.claude/skills/team/`, Terry via this
registry.

Reload semantics:
  - Loaded once on gateway startup.
  - `reload_if_changed()` rescans + recomputes sha256s; cheap enough
    to call from a periodic background task.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger("gateway.skills")


_FRONTMATTER_RE = re.compile(
    r"\A---\s*\n(?P<fm>.*?)\n---\s*\n(?P<body>.*)\Z",
    re.DOTALL,
)


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    audience: tuple[str, ...]
    inputs: dict[str, Any]
    outputs: dict[str, Any]
    triggers: tuple[str, ...]
    constraints: tuple[str, ...]
    body: str
    path: Path
    read_only: bool
    sha256: str

    def is_visible_to(self, audience: str) -> bool:
        return "all" in self.audience or audience in self.audience


class SkillRegistry:
    def __init__(self, skills_dir: Path) -> None:
        self._dir = skills_dir
        self._skills: dict[str, Skill] = {}
        self._signature: str = ""

    # ---------------------------------------------------------------- io

    def load(self) -> int:
        """Rescan the skills directory. Returns the count loaded."""
        if not self._dir.is_dir():
            self._skills = {}
            self._signature = ""
            return 0
        out: dict[str, Skill] = {}
        for path in sorted(self._dir.glob("*.md")):
            if path.name.startswith("_"):
                continue                # skip _template.md and friends
            try:
                skill = _parse_skill(path)
            except _SkillParseError as e:
                log.warning("skill %s skipped: %s", path.name, e)
                continue
            if skill.name in out:
                log.warning("duplicate skill name %r in %s", skill.name, path)
                continue
            out[skill.name] = skill
        self._skills = out
        self._signature = _signature(out)
        return len(out)

    def reload_if_changed(self) -> int:
        """Cheap freshness check — reloads only if any file's mtime
        or content changed. Returns the new count."""
        if not self._dir.is_dir():
            if self._skills:
                self._skills = {}
                self._signature = ""
            return 0
        sig_now = _dir_signature(self._dir)
        if sig_now == self._signature:
            return len(self._skills)
        return self.load()

    # ---------------------------------------------------------------- read

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def list(self, audience: str = "all") -> list[Skill]:
        return [s for s in self._skills.values() if s.is_visible_to(audience)]

    def find_by_trigger(self, user_msg: str) -> list[Skill]:
        """Return skills whose trigger phrase appears in user_msg.

        Matching is case-insensitive substring against each trigger's
        words, with `X` placeholders ignored. Best-effort — the planner
        does the real disambiguation.
        """
        if not user_msg:
            return []
        msg_low = user_msg.lower()
        hits: list[tuple[int, Skill]] = []
        for s in self._skills.values():
            for trig in s.triggers:
                # Strip `X`/`<...>` placeholders.
                t_low = re.sub(r"\b[Xx]\b|<[^>]+>", "", trig).lower().strip()
                if not t_low:
                    continue
                # Word-bag overlap: every trigger word must appear in
                # the message (cheap approximation).
                words = [w for w in t_low.split() if len(w) > 2]
                if words and all(w in msg_low for w in words):
                    hits.append((len(words), s))
                    break
        # Sort by trigger word count (longer matches first).
        hits.sort(key=lambda p: -p[0])
        return [s for _, s in hits]

    def digest_for_planner(self, audience: str = "terry") -> str:
        """Markdown bullet list (≤2000 chars) used in Planner system
        prompt so the planner sees the catalogue."""
        skills = self.list(audience)
        if not skills:
            return "(no skills available)"
        lines: list[str] = ["## Available skills"]
        for s in skills:
            triggers = ", ".join(s.triggers[:3])
            lines.append(f"- **{s.name}**: {s.description} (triggers: {triggers})")
        out = "\n".join(lines)
        return out[:2000]

    # ---------------------------------------------------------------- writes

    def write_skill(
        self,
        *,
        name: str,
        body_with_frontmatter: str,
    ) -> Skill:
        """Write a new skill to disk and reload. Used by the
        [CREATE_SKILL] flow (after Critic approval).

        The on-disk filename comes from `_slugify(name)` (always
        confined to `self._dir`), but `Skill.name` is read from the
        body's frontmatter — they could legally differ. We require
        them to slugify identically and re-confine the resolved path
        so a malicious frontmatter name can't trick a downstream
        consumer that treats `Skill.name` as a path component.
        """
        slug = _slugify(name)
        path = (self._dir / f"{slug}.md").resolve()
        # Confine: the resolved file must live directly under skills_dir.
        try:
            path.relative_to(self._dir.resolve())
        except ValueError:
            raise ValueError(f"skill path escaped {self._dir}: {path}")
        if path.exists():
            raise FileExistsError(f"skill {name!r} already exists at {path}")
        self._dir.mkdir(parents=True, exist_ok=True)
        path.write_text(body_with_frontmatter, encoding="utf-8")
        try:
            skill = _parse_skill(path)
        except _SkillParseError as e:
            path.unlink(missing_ok=True)
            raise ValueError(f"invalid skill: {e}") from e
        # Frontmatter `name` must slugify to the same path or we'd be
        # mounting a skill at filename A whose Skill.name is B.
        if _slugify(skill.name) != slug:
            path.unlink(missing_ok=True)
            raise ValueError(
                f"frontmatter name {skill.name!r} doesn't match "
                f"requested name {name!r} (slug differs)"
            )
        self._skills[skill.name] = skill
        self._signature = _signature(self._skills)
        return skill


# ---------------------------------------------------------------- parsing


class _SkillParseError(ValueError):
    pass


def _parse_skill(path: Path) -> Skill:
    raw = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(raw)
    if m is None:
        raise _SkillParseError("missing frontmatter")
    try:
        fm = yaml.safe_load(m.group("fm")) or {}
    except yaml.YAMLError as e:
        raise _SkillParseError(f"yaml parse error: {e}") from e
    name = fm.get("name")
    if not isinstance(name, str) or not name:
        raise _SkillParseError("missing name")
    description = fm.get("description") or ""
    if not isinstance(description, str):
        description = str(description)
    audience_raw = fm.get("audience") or ["all"]
    if isinstance(audience_raw, str):
        audience = (audience_raw,)
    else:
        audience = tuple(str(a) for a in audience_raw)
    triggers_raw = fm.get("triggers") or []
    triggers = tuple(str(t) for t in triggers_raw)
    constraints_raw = fm.get("constraints") or []
    constraints = tuple(str(c) for c in constraints_raw)
    inputs = fm.get("inputs") or {}
    if not isinstance(inputs, dict):
        inputs = {}
    outputs = fm.get("outputs") or {}
    if not isinstance(outputs, dict):
        outputs = {}
    body = m.group("body")
    sha256 = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return Skill(
        name=name,
        description=description[:240],
        audience=audience,
        inputs=inputs,
        outputs=outputs,
        triggers=triggers,
        constraints=constraints,
        body=body,
        path=path,
        read_only=bool(fm.get("read_only", False)),
        sha256=sha256,
    )


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-") or "skill"


def _signature(skills: dict[str, Skill]) -> str:
    parts = sorted(f"{s.name}:{s.sha256}" for s in skills.values())
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _dir_signature(skills_dir: Path) -> str:
    parts: list[str] = []
    for path in sorted(skills_dir.glob("*.md")):
        if path.name.startswith("_"):
            continue
        try:
            stat = path.stat()
            parts.append(f"{path.name}:{stat.st_mtime_ns}:{stat.st_size}")
        except OSError:
            continue
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
