"""Coder helper — write/debug/refactor code on Hive's behalf."""

from __future__ import annotations

from gateway.helpers.base import BaseHelper
from gateway.helpers.shapes import CodePlan


class CoderHelper(BaseHelper):
    role = "coder"

    def __init__(self, **kwargs) -> None:
        kwargs.setdefault("schema", CodePlan)
        super().__init__(**kwargs)
