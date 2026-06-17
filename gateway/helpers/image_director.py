"""Image Director helper — turns the ImageBuildState + user msg into a
finished image generation payload.

Inputs (from the planner):
  - build: ImageBuildState as a dict (subject, aspect, loras, mood, …)
  - user_msg: the latest user message
  - available_loras: list of LoRA names the catalog allows

Output: ImagePlan — the actual prompt + aspect + loras + count the
synthesizer can hand to the image_render action verb.
"""

from __future__ import annotations

import json
import logging

from gateway.helpers.base import (
    BaseHelper, HelperResult, HelperTask, ResultBuilder,
    SchemaValidationError, load_prompt, parse_with_schema,
)
from gateway.helpers.shapes import ImagePlan

log = logging.getLogger("gateway.helpers.image_director")


_VALID_ASPECTS = {"portrait", "landscape", "square", "ultrawide"}


class ImageDirectorHelper(BaseHelper):
    role = "image_director"

    def __init__(self, **kwargs) -> None:
        kwargs.setdefault("schema", ImagePlan)
        super().__init__(**kwargs)

    async def invoke(self, task: HelperTask) -> HelperResult:
        rb = ResultBuilder(role=self.role, model_id=self.model_id,
                           parent_id=task.parent_id)

        build = task.inputs.get("build") or {}
        user_msg = str(task.inputs.get("user_msg", "")).strip()
        available_loras = list(task.inputs.get("available_loras") or [])
        if not isinstance(build, dict):
            return rb.fail("'build' input must be a dict").build()

        # Compose the user message with all context.
        try:
            system = load_prompt(self.prompt_name)
        except FileNotFoundError as e:
            return rb.fail(str(e)).build()
        payload = {
            "goal": task.goal,
            "inputs": {
                "build": build,
                "user_msg": user_msg,
                "available_loras": available_loras[:200],
            },
        }
        user = json.dumps(payload, indent=2, default=str)

        import asyncio
        try:
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
                f"image_director timed out after {self.timeout_s}s",
            ).build()
        except Exception as e:  # noqa: BLE001
            log.exception("image_director unexpected")
            return rb.fail(f"unexpected: {type(e).__name__}: {e}").build()
        rb.add_tokens(t_in, t_out)
        rb.raw_text = text

        # Parse + sanitize. Fall back to the build state directly if
        # the LLM emits something we can't validate.
        try:
            parsed = parse_with_schema(text, ImagePlan)
            rb.output = parsed.model_dump()
        except SchemaValidationError:
            rb.output = self._fallback_plan(build, user_msg)
            rb.confidence = "low"

        # Drop unknown LoRAs.
        loras = list(rb.output.get("loras") or [])
        if available_loras:
            allowed = set(available_loras)
            loras = [l for l in loras if l in allowed]
        rb.output["loras"] = loras

        # Validate aspect against the build state's aspect.
        aspect = str(rb.output.get("aspect", "")).lower()
        if aspect not in _VALID_ASPECTS:
            aspect = build.get("aspect") if isinstance(build.get("aspect"), str) else "portrait"
            rb.output["aspect"] = aspect

        # Make sure prompt is non-empty; fall back to subject if missing.
        if not str(rb.output.get("prompt") or "").strip():
            rb.output["prompt"] = (
                f"a {build.get('mood', 'cinematic')} portrait of "
                f"{build.get('subject') or 'the subject'}"
            )
        return rb.build()

    @staticmethod
    def _fallback_plan(build: dict, user_msg: str) -> dict:
        subject = build.get("subject") or "the subject"
        mood = build.get("mood") or "cinematic"
        return {
            "summary": f"rendering {subject} ({build.get('aspect', 'portrait')})",
            "prompt": f"a {mood} portrait of {subject}",
            "negative_prompt": (
                build.get("negative") or
                "blurry, low quality, deformed, watermark"
            ),
            "aspect": build.get("aspect") or "portrait",
            "loras": list(build.get("style_loras") or []),
            "count": int(build.get("count", 1)) or 1,
            "plan": ["read build state", "compose prompt (LLM JSON failed)"],
        }
