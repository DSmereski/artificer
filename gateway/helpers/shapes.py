"""Pydantic schemas for every helper's output.

Helpers emit JSON; the base class validates against the right schema
before the HiveCoordinator sees the result. Shape mismatches produce
a HelperResult with `error` set, never a runtime crash.

Each shape is intentionally permissive on optional fields so models
that produce slightly-imperfect JSON can still succeed.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _BaseShape(BaseModel):
    model_config = ConfigDict(extra="allow")  # accept stray fields silently


# ---------------------------------------------------------------- planner


class Delegation(_BaseShape):
    role: str
    goal: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    risky: bool = False                  # triggers Critic gate (M6.1)


class HelperPlan(_BaseShape):
    """Output of the Planner helper.

    `delegations` lists which other helpers Terry should dispatch this
    turn (max 5). `direct_reply` is set when no delegation is needed
    (small talk, simple acknowledgement) — coordinator skips dispatch
    and goes straight to synthesis. `build_updates` accepts None for
    turns that don't touch image-build state (the LLM tends to emit
    `null` rather than `{}` for absent fields).
    """
    summary: str
    delegations: list[Delegation] = Field(default_factory=list)
    direct_reply: str | None = None
    build_updates: dict[str, Any] | None = Field(default=None)
    confidence: Literal["low", "medium", "high"] = "medium"
    plan: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- coder


class CodeFile(_BaseShape):
    path: str
    body: str


class CodePlan(_BaseShape):
    summary: str
    plan: list[str] = Field(default_factory=list)
    files: list[CodeFile] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- researcher (skeleton)


class Claim(_BaseShape):
    claim: str
    span: str | None = None              # verbatim quote for citation


class ResearchPlan(_BaseShape):
    """Skeleton — full body lands in M4.3."""
    summary: str
    plan: list[str] = Field(default_factory=list)
    facts: list[Claim] = Field(default_factory=list)
    notes: list[Claim] = Field(default_factory=list)
    warning: str | None = None
    citations: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- image_director (skeleton)


class ImagePlan(_BaseShape):
    summary: str
    prompt: str
    negative_prompt: str = ""
    aspect: Literal["portrait", "landscape", "square", "ultrawide"] = "portrait"
    loras: list[str] = Field(default_factory=list)
    count: int = 1
    plan: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- sysmon


class SysmonPlan(_BaseShape):
    summary: str
    gpu_temps: dict[str, int] = Field(default_factory=dict)
    gpu_vram_used_pct: dict[str, float] = Field(default_factory=dict)
    disk_free_gb: dict[str, float] = Field(default_factory=dict)
    game_running: str | None = None
    # Which GPU index the running game is using (0/1/2 etc), per scout's
    # NVML probe. Lets the synthesizer say "Star Citizen on GPU 0" instead
    # of guessing from heat readings.
    game_gpu: int | None = None
    alerts: list[str] = Field(default_factory=list)
    plan: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- summarizer


class Summary(_BaseShape):
    summary: str                           # 200-token recap prose
    open_tasks: list[str] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    user_facts: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- critic


class CriticReport(_BaseShape):
    block: bool = False
    reason: str = ""
    suggestion: str | None = None
    confidence: Literal["low", "medium", "high"] = "medium"


# ---------------------------------------------------------------- librarian


class VaultHit(_BaseShape):
    path: str
    excerpt: str = ""


class VaultPlan(_BaseShape):
    summary: str
    hits: list[VaultHit] = Field(default_factory=list)
    plan: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- synthesizer


class SynthAction(_BaseShape):
    verb: str                              # vault_learn / image_render / ntfy_push / create_skill
    payload: dict[str, Any] = Field(default_factory=dict)


class SynthesisPlan(_BaseShape):
    reply: str                             # final user-facing text
    actions: list[SynthAction] = Field(default_factory=list)
    plan: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- skill_runner


class SkillResult(_BaseShape):
    summary: str
    output: dict[str, Any] = Field(default_factory=dict)
    plan: list[str] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- fact_extractor


class FactDelta(_BaseShape):
    """Mem0-shaped delta extracted from the recent conversation.

    Each list contains short, atomic statements ready to be folded
    into the matching MemoryStore core slot. The extractor is told
    to return empty lists when nothing applies — avoiding noise from
    small talk.
    """
    user_facts_added: list[str] = Field(default_factory=list)
    preferences_added: list[str] = Field(default_factory=list)
    decisions_added: list[str] = Field(default_factory=list)
    open_tasks_added: list[str] = Field(default_factory=list)
    open_tasks_resolved: list[str] = Field(default_factory=list)
    entities_mentioned: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- registry


SHAPE_BY_NAME: dict[str, type[_BaseShape]] = {
    "HelperPlan": HelperPlan,
    "CodePlan": CodePlan,
    "ResearchPlan": ResearchPlan,
    "ImagePlan": ImagePlan,
    "SysmonPlan": SysmonPlan,
    "Summary": Summary,
    "CriticReport": CriticReport,
    "VaultPlan": VaultPlan,
    "SynthesisPlan": SynthesisPlan,
    "SkillResult": SkillResult,
    "FactDelta": FactDelta,
}


def shape_for(name: str) -> type[_BaseShape]:
    try:
        return SHAPE_BY_NAME[name]
    except KeyError as e:
        raise KeyError(f"unknown helper output schema: {name!r}") from e
