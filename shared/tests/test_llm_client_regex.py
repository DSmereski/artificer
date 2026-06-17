"""Tests for shared/llm_client.py regex patterns.

Covers the _CHAT_TEMPLATE_LEAK regex so we never regress on the greedy
DOTALL truncation bug that dropped mid-sentence "system"/"user"/"assistant"
words.
"""

from __future__ import annotations

import re

import pytest

# Import the compiled pattern directly so this test stays independent of
# Ollama / torch at collection time.
from shared.llm_client import _CHAT_TEMPLATE_LEAK


# ---------------------------------------------------------------------------
# Helper — mirrors the truncation logic in _call_llm
# ---------------------------------------------------------------------------

def _apply_leak_filter(text: str) -> str:
    m = _CHAT_TEMPLATE_LEAK.search(text)
    if m:
        return text[: m.start()].strip()
    return text


# ---------------------------------------------------------------------------
# Tests — the pattern must NOT truncate legitimate prose
# ---------------------------------------------------------------------------

def test_system_word_mid_sentence_not_truncated() -> None:
    """A sentence containing 'system' must survive the filter intact."""
    text = "The system monitors disk usage and reports temperature."
    assert _apply_leak_filter(text) == text


def test_user_word_mid_sentence_not_truncated() -> None:
    """'user' inside a sentence must not trigger truncation."""
    text = "The user logged in and checked the dashboard."
    assert _apply_leak_filter(text) == text


def test_assistant_word_mid_sentence_not_truncated() -> None:
    """'assistant' inside a sentence must not trigger truncation."""
    text = "I'm your assistant — let me know if you need anything else."
    assert _apply_leak_filter(text) == text


def test_system_alone_on_line_is_stripped() -> None:
    """Bare 'system' on its own line IS chat template leakage — strip it."""
    text = "Here is my answer.\nsystem\nsome continuation"
    result = _apply_leak_filter(text)
    assert result == "Here is my answer."
    assert "system" not in result.split("\n")[-1]


def test_user_alone_on_line_is_stripped() -> None:
    """Bare 'user' on its own line IS leakage."""
    text = "Sure thing.\nuser"
    result = _apply_leak_filter(text)
    assert result == "Sure thing."


def test_assistant_alone_on_line_is_stripped() -> None:
    """Bare 'assistant' on its own line IS leakage."""
    text = "I can help with that.\nassistant\nmore junk"
    result = _apply_leak_filter(text)
    assert result == "I can help with that."


def test_role_with_colon_alone_is_stripped() -> None:
    """'user:' alone on a line is also leakage."""
    text = "Done.\nuser:"
    result = _apply_leak_filter(text)
    assert result == "Done."


def test_no_leakage_unchanged() -> None:
    """Clean reply with no leakage at all passes through unchanged."""
    text = "Everything looks good from where I'm standing."
    assert _apply_leak_filter(text) == text


def test_multiline_reply_with_system_in_prose() -> None:
    """A multi-paragraph reply that mentions system in prose is not mangled."""
    text = (
        "Here's what happened:\n\n"
        "The system rebooted at 03:00.\n"
        "The assistant restarted all services.\n"
        "No user was affected."
    )
    assert _apply_leak_filter(text) == text
