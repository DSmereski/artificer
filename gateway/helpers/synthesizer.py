"""Synthesizer helper — Hive's voice.

Takes the planner's plan + every helper's result and produces:
  - the final user-facing reply (in Hive's voice)
  - the list of side-effect actions (vault writes, image renders, etc.)
"""

from __future__ import annotations

import re
from typing import Any

from gateway.helpers.base import (
    BaseHelper,
    SchemaValidationError,
    _THINK_BLOCK,
    _THINK_BLOCK_OPEN,
)
from gateway.helpers.shapes import SynthesisPlan


_MIN_PROSE_CHARS = 4

# Matches one or more trailing JSON-object blocks at the end of text.
# Conservative: only fires when `{...}` appears at the very end (after
# optional whitespace).  Prose-internal braces (mid-sentence) are NOT
# matched because the pattern is anchored to end-of-string ($).
# re.DOTALL lets `.` cross newlines inside the block.
_TRAILING_JSON = re.compile(r"(\s*\{.*\}\s*)+$", re.DOTALL)


class SynthesizerHelper(BaseHelper):
    role = "synthesizer"

    def __init__(self, **kwargs) -> None:
        kwargs.setdefault("schema", SynthesisPlan)
        super().__init__(**kwargs)

    def _parse_fallback(
        self, text: str, error: SchemaValidationError,
    ) -> dict[str, Any] | None:
        """Wrap prose-only LLM output as a SynthesisPlan.

        planner-qwen occasionally produces a substantive answer in plain
        prose without the JSON envelope. Rather than dropping the user's
        answer on the floor and falling back to a generic "helper outputs
        below" stub, we treat the prose as the final reply.

        Skipped for empty / whitespace-only / pure-reasoning replies —
        those still surface as errors so a real bug isn't masked.
        """
        stripped = _strip_think(text).strip()
        # Strip any trailing JSON object block(s) that the LLM appended
        # after its prose answer (e.g. `{"actions": []}`).  Only removed
        # when the block sits at the very end of the string; prose-internal
        # braces are left untouched.
        prose = _TRAILING_JSON.sub("", stripped).strip()
        if len(prose) < _MIN_PROSE_CHARS:
            # Nothing usable left after stripping — fall through to error.
            return None
        return {"reply": prose, "actions": []}


def _strip_think(text: str) -> str:
    """Remove qwen3 <think>...</think> reasoning blocks.

    Mirrors the closed/open precedence used by extract_json so a
    response like '<think>...</think>real answer' lands as just
    'real answer' in the user-visible reply.
    """
    closed = _THINK_BLOCK.sub("", text)
    if closed != text:
        return closed
    if "<think>" in text.lower():
        return _THINK_BLOCK_OPEN.sub("", text)
    return text
