"""Hive helpers — specialised LLM minds Hive can dispatch to.

Each helper has a role (planner, coder, researcher, …) and an output
schema (Pydantic). Concrete helpers live in sibling modules and are
registered via `build_helpers()` below.

The HiveCoordinator (M2.3) orchestrates dispatch + budget enforcement;
helpers themselves are simple async callables that return a
HelperResult.
"""

from __future__ import annotations

from gateway.helpers.base import (
    Helper, HelperResult, HelperTask, OllamaInvoker, ResultBuilder,
    SchemaValidationError,
)

__all__ = [
    "Helper",
    "HelperResult",
    "HelperTask",
    "OllamaInvoker",
    "ResultBuilder",
    "SchemaValidationError",
]
