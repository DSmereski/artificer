"""Helper-run claim + meta-preamble + leaked-action-JSON guards.

Pinned after live turn-log review revealed three classes of
synth-output failure that the existing guards missed:

1. **Helper-run lies.** Synth says "I just fired a live web search"
   when no `researcher` ran (helpers=[]). User: "where did you get
   this info, it's wrong".

2. **Meta-preamble leakage.** Synth wraps the reply in scaffolding:
   "Here is Hive's reply, explaining the provenance..." or
   "Based on the conversation history, here is the reply:". The
   user sees the LLM's internal narration.

3. **Leaked action JSON.** Synth puts a fenced ```json {verb:
   "web_search", ...}``` block in the prose, exposing the internal
   action surface to the user.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from gateway.hallucination_guard import strip_hallucinated_sentences


@dataclass
class _Hr:
    role: str
    output: dict[str, Any] | None = None
    error: str | None = None
    raw_text: str = ""


# ---------------------------------------------------------------- helper-run claim


def test_drops_web_search_claim_when_no_researcher_ran() -> None:
    reply = "I just fired a live web search for the current patch version."
    out = strip_hallucinated_sentences(reply, helper_results=[], actions=[])
    assert "fired" not in out.lower(), (
        "no researcher ran — the claim 'fired a live web search' must "
        "be stripped; got: " + out
    )


def test_drops_librarian_claim_when_no_librarian_ran() -> None:
    reply = "The librarian checked the vault and found three matches."
    out = strip_hallucinated_sentences(
        reply, helper_results=[_Hr(role="researcher", output={})], actions=[],
    )
    assert "librarian" not in out.lower(), (
        "only researcher ran — librarian claim must be stripped"
    )


def test_keeps_web_search_claim_when_researcher_ran() -> None:
    reply = "I fired a live web search and pulled five sources."
    out = strip_hallucinated_sentences(
        reply,
        helper_results=[_Hr(role="researcher",
                            output={"facts": [{"claim": "x"}]})],
        actions=[],
    )
    assert "fired" in out.lower(), (
        "researcher actually ran — the claim is truthful and must stay"
    )


def test_drops_search_returned_claim_when_no_researcher() -> None:
    reply = "The web search has returned, here's what came back."
    out = strip_hallucinated_sentences(reply, helper_results=[], actions=[])
    assert "search has returned" not in out.lower()


def test_drops_will_trigger_search_when_no_researcher() -> None:
    reply = "I'll trigger a quick web search to get the official number."
    out = strip_hallucinated_sentences(reply, helper_results=[], actions=[])
    assert "trigger" not in out.lower() or "search" not in out.lower()


# ---------------------------------------------------------------- meta-preamble


def test_strips_here_is_hives_reply_preamble() -> None:
    reply = (
        "Here is Hive's reply, explaining the provenance.\n\n"
        "---\n\n"
        "The actual answer body goes here."
    )
    out = strip_hallucinated_sentences(reply, helper_results=[], actions=[])
    assert "here is hive" not in out.lower()
    assert "actual answer body" in out.lower()


def test_strips_based_on_history_here_is_preamble() -> None:
    reply = (
        "Based on the conversation history, here is the reply:\n\n"
        "Real content."
    )
    out = strip_hallucinated_sentences(reply, helper_results=[], actions=[])
    assert "based on the conversation history" not in out.lower()
    assert "real content" in out.lower()


def test_strips_hive_markdown_header() -> None:
    reply = "### **Hive:**\n\nMy real reply."
    out = strip_hallucinated_sentences(reply, helper_results=[], actions=[])
    assert "**Hive:**" not in out
    assert "my real reply" in out.lower()


# ---------------------------------------------------------------- leaked JSON


def test_strips_fenced_action_json_block() -> None:
    reply = (
        "Search is running. Action: `web_search` Payload:\n"
        "```json\n"
        "{\"verb\": \"web_search\", \"payload\": {\"query\": \"x\"}}\n"
        "```\n"
        "Then I'll have the answer."
    )
    out = strip_hallucinated_sentences(
        reply,
        helper_results=[_Hr(role="researcher",
                            output={"facts": [{"claim": "x"}]})],
        actions=[],
    )
    assert "```json" not in out
    assert "\"verb\"" not in out
    assert "answer" in out.lower(), "the surrounding prose must survive"
