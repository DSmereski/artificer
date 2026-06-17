"""Pydantic-typed helper inputs (Phase D.1 proof-of-concept).

The legacy `BaseHelper` shape lets each helper rummage through
`task.inputs: dict[str, Any]` ad-hoc with `.get(key)` and isinstance
guards. That works, but inputs drift silently — a planner upgrade that
renames a key from `query` to `question` shows up as a behavior bug,
not a validation failure.

`TypedHelper` adds an `Inputs: type[BaseModel]` class attribute. The
helper calls `self.parse_inputs(task)` at the top of `invoke`; on
validation failure it gets a typed error string back and can decide
whether to short-circuit (return a graceful "missing inputs" result)
or fail loudly. Either way the structural drift surfaces immediately
in the turn log instead of corrupting downstream output.

The Outputs side stays optional — most helpers already validate
their LLM reply against a Pydantic schema via `BaseHelper.schema`
(see `synthesizer.py`, `planner.py`). TypedHelper exists for the
input side so non-LLM helpers (chat_recall, librarian) still get a
typed contract.

Migration target for this proof: `chat_recall.py`. The other helpers
follow once this shape proves itself in production.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ValidationError

from gateway.helpers.base import BaseHelper, HelperTask


class TypedHelper(BaseHelper):
    """BaseHelper extension that validates `task.inputs` against a
    declared Pydantic model before the helper runs.

    Subclasses set `Inputs` to a `type[BaseModel]`. `parse_inputs`
    returns either the validated model instance or a one-line error
    string the helper can surface in its `HelperResult`.

    Subclasses that don't declare `Inputs` behave exactly like
    `BaseHelper` — `parse_inputs` returns `None` and the legacy
    dict-access path keeps working.
    """

    Inputs: ClassVar[type[BaseModel] | None] = None

    def parse_inputs(self, task: HelperTask) -> BaseModel | str | None:
        """Validate `task.inputs` against `self.Inputs`.

        Returns:
          - validated `BaseModel` instance on success
          - one-line error string on validation failure
          - `None` when the helper hasn't declared `Inputs` (legacy)
        """
        cls = type(self).Inputs
        if cls is None:
            return None
        try:
            return cls.model_validate(task.inputs)
        except ValidationError as e:
            # Shape: "field 'x': msg; field 'y': msg" — terse enough to
            # ride along inside HelperResult.error.
            parts = [
                f"field {'.'.join(str(p) for p in err['loc'])!r}: {err['msg']}"
                for err in e.errors()
            ]
            return "input validation failed: " + "; ".join(parts)
