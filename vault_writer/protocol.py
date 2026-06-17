"""Wire protocol for the vault-writer daemon.

Line-delimited JSON over TCP. One request per connection (for now).
Request payload:
  {"method": "<name>", "auth": "<token>", "params": {...}}

Request / response dataclasses are frozen. Size limits enforced at decode.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, is_dataclass

from vault_writer.util import MAX_BODY_CHARS, MAX_TITLE_CHARS


MAX_WIRE_BYTES = 128 * 1024   # 128 KiB per request line


@dataclass(frozen=True, slots=True)
class PingRequest:
    pass


@dataclass(frozen=True, slots=True)
class PingResponse:
    pong: bool
    daemon_version: str


@dataclass(frozen=True, slots=True)
class LearnRequest:
    category: str
    title: str
    body: str
    author: str
    audience: list[str] = field(default_factory=lambda: ["all"])
    tags: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)
    idempotency_key: str | None = None


@dataclass(frozen=True, slots=True)
class LearnResponse:
    ok: bool
    path: str
    created: bool
    deduped: bool = False       # true if request was a no-op duplicate


@dataclass(frozen=True, slots=True)
class ChatLogAppendRequest:
    bot: str
    user_id: int
    role: str
    content: str
    thread_id: str = "default"
    turn_id: str | None = None
    parent_id: int | None = None


@dataclass(frozen=True, slots=True)
class ChatLogAppendResponse:
    ok: bool
    id: int


@dataclass(frozen=True, slots=True)
class ThreadCreateRequest:
    thread_id: str
    bot: str
    user_id: int
    title: str | None = None
    parent_thread_id: str | None = None
    fork_point_turn_id: str | None = None


@dataclass(frozen=True, slots=True)
class ThreadCreateResponse:
    ok: bool
    thread_id: str
    created: bool = True   # False when thread_id already existed (idempotent no-op)


@dataclass(frozen=True, slots=True)
class ThreadArchiveRequest:
    thread_id: str


@dataclass(frozen=True, slots=True)
class ThreadArchiveResponse:
    ok: bool


@dataclass(frozen=True, slots=True)
class ThreadSetTitleRequest:
    thread_id: str
    title: str


@dataclass(frozen=True, slots=True)
class ThreadSetTitleResponse:
    ok: bool


@dataclass(frozen=True, slots=True)
class ThreadTouchRequest:
    thread_id: str


@dataclass(frozen=True, slots=True)
class ThreadTouchResponse:
    ok: bool


@dataclass(frozen=True, slots=True)
class ThreadForkRequest:
    new_thread_id: str
    source_thread_id: str
    bot: str
    user_id: int
    title: str | None = None
    fork_point_turn_id: str | None = None


@dataclass(frozen=True, slots=True)
class ThreadForkResponse:
    ok: bool
    thread_id: str
    rows_copied: int


@dataclass(frozen=True, slots=True)
class ThreadRenameRequest:
    thread_id: str
    title: str


@dataclass(frozen=True, slots=True)
class ThreadRenameResponse:
    ok: bool


@dataclass(frozen=True, slots=True)
class ThreadUnarchiveRequest:
    thread_id: str


@dataclass(frozen=True, slots=True)
class ThreadUnarchiveResponse:
    ok: bool


@dataclass(frozen=True, slots=True)
class ThreadPinRequest:
    thread_id: str
    pinned: bool = True


@dataclass(frozen=True, slots=True)
class ThreadPinResponse:
    ok: bool


@dataclass(frozen=True, slots=True)
class ChatPinRequest:
    turn_id: str
    bot: str
    user_id: int
    pinned: bool = True


@dataclass(frozen=True, slots=True)
class ChatPinResponse:
    ok: bool
    rows: int


@dataclass(frozen=True, slots=True)
class EntityPageUpdateRequest:
    slug: str
    kind: str
    title: str
    compiled_truth: str = ""
    timeline_entry: str = ""
    # Phase 3 (#456): graphify-shaped edge list. Each entry must be
    # {"target_slug": str, "label": str, "confidence": "EXTRACTED"
    # | "INFERRED" | "AMBIGUOUS"}. Default-empty so pre-Phase-3
    # callers keep working unchanged.
    relationships: tuple = ()


@dataclass(frozen=True, slots=True)
class EntityPageUpdateResponse:
    ok: bool
    prior_compiled_truth: str = ""
    prior_existed: bool = False


@dataclass(frozen=True, slots=True)
class ChatLogClearRequest:
    bot: str
    user_id: int


@dataclass(frozen=True, slots=True)
class ChatLogClearResponse:
    ok: bool
    deleted: int = 0


@dataclass(frozen=True, slots=True)
class ErrorResponse:
    error: str


Request = (
    PingRequest | LearnRequest | ChatLogAppendRequest
    | ChatLogClearRequest
    | ThreadCreateRequest | ThreadArchiveRequest | ThreadSetTitleRequest
    | ThreadTouchRequest | ThreadForkRequest
    | ThreadRenameRequest | ThreadUnarchiveRequest | ThreadPinRequest
    | ChatPinRequest
    | EntityPageUpdateRequest
)
Response = (
    PingResponse | LearnResponse | ChatLogAppendResponse
    | ChatLogClearResponse
    | ThreadCreateResponse | ThreadArchiveResponse | ThreadSetTitleResponse
    | ThreadTouchResponse | ThreadForkResponse
    | ThreadRenameResponse | ThreadUnarchiveResponse | ThreadPinResponse
    | ChatPinResponse
    | EntityPageUpdateResponse
    | ErrorResponse
)


_METHOD_TO_REQ: dict[str, type] = {
    "ping": PingRequest,
    "learn": LearnRequest,
    "chat_log_append": ChatLogAppendRequest,
    "chat_log_clear": ChatLogClearRequest,
    "thread_create": ThreadCreateRequest,
    "thread_archive": ThreadArchiveRequest,
    "thread_set_title": ThreadSetTitleRequest,
    "thread_touch": ThreadTouchRequest,
    "thread_fork": ThreadForkRequest,
    "thread_rename": ThreadRenameRequest,
    "thread_unarchive": ThreadUnarchiveRequest,
    "thread_pin": ThreadPinRequest,
    "chat_pin": ChatPinRequest,
    "entity_page_update": EntityPageUpdateRequest,
}


class AuthRequired(Exception):
    """Raised when a request lacks or carries an invalid auth token."""


def decode_request(wire: bytes, expected_token: str | None) -> Request:
    """Parse a single line of wire bytes into a typed Request.

    Enforces size limits and auth. Raises ValueError on malformed input,
    AuthRequired on auth failures.
    """
    if len(wire) > MAX_WIRE_BYTES:
        raise ValueError(f"request too large ({len(wire)} > {MAX_WIRE_BYTES})")

    try:
        obj = json.loads(wire.decode("utf-8").strip())
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ValueError(f"malformed request: {e}") from e
    if not isinstance(obj, dict):
        raise ValueError("malformed request: expected object")

    method = obj.get("method")
    if not isinstance(method, str) or method not in _METHOD_TO_REQ:
        raise ValueError(f"unknown method: {method!r}")

    # Auth is required for write methods. Ping is always open (health check).
    if method != "ping" and expected_token:
        supplied = obj.get("auth")
        if not isinstance(supplied, str) or supplied != expected_token:
            raise AuthRequired("missing or invalid auth token")

    params = obj.get("params") or {}
    if not isinstance(params, dict):
        raise ValueError("malformed request: params must be an object")

    cls = _METHOD_TO_REQ[method]
    # Phase 3 (#456): normalise relationships to a list before constructing
    # EntityPageUpdateRequest so the field is always a list (not tuple)
    # and the default-absent case returns [] not ().
    if cls is EntityPageUpdateRequest and "relationships" not in params:
        params = {**params, "relationships": []}
    elif cls is EntityPageUpdateRequest and "relationships" in params:
        params = {**params, "relationships": list(params["relationships"])}
    try:
        req = cls(**params)
    except TypeError as e:
        raise ValueError(f"malformed request params for {method}: {e}") from e

    # Per-request field limits.
    if isinstance(req, LearnRequest):
        if len(req.title) > MAX_TITLE_CHARS:
            raise ValueError(f"title too long ({len(req.title)} > {MAX_TITLE_CHARS})")
        if len(req.body) > MAX_BODY_CHARS:
            raise ValueError(f"body too long ({len(req.body)} > {MAX_BODY_CHARS})")
        if req.author == "human":
            raise ValueError("author='human' is reserved; learn requests cannot claim it")
    elif isinstance(req, ChatLogAppendRequest):
        if len(req.content) > MAX_BODY_CHARS:
            raise ValueError(
                f"chat content too long ({len(req.content)} > {MAX_BODY_CHARS})"
            )
        if req.role not in ("user", "assistant"):
            raise ValueError(f"invalid role: {req.role!r}")
    elif isinstance(req, (ThreadCreateRequest, ThreadForkRequest)):
        if req.title is not None and len(req.title) > MAX_TITLE_CHARS:
            raise ValueError(
                f"thread title too long ({len(req.title)} > {MAX_TITLE_CHARS})"
            )
    elif isinstance(req, (ThreadSetTitleRequest, ThreadRenameRequest)):
        if len(req.title) > MAX_TITLE_CHARS:
            raise ValueError(
                f"thread title too long ({len(req.title)} > {MAX_TITLE_CHARS})"
            )
    elif isinstance(req, EntityPageUpdateRequest):
        if len(req.title) > MAX_TITLE_CHARS:
            raise ValueError(
                f"entity title too long ({len(req.title)} > {MAX_TITLE_CHARS})"
            )
        if len(req.compiled_truth) > MAX_BODY_CHARS:
            raise ValueError(
                f"compiled_truth too long ({len(req.compiled_truth)} > {MAX_BODY_CHARS})"
            )
        if len(req.timeline_entry) > MAX_BODY_CHARS:
            raise ValueError(
                f"timeline_entry too long ({len(req.timeline_entry)} > {MAX_BODY_CHARS})"
            )
        # Phase 3 (#456): validate the graphify-shaped relationships
        # list. Cap edge count at 32 — far above what a single turn
        # would legitimately emit, well below "abusive payload."
        rels = req.relationships
        if not isinstance(rels, (list, tuple)):
            raise ValueError("relationships must be a list")
        if len(rels) > 32:
            raise ValueError(
                f"too many relationships ({len(rels)} > 32)"
            )
        _ALLOWED_CONFIDENCE = ("EXTRACTED", "INFERRED", "AMBIGUOUS")
        for edge in rels:
            if not isinstance(edge, dict):
                raise ValueError("relationships entry must be an object")
            tgt = edge.get("target_slug")
            label = edge.get("label")
            conf = edge.get("confidence")
            if not isinstance(tgt, str) or not tgt:
                raise ValueError("edge missing target_slug")
            if not isinstance(label, str) or not label:
                raise ValueError("edge missing label")
            if conf not in _ALLOWED_CONFIDENCE:
                raise ValueError(
                    f"edge confidence must be one of {_ALLOWED_CONFIDENCE}, "
                    f"got {conf!r}"
                )
            if len(tgt) > 80 or len(label) > 80:
                raise ValueError("edge slug/label > 80 chars")

    return req


def encode_response(resp: Response) -> bytes:
    if not is_dataclass(resp):
        raise TypeError(f"not a response dataclass: {resp!r}")
    return (json.dumps(asdict(resp)) + "\n").encode("utf-8")


__all__ = [
    "PingRequest", "PingResponse",
    "LearnRequest", "LearnResponse",
    "ChatLogAppendRequest", "ChatLogAppendResponse",
    "ChatLogClearRequest", "ChatLogClearResponse",
    "ThreadCreateRequest", "ThreadCreateResponse",
    "ThreadArchiveRequest", "ThreadArchiveResponse",
    "ThreadSetTitleRequest", "ThreadSetTitleResponse",
    "ThreadTouchRequest", "ThreadTouchResponse",
    "ThreadForkRequest", "ThreadForkResponse",
    "ThreadRenameRequest", "ThreadRenameResponse",
    "ThreadUnarchiveRequest", "ThreadUnarchiveResponse",
    "ThreadPinRequest", "ThreadPinResponse",
    "ChatPinRequest", "ChatPinResponse",
    "EntityPageUpdateRequest", "EntityPageUpdateResponse",
    "ErrorResponse",
    "Request", "Response",
    "AuthRequired",
    "MAX_WIRE_BYTES",
    "decode_request", "encode_response",
]
