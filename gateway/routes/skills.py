"""/v1/skills — list, fetch, and author skills.

Surfaces the M3 SkillRegistry. Skills authored either by the user
(markdown drop into vault/skills/), by Claude Code (via the symlink),
or by Terry (after Critic approval) appear here.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from gateway.deps import require_device, state


router = APIRouter(prefix="/v1/skills", tags=["skills"])
log = logging.getLogger("gateway.skills_route")


@router.get("")
def list_skills(
    audience: str = Query(default="all"),
    device=Depends(require_device),
    request: Request = None,
) -> dict:
    st = state(request)
    reg = st.skill_registry
    if reg is None:
        raise HTTPException(503, "skill registry not initialised")
    reg.reload_if_changed()
    return {
        "skills": [
            {
                "name": s.name,
                "description": s.description,
                "triggers": list(s.triggers),
                "audience": list(s.audience),
                "read_only": s.read_only,
                "path": str(s.path.relative_to(s.path.parent.parent.parent)
                            if s.path.parent.parent.parent in s.path.parents
                            else s.path),
            }
            for s in reg.list(audience=audience)
        ],
    }


class CreateSkillRequest(BaseModel):
    name: str = Field(..., max_length=64)
    body: str = Field(..., min_length=100, max_length=8 * 1024)


@router.post("")
async def create_skill(
    body: CreateSkillRequest,
    device=Depends(require_device),
    request: Request = None,
) -> dict:
    """Author a new skill. The body must be a complete markdown file
    with `---` frontmatter (matching the M3 schema)."""
    st = state(request)
    reg = st.skill_registry
    if reg is None:
        raise HTTPException(503, "skill registry not initialised")
    if "---" not in body.body:
        raise HTTPException(400, "body must include `---` frontmatter")
    try:
        skill = reg.write_skill(
            name=body.name, body_with_frontmatter=body.body,
        )
    except FileExistsError:
        raise HTTPException(409, f"skill {body.name!r} already exists")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "name": skill.name,
        "path": str(skill.path),
        "audience": list(skill.audience),
    }


@router.get("/{name}")
def get_skill(
    name: str,
    device=Depends(require_device),
    request: Request = None,
) -> dict:
    st = state(request)
    reg = st.skill_registry
    if reg is None:
        raise HTTPException(503, "skill registry not initialised")
    skill = reg.get(name)
    if skill is None:
        raise HTTPException(404, f"unknown skill: {name}")
    return {
        "name": skill.name,
        "description": skill.description,
        "audience": list(skill.audience),
        "triggers": list(skill.triggers),
        "constraints": list(skill.constraints),
        "inputs": skill.inputs,
        "outputs": skill.outputs,
        "body": skill.body,
        "read_only": skill.read_only,
        "path": str(skill.path),
    }
