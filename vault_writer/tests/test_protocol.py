"""Tests for vault_writer.protocol."""

from __future__ import annotations

import json

import pytest

from vault_writer.protocol import (
    AuthRequired,
    LearnRequest,
    PingRequest,
    PingResponse,
    decode_request,
    encode_response,
)
from vault_writer.util import MAX_BODY_CHARS, MAX_TITLE_CHARS


def test_ping_request_roundtrip() -> None:
    wire = b'{"method": "ping", "params": {}}\n'
    req = decode_request(wire, expected_token=None)
    assert isinstance(req, PingRequest)


def test_ping_response_encodes_with_trailing_newline() -> None:
    resp = PingResponse(pong=True, daemon_version="0.1.0")
    wire = encode_response(resp)
    assert wire.endswith(b"\n")
    assert b'"pong": true' in wire
    assert b'"daemon_version": "0.1.0"' in wire


def test_decode_unknown_method_raises() -> None:
    with pytest.raises(ValueError, match="unknown method"):
        decode_request(
            b'{"method": "launch_missiles", "params": {}}\n',
            expected_token=None,
        )


def test_decode_malformed_json_raises() -> None:
    with pytest.raises(ValueError, match="malformed"):
        decode_request(b'not json\n', expected_token=None)


def test_ping_does_not_require_auth_even_when_configured() -> None:
    wire = b'{"method": "ping", "params": {}}\n'
    req = decode_request(wire, expected_token="s3cret")
    assert isinstance(req, PingRequest)


def test_learn_without_auth_raises_auth_required() -> None:
    payload = {
        "method": "learn",
        "params": {
            "category": "knowledge", "title": "x", "body": "y",
            "author": "claude-code",
        },
    }
    with pytest.raises(AuthRequired):
        decode_request(json.dumps(payload).encode() + b"\n", expected_token="s3cret")


def test_learn_with_wrong_auth_raises_auth_required() -> None:
    payload = {
        "method": "learn",
        "auth": "wrong",
        "params": {
            "category": "knowledge", "title": "x", "body": "y",
            "author": "claude-code",
        },
    }
    with pytest.raises(AuthRequired):
        decode_request(json.dumps(payload).encode() + b"\n", expected_token="s3cret")


def test_learn_with_correct_auth_decodes() -> None:
    payload = {
        "method": "learn",
        "auth": "s3cret",
        "params": {
            "category": "knowledge", "title": "x", "body": "y",
            "author": "claude-code",
        },
    }
    req = decode_request(json.dumps(payload).encode() + b"\n", expected_token="s3cret")
    assert isinstance(req, LearnRequest)
    assert req.author == "claude-code"


def test_learn_with_human_author_rejected() -> None:
    payload = {
        "method": "learn",
        "params": {
            "category": "knowledge", "title": "x", "body": "y",
            "author": "human",
        },
    }
    with pytest.raises(ValueError, match="human"):
        decode_request(json.dumps(payload).encode() + b"\n", expected_token=None)


def test_learn_with_oversized_body_rejected() -> None:
    payload = {
        "method": "learn",
        "params": {
            "category": "knowledge", "title": "x",
            "body": "A" * (MAX_BODY_CHARS + 1),
            "author": "claude-code",
        },
    }
    with pytest.raises(ValueError, match="body too long"):
        decode_request(json.dumps(payload).encode() + b"\n", expected_token=None)


def test_learn_with_oversized_title_rejected() -> None:
    payload = {
        "method": "learn",
        "params": {
            "category": "knowledge",
            "title": "A" * (MAX_TITLE_CHARS + 1),
            "body": "y",
            "author": "claude-code",
        },
    }
    with pytest.raises(ValueError, match="title too long"):
        decode_request(json.dumps(payload).encode() + b"\n", expected_token=None)


# ---- Finding 5: chat_log_clear RPC ----------------------------------------


def test_chat_log_clear_request_decodes() -> None:
    """Finding 5: chat_log_clear must be a valid protocol method so the
    daemon can receive the request from MemoryStore.reset."""
    from vault_writer.protocol import ChatLogClearRequest
    payload = {
        "method": "chat_log_clear",
        "params": {"bot": "terry", "user_id": 42},
    }
    req = decode_request(json.dumps(payload).encode() + b"\n", expected_token=None)
    assert isinstance(req, ChatLogClearRequest)
    assert req.bot == "terry"
    assert req.user_id == 42


def test_chat_log_clear_response_encodes() -> None:
    """ChatLogClearResponse must serialise cleanly to wire format."""
    from vault_writer.protocol import ChatLogClearResponse
    resp = ChatLogClearResponse(ok=True, deleted=3)
    wire = encode_response(resp)
    obj = json.loads(wire.decode())
    assert obj["ok"] is True
    assert obj["deleted"] == 3


def test_entity_page_update_request_accepts_relationships() -> None:
    """The wire format carries relationships through unchanged."""
    from vault_writer.protocol import decode_request
    body = json.dumps({
        "method": "entity_page_update",
        "params": {
            "slug": "kraken", "kind": "thing", "title": "Kraken",
            "compiled_truth": "x", "timeline_entry": "t",
            "relationships": [
                {"target_slug": "drake", "label": "made_by",
                 "confidence": "EXTRACTED"}
            ],
        },
    }).encode("utf-8")
    req = decode_request(body, expected_token=None)
    assert req.relationships == [
        {"target_slug": "drake", "label": "made_by",
         "confidence": "EXTRACTED"}
    ]


def test_entity_page_update_request_relationships_default_empty() -> None:
    """Older callers without the field still decode."""
    from vault_writer.protocol import decode_request
    body = json.dumps({
        "method": "entity_page_update",
        "params": {
            "slug": "kraken", "kind": "thing", "title": "Kraken",
        },
    }).encode("utf-8")
    req = decode_request(body, expected_token=None)
    assert req.relationships == []


def test_entity_page_update_request_rejects_bad_confidence() -> None:
    """The validator rejects unknown confidence labels — only
    EXTRACTED, INFERRED, AMBIGUOUS allowed (graphify shape)."""
    import pytest
    from vault_writer.protocol import decode_request
    body = json.dumps({
        "method": "entity_page_update",
        "params": {
            "slug": "kraken", "kind": "thing", "title": "Kraken",
            "relationships": [
                {"target_slug": "drake", "label": "made_by",
                 "confidence": "MAYBE"}
            ],
        },
    }).encode("utf-8")
    with pytest.raises(ValueError, match="confidence"):
        decode_request(body, expected_token=None)


def test_entity_page_update_request_rejects_too_many_edges() -> None:
    """A runaway turn must not push 10000 edges through. Cap at 32."""
    import pytest
    from vault_writer.protocol import decode_request
    body = json.dumps({
        "method": "entity_page_update",
        "params": {
            "slug": "kraken", "kind": "thing", "title": "Kraken",
            "relationships": [
                {"target_slug": f"t{i}", "label": "rel",
                 "confidence": "INFERRED"}
                for i in range(33)
            ],
        },
    }).encode("utf-8")
    with pytest.raises(ValueError, match="relationships"):
        decode_request(body, expected_token=None)
