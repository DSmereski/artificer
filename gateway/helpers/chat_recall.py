"""Chat-recall helper — FTS5 lookup over the chat_log table.

The "recall" memory tier in the Letta-shaped three-tier model: anything
older than the verbatim rolling buffer (200 messages) but younger than
the long-digest summary lives here. The planner delegates to this
helper when the user asks about something earlier in the conversation
(`"what did we say about X"`, `"remember when we discussed Y"`).

Bypasses the LLM. Hits SQLite directly via `VaultClient.search_chat`,
so it's cheap (~5ms) — much faster than any helper that has to round-
trip Ollama.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from gateway.helpers.base import HelperResult, HelperTask, ResultBuilder
from gateway.helpers.typed_tool import TypedHelper


log = logging.getLogger("gateway.helpers.chat_recall")


_DEFAULT_LIMIT = 8
_MAX_BODY_CHARS = 600   # truncate per-hit so the synth prompt stays bounded


class ChatRecallInputs(BaseModel):
    """Typed shape for chat_recall task inputs.

    Every field is optional because the planner sometimes calls this
    helper with only a `goal` and lets the helper extract a query from
    that. Validation here protects against type drift (planner sending
    a list under `query`, or a string under `user_id`) — not missing
    fields.
    """
    model_config = {"extra": "allow"}

    query: str | None = None
    topic: str | None = None
    question: str | None = None
    subject: str | None = None
    bot: str | None = None
    user_id: int | str | None = None
    thread_id: str | None = None


class ChatRecallHelper(TypedHelper):
    """Direct chat_log search — no LLM call. Mirrors librarian's
    short-circuit pattern when there are zero candidates."""

    role = "chat_recall"
    Inputs: ClassVar[type[BaseModel] | None] = ChatRecallInputs

    def __init__(self, **kwargs) -> None:
        # No schema or prompt needed — we override `invoke` and never
        # touch the LLM. Accept the factory's standard kwargs so
        # registration is uniform.
        self._vault_client_factory = kwargs.pop("vault_client_factory", None)
        super().__init__(**kwargs)

    async def invoke(self, task: HelperTask) -> HelperResult:
        rb = ResultBuilder(
            role=self.role, model_id=self.model_id,
            parent_id=task.parent_id,
        )
        parsed = self.parse_inputs(task)
        if isinstance(parsed, str):
            # Validation failed — surface a typed error rather than
            # silently behaving as if no inputs were supplied.
            rb.output = {"hits": [], "summary": parsed}
            rb.confidence = "low"
            rb.error = parsed
            return rb.build()
        inputs = parsed if isinstance(parsed, ChatRecallInputs) else None

        query = self._extract_query(task, inputs)
        bot = self._extract_bot(inputs)
        user_id = self._extract_user_id(inputs)
        thread_id = inputs.thread_id if inputs else None

        if not query:
            rb.output = {"hits": [], "summary": "no query supplied"}
            rb.confidence = "low"
            return rb.build()

        if self._vault_client_factory is None:
            rb.output = {"hits": [], "summary": "chat_log not configured (no vault client)"}
            rb.confidence = "low"
            return rb.build()
        if user_id is None:
            rb.output = {"hits": [], "summary": "chat_log lookup skipped (no user_id in inputs)"}
            rb.confidence = "low"
            return rb.build()

        try:
            client = self._vault_client_factory()
            rows = client.search_chat(
                bot=bot, user_id=user_id, query_text=query,
                limit=_DEFAULT_LIMIT, thread_id=thread_id,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("chat_recall search failed: %s", e)
            rb.output = {"hits": [], "summary": f"search error: {e}"}
            rb.confidence = "low"
            return rb.build()

        hits: list[dict[str, Any]] = []
        for r in rows:
            content = (r.get("content") or "").strip()
            if len(content) > _MAX_BODY_CHARS:
                content = content[:_MAX_BODY_CHARS] + "..."
            hits.append({
                "role": r.get("role"),
                "content": content,
                "thread_id": r.get("thread_id"),
                "created_at": r.get("created_at"),
            })

        rb.output = {
            "hits": hits,
            "summary": (
                f"{len(hits)} chat-log hits for {query!r}"
                if hits else f"no chat-log hits for {query!r}"
            ),
        }
        rb.confidence = "high" if hits else "low"
        return rb.build()

    # ---------------------------------------------------------------- extract

    @staticmethod
    def _extract_query(task: HelperTask, inputs: ChatRecallInputs | None) -> str:
        if inputs is not None:
            for v in (inputs.query, inputs.topic, inputs.question, inputs.subject):
                if isinstance(v, str) and v.strip():
                    return v.strip()
        return (task.goal or "").strip()

    @staticmethod
    def _extract_bot(inputs: ChatRecallInputs | None) -> str:
        if inputs is not None and isinstance(inputs.bot, str) and inputs.bot.strip():
            return inputs.bot.strip()
        return "terry"

    @staticmethod
    def _extract_user_id(inputs: ChatRecallInputs | None) -> int | None:
        if inputs is None:
            return None
        v = inputs.user_id
        if isinstance(v, int) and not isinstance(v, bool):
            return v
        if isinstance(v, str) and v.isdigit():
            return int(v)
        return None
