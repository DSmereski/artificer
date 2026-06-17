"""Researcher helper — runs the M4.3 corroborated research pipeline.

Inputs:
  - topic: str
  - max_sources: int (optional; default 5)

Output: ResearchPlan with facts (≥2-source) + notes (single-source) +
sources + warning.
"""

from __future__ import annotations

import logging

from gateway.helpers.base import (
    BaseHelper, HelperResult, HelperTask, ResultBuilder,
)
from gateway.helpers.shapes import ResearchPlan
from gateway.research_pipeline import (
    ResearchDeps, ddg_search, research,
)
from gateway.safe_fetcher import safe_fetch

log = logging.getLogger("gateway.helpers.researcher")


class ResearcherHelper(BaseHelper):
    role = "researcher"

    def __init__(self, **kwargs) -> None:
        kwargs.setdefault("schema", ResearchPlan)
        super().__init__(**kwargs)

    async def invoke(self, task: HelperTask) -> HelperResult:
        rb = ResultBuilder(role=self.role, model_id=self.model_id,
                           parent_id=task.parent_id)

        topic = str(task.inputs.get("topic", "")).strip()
        if not topic:
            return rb.fail("missing 'topic' input").build()

        max_sources = int(task.inputs.get("max_sources", 5))
        max_sources = max(2, min(max_sources, 5))

        # LLM adapter for the pipeline.
        async def _llm(system: str, user: str, params):
            text, t_in, t_out = await self.invoker.chat(
                model=self.ollama_name,
                system=system, user=user,
                params=params or self.params,
                use_cpu=task.use_cpu,
            )
            rb.add_tokens(t_in, t_out)
            return text

        deps = ResearchDeps(
            search=lambda t, k: ddg_search(t, k=k),
            fetch=lambda u: safe_fetch(u),
            llm=_llm,
        )
        try:
            out = await research(topic, deps, max_sources=max_sources)
        except Exception as e:  # noqa: BLE001
            log.exception("research pipeline failed")
            return rb.fail(f"pipeline error: {type(e).__name__}: {e}").build()

        rb.output = {
            "summary": (
                f"Researched {topic!r}: {len(out.facts)} fact(s), "
                f"{len(out.notes)} single-source note(s), "
                f"{len(out.sources)} source(s)."
            ),
            "plan": ["search ddg", "safe-fetch sources",
                     "extract claims", "corroborate"],
            "facts": out.facts,
            "notes": out.notes,
            "warning": out.warning,
            "citations": [s["url"] for s in out.sources],
        }
        rb.plan = list(rb.output["plan"])
        rb.citations = list(rb.output["citations"])
        rb.confidence = "high" if out.facts else (
            "low" if out.warning else "medium"
        )
        return rb.build()
