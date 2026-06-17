"""Skill Runner helper — execute a vault skill.

Loads the skill body from the SkillRegistry and runs the LLM with the
body appended to the system prompt as the procedure spec.
"""

from __future__ import annotations

import logging

from gateway.helpers.base import (
    BaseHelper, HelperResult, HelperTask, ResultBuilder, load_prompt,
)
from gateway.helpers.shapes import SkillResult

log = logging.getLogger("gateway.helpers.skill_runner")


class SkillRunnerHelper(BaseHelper):
    role = "skill_runner"

    def __init__(self, registry=None, **kwargs) -> None:
        kwargs.setdefault("schema", SkillResult)
        super().__init__(**kwargs)
        # Optional registry — when set, load the skill body and append
        # to the system prompt at invoke time. When None (M2.2 default),
        # the body must be passed via task.inputs["body"].
        self._registry = registry

    async def invoke(self, task: HelperTask) -> HelperResult:
        rb = ResultBuilder(role=self.role, model_id=self.model_id,
                           parent_id=task.parent_id)

        skill_name = task.inputs.get("skill")
        body = task.inputs.get("body")
        constraints = list(task.constraints)

        if self._registry is not None and skill_name:
            skill = self._registry.get(str(skill_name))
            if skill is None:
                return rb.fail(f"unknown skill: {skill_name!r}").build()
            body = skill.body
            constraints = list(constraints) + list(skill.constraints)
            rb.citations.append(str(skill.path))

        if not body:
            return rb.fail(
                "skill_runner needs either inputs.body or a registry+name"
            ).build()

        # Build system prompt: the runner's framing + the skill body.
        try:
            system = load_prompt(self.prompt_name) + "\n\n" + body
        except FileNotFoundError as e:
            return rb.fail(str(e)).build()

        # Quarantined user message: only the skill's inputs + constraints.
        import json
        user = json.dumps({
            "skill": skill_name,
            "inputs": {k: v for k, v in task.inputs.items()
                       if k not in ("skill", "body")},
            "constraints": constraints,
        }, indent=2, default=str)

        # Call the LLM via the base class's invoker, but with our
        # own system prompt (skipping load_prompt in BaseHelper).
        try:
            import asyncio
            text, t_in, t_out = await asyncio.wait_for(
                self.invoker.chat(
                    model=self.ollama_name,
                    system=system, user=user,
                    params=self.params, use_cpu=task.use_cpu,
                ),
                timeout=self.timeout_s,
            )
        except asyncio.TimeoutError:
            return rb.fail(
                f"skill_runner timed out after {self.timeout_s}s",
            ).build()
        except Exception as e:  # noqa: BLE001
            log.exception("skill_runner unexpected error")
            return rb.fail(f"unexpected: {type(e).__name__}: {e}").build()
        rb.add_tokens(t_in, t_out)
        rb.raw_text = text

        if self.schema is None:
            rb.output = {"text": text}
        else:
            from gateway.helpers.base import (
                SchemaValidationError, parse_with_schema,
            )
            try:
                parsed = parse_with_schema(text, self.schema)
                rb.output = parsed.model_dump()
            except SchemaValidationError as e:
                return rb.fail(str(e)).build()

        plan = rb.output.get("plan")
        if isinstance(plan, list):
            rb.plan = [str(s) for s in plan]
        cites = rb.output.get("citations")
        if isinstance(cites, list):
            rb.citations.extend(str(c) for c in cites)
        return rb.build()
