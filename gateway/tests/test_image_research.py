"""Unit tests for gateway.image_research (sync parts only — embed call is mocked).

The full async path goes out to Ollama for embeddings + sqlite-vec; we
test the inputs/outputs of the deterministic logic. End-to-end behaviour
is exercised by the integration smoke tests at gateway boot.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from gateway.image_research import (
    _format_block,
    _read_people_note,
    _slugify_user,
    gather_chat_context,
    looks_like_image_request,
)


# ---------------------------------------------------------------- looks_like_image_request


@pytest.mark.parametrize("text", [
    "draw me a dragon",
    "make a picture of a cat",
    "render Sylvanas",
    "show me what Maggy looks like",
    "I want a portrait",
    "let me see your selfie",
    "paint a sunset",
    "illustrate this scene",
])
def test_image_request_positive(text: str):
    assert looks_like_image_request(text) is True


@pytest.mark.parametrize("text", [
    "what's the weather",
    "explain how flux works",
    "",
    "tell me a joke",
])
def test_image_request_negative(text: str):
    assert looks_like_image_request(text) is False


# ---------------------------------------------------------------- slugify


def test_slugify_basic():
    assert _slugify_user("Penguin") == "penguin"
    assert _slugify_user("Operator Account") == "operator-account"
    assert _slugify_user("user@example.com") == "user-example.com"


def test_slugify_empty():
    assert _slugify_user("") == "unknown"
    assert _slugify_user(None) == "unknown"  # type: ignore[arg-type]


# ---------------------------------------------------------------- people note read


def test_read_people_note_finds_match(tmp_path: Path):
    people = tmp_path / "people"
    people.mkdir()
    (people / "penguin.md").write_text(
        "---\naudience: [terry]\n---\n\n# Penguin\n\nGreen eyes.",
        encoding="utf-8",
    )
    out = _read_people_note(tmp_path, "Penguin", "terry")
    assert out is not None
    rel, body = out
    assert rel == "people/penguin.md"
    assert "Green eyes." in body


def test_read_people_note_audience_filtered(tmp_path: Path):
    people = tmp_path / "people"
    people.mkdir()
    (people / "boss.md").write_text(
        "---\naudience: [maggy]\n---\n\n# Boss",
        encoding="utf-8",
    )
    # terry shouldn't see a maggy-only note
    assert _read_people_note(tmp_path, "Boss", "terry") is None


def test_read_people_note_missing(tmp_path: Path):
    (tmp_path / "people").mkdir()
    assert _read_people_note(tmp_path, "ghost", "terry") is None


# ---------------------------------------------------------------- _format_block


def test_format_block_empty():
    assert _format_block([], max_chars=1000) == ""


def test_format_block_wraps_in_untrusted_marker():
    out = _format_block([("path/note.md", "body text")], max_chars=1000)
    assert "BEGIN UNTRUSTED" in out
    assert "END UNTRUSTED" in out
    assert "<!-- path/note.md -->" in out
    assert "body text" in out


def test_format_block_respects_max_chars():
    huge = "x" * 5000
    out = _format_block([("a.md", huge), ("b.md", huge)], max_chars=2000)
    assert len(out) <= 3000  # untrusted wrapper adds some prefix/suffix bytes


# ---------------------------------------------------------------- gather (image-cue gating)


def test_gather_skips_trivial_chitchat(tmp_path: Path):
    # Greetings / yes-no / single tokens should bail before we spend an
    # embedding call. "hi" is the canonical example.
    out = asyncio.run(
        gather_chat_context(
            user_text="hi",
            user_name="penguin",
            vault_path=tmp_path,
            daemon_host="127.0.0.1",
            daemon_port=8765,
        ),
    )
    assert out == ""


def test_gather_image_only_mode_skips_text_questions(tmp_path: Path):
    # Legacy explicit-image mode: a non-image-cue question should bail.
    out = asyncio.run(
        gather_chat_context(
            user_text="what is a Drake?",
            user_name="penguin",
            vault_path=tmp_path,
            daemon_host="127.0.0.1",
            daemon_port=8765,
            require_image_cue=True,
        ),
    )
    assert out == ""


def test_gather_includes_user_note_even_without_embedding(tmp_path: Path):
    # When ollama embed fails (no daemon), gather should still surface the
    # per-user people/<user>.md note since that's a direct file read.
    people = tmp_path / "people"
    people.mkdir()
    (people / "penguin.md").write_text(
        "---\naudience: [terry]\n---\n\n# Penguin appearance\n\nGreen eyes.",
        encoding="utf-8",
    )
    out = asyncio.run(
        gather_chat_context(
            user_text="draw me",
            user_name="Penguin",
            vault_path=tmp_path,
            daemon_host="127.0.0.1",
            daemon_port=1,  # bogus port — embed call will fail; that's fine
        ),
    )
    assert "Green eyes." in out
