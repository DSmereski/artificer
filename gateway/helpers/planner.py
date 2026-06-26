"""Planner helper — Hive's first thought every turn.

Reads the user message + conversation context + image-build state
(M5.1) + skill catalogue (M3) and decides:
  - direct reply (no delegation needed), OR
  - which helpers to dispatch and what each should produce
The output is a HelperPlan (see helpers.shapes).
"""

from __future__ import annotations

from gateway.helpers.base import BaseHelper, HelperTask, ResultBuilder
from gateway.helpers.shapes import HelperPlan


class PlannerHelper(BaseHelper):
    role = "planner"

    def __init__(self, **kwargs) -> None:
        kwargs.setdefault("schema", HelperPlan)
        super().__init__(**kwargs)

    def _post_parse(self, task: HelperTask, rb: ResultBuilder) -> None:
        super()._post_parse(task, rb)
        # Mirror summary into plan if plan is empty.
        if not rb.plan:
            summary = rb.output.get("summary")
            if isinstance(summary, str) and summary:
                rb.plan = [summary]
