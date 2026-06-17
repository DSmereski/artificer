"""Reply post-processor that drops fabricated specs + action claims.

Lives outside `hive_coordinator.py` because:
  - 180 lines of regex tables had no business in the coordinator's
    dispatch loop (analyst's 2026-04-29 review).
  - Every component the regex tables describe is testable in isolation.
  - The synthesizer (or any future post-processor) can reuse it.

Two guards run in series on each candidate sentence:

1. **Action-claim guard.** When a sentence claims the assistant
   performed an action (`saved to vault`, `cross-linked`, ...) we
   require the corresponding verb to be in the synthesizer's emitted
   actions list. Discovered after 2026-04-28 turn-log review: Terry
   kept saying "I saved that to your vault" in replies that emitted no
   `vault_learn`.
2. **Number guard.** Sentences with no specific numbers pass through.
   Sentences whose every specific number (≥3 digits) appears in the
   helper-output haystack also pass. A sentence is dropped only when
   at least one number is untraceable AND the sentence has at least
   one specific-looking number — guards against tossing benign small
   numbers like "a couple" or "one of them".

Refusals/warnings/safety messages are exempt — the guard's purpose is
fabricated specs, not user-facing safety content (rate-limit notices,
"call 911", HIPAA refusals, etc.).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any


log = logging.getLogger("gateway.hallucination_guard")


# A "fact-bearing" number: optional sign / commas / decimal, at least
# one digit. Bare numbers like "150" qualify when the surrounding reply
# is meant to describe specs.
_NUMBER_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")

# Time-of-day patterns like "9:30 AM" / "23:00" / "10:30". The bare
# `_NUMBER_RE` splits these into 1-2-char chunks that fail the
# specificity threshold, so a synth that fabricates business hours
# slips past the number guard. This regex captures the whole token
# (with optional am/pm suffix) as one "specific" claim that must
# trace to a helper haystack.
_TIME_RE = re.compile(
    r"\b\d{1,2}:\d{2}(?:\s*[ap]\.?m\.?)?\b",
    re.IGNORECASE,
)
# Sentences mentioning these terms are exempt — the guard targets
# fabricated-spec hallucinations, not refusals/warnings/safety where
# the user-facing message must stay intact.
_REFUSAL_RE = re.compile(
    r"\b(can't|cannot|won['\u2019]?t|refuse[ds]?|warning|caution|"
    r"emergency|do not|don['\u2019]?t|please don['\u2019]?t|"
    r"rate[\s-]?limit|forbidden|unauthori[sz]ed|"
    r"911|hotline|HIPAA|GDPR|safety|risk)\b",
    re.IGNORECASE,
)
# Sentence terminator. Doesn't try to be perfect — close enough.
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\"'])")
_NUM_SCRUB = re.compile(r"[,\s]")

# Action-claim guard. Each entry is (pattern, required_verb): when the
# pattern matches a sentence, the named verb must be in the synth's
# emitted actions list. If it isn't, the synth is claiming something
# it didn't actually do — strip the sentence.
#
# `vault_learn` covers smart-link / cross-link / wiki-link claims too:
# `ActionExecutor.vault_learn` performs auto-linking via `_autolink_body`,
# so smart-link claims are valid iff a vault_learn was emitted.
_ACTION_CLAIM_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(
        r"\b(?:smart[\s-]?link|cross[\s-]?link|cross[\s-]?referenc|"
        r"wiki[\s-]?link)",
        re.IGNORECASE,
    ), "vault_learn"),
    # Co-occurrence patterns within one sentence: any of
    # saved/stored/wrote/added/created together with a vault marker
    # (the literal word "vault", a category descriptor, a "note titled
    # X" phrase). Wide-window because the SC knowledge-base run
    # showed phrasings like "I've saved a top-level note titled
    # 'X' under the `knowledge` category" — the verb sits 40+ chars
    # from the vault marker. All require vault_learn in actions.
    (re.compile(
        r"\b(?:I'?ve\s+|I\s+)?(?:saved|stored|wrote|added|created)\b"
        r".{0,140}?\bvault\b",
        re.IGNORECASE | re.DOTALL,
    ), "vault_learn"),
    (re.compile(
        r"\b(?:I'?ve\s+|I\s+)?(?:saved|stored|wrote|added|created)\b"
        r".{0,140}?\bunder\s+(?:the\s+)?[`'\"]?[\w-]+[`'\"]?\s+"
        r"(?:category|folder|namespace)\b",
        re.IGNORECASE | re.DOTALL,
    ), "vault_learn"),
    (re.compile(
        r"\b(?:I'?ve\s+|I\s+)?(?:saved|stored|wrote|added|created)\b"
        r".{0,140}?\bnote\s+(?:titled|named|called)\b",
        re.IGNORECASE | re.DOTALL,
    ), "vault_learn"),
    (re.compile(
        r"\b(?:I'?ve\s+)?(?:saved|stored)\s+(?:it|this|that)\b",
        re.IGNORECASE,
    ), "vault_learn"),
    # Passive voice: "the note has been saved as `slug.md`", "this
    # entry was added to your knowledge base", "X has been recorded".
    # Strong action-claim regardless of whether "vault" appears — if
    # the synth says something was saved/written/recorded but no
    # vault_learn fired, it's lying.
    (re.compile(
        r"\b(?:has|have|was|were)\s+been\s+"
        r"(?:saved|stored|written|added|created|recorded|compiled|persisted)\b",
        re.IGNORECASE,
    ), "vault_learn"),
    (re.compile(
        r"\b(?:has|have|was|were)\s+"
        r"(?:saved|stored|written|added|created|recorded|persisted)\b"
        r".{0,80}?\b(?:vault|note|knowledge\s+base|entry)\b",
        re.IGNORECASE | re.DOTALL,
    ), "vault_learn"),
    # "Saved as `slug.md`" / "Saved to: knowledge/factions/x.md".
    # The SC knowledge-base run (2026-06-04) showed planner-qwen
    # producing these phrasings 9/33 turns with no vault_learn emitted.
    (re.compile(
        r"\b(?:I'?ve\s+|I\s+)?(?:saved|stored|wrote|added|created|logged)"
        r"\s+(?:as|to:?)\s+[`'\"]?[\w/.-]+\.(?:md|markdown)\b",
        re.IGNORECASE,
    ), "vault_learn"),
    # "Saved the new lore note" / "Saved the new manufacturer profile".
    (re.compile(
        r"\b(?:I'?ve\s+|I\s+)?(?:saved|stored|wrote|added|created|logged)\s+"
        r"(?:the\s+)?(?:new\s+|fresh\s+|updated\s+)?\w+\s+"
        r"(?:note|profile|entry|lore)\b",
        re.IGNORECASE,
    ), "vault_learn"),
    # "Saved 'X' as `file.md`" / "Saved 'X' to the vault".
    (re.compile(
        r"\b(?:I'?ve\s+|I\s+)?(?:saved|stored|wrote|added|created|logged)\s+"
        r"['\"`][^'\"`\n]{2,80}['\"`]",
        re.IGNORECASE,
    ), "vault_learn"),
    # Third-person Terry claims: "Terry has logged...", "Terry saved...".
    (re.compile(
        r"\bTerry\s+(?:has\s+|just\s+)?"
        r"(?:logged|saved|stored|added|created|recorded|persisted|"
        r"compiled|wrote)\b",
        re.IGNORECASE,
    ), "vault_learn"),
    # "Logged that as an escalation" — synth claiming the escalate_to_dev
    # action without emitting it. The SC run showed this verbatim 3 turns.
    (re.compile(
        r"\b(?:I'?ve\s+|I\s+)?logged\s+(?:that|this|it)\s+"
        r"as\s+(?:an?\s+)?escalation\b",
        re.IGNORECASE,
    ), "escalate_to_dev"),
]


# Helper-run claim patterns: synth-emitted sentences asserting that a
# specific retrieval helper ran (`fired a web search`, `the librarian
# checked`, etc.). Each pattern maps to the helper role it claims —
# the sentence is dropped when that role isn't in helper_results, i.e.
# the synth lied about an operation that never happened.
#
# Observed prod failure mode: planner emitted no delegations,
# helper_results is empty, but the synth still says "I just fired a
# live web search... I'm waiting for the corroborated results to come
# back" — pure invention. User notices and loses trust ("the patch
# number is wrong?", "where did you get this info").
_HELPER_RUN_CLAIM_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Web-search / researcher claims.
    (re.compile(
        r"\b(?:fired|firing|ran|running|trigger(?:ed|ing)?|will\s+trigger|"
        r"I['’]?ll\s+(?:fire|trigger|run|fire\s+up))\s+"
        r"(?:a\s+|the\s+|another\s+)?"
        r"(?:live\s+|fresh\s+|targeted\s+|quick\s+|new\s+)?"
        r"(?:web\s+search|ddg\s+search|google\s+search)\b",
        re.IGNORECASE,
    ), "researcher"),
    (re.compile(
        r"\b(?:web\s+search|live\s+search|the\s+search|search\s+result)s?\s+"
        r"(?:has\s+)?(?:returned|finished|completed|came\s+back)\b",
        re.IGNORECASE,
    ), "researcher"),
    (re.compile(
        r"\b(?:the\s+)?researcher\s+(?:ran|returned|found|reported|"
        r"pulled|checked|extracted|came\s+back)\b",
        re.IGNORECASE,
    ), "researcher"),
    (re.compile(
        r"\b(?:searched|searched\s+through|scoured)\s+(?:the\s+)?web\b",
        re.IGNORECASE,
    ), "researcher"),
    # Vault / librarian claims.
    (re.compile(
        r"\b(?:the\s+)?librarian\s+(?:ran|returned|found|checked|reported|"
        r"scanned|came\s+back|pulled)\b",
        re.IGNORECASE,
    ), "librarian"),
    (re.compile(
        r"\b(?:checked|searched|scanned|scoured)\s+(?:the\s+)?"
        r"(?:internal\s+)?vault\b",
        re.IGNORECASE,
    ), "librarian"),
]


# Sentences / lines that are pure synth meta-scaffolding and have no
# place in a user-facing reply. The LLM occasionally emits these as
# headers around its actual response.
_META_PREAMBLE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^\s*here\s+is\s+terry['’]?s\s+reply\b.*$",
               re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*here\s+is\s+the\s+reply\b.*$",
               re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*based\s+on\s+(?:the\s+)?(?:conversation\s+history|"
               r"chat\s+history|vault\s+preflight|context|"
               r"helper\s+results)[^\n]*here\s+is.*$",
               re.IGNORECASE | re.MULTILINE),
    # Standalone markdown horizontal rules.
    re.compile(r"^\s*---+\s*$", re.MULTILINE),
    # `### **Terry:**` header style.
    re.compile(r"^\s*#{1,6}\s*\*?\*?terry:?\*?\*?\s*$",
               re.IGNORECASE | re.MULTILINE),
]


# JSON fenced blocks that leak the synth's action JSON into the user-
# visible prose. The action_executor consumes structured actions
# directly from synth.output.actions — the user should never see a
# fenced JSON block.
_LEAKED_ACTION_JSON_RE = re.compile(
    r"```(?:json)?\s*\{[^`]*\"(?:verb|payload|query|limit)\"[^`]*\}\s*```",
    re.IGNORECASE | re.DOTALL,
)


def _normalize_number(s: str) -> str:
    """'1,200' / ' 1200 ' / '1200.0' → '1200'. Lets us match across
    formatting differences between reply and helper output."""
    cleaned = _NUM_SCRUB.sub("", s)
    if "." in cleaned:
        try:
            f = float(cleaned)
            if f.is_integer():
                return str(int(f))
            return str(f)
        except ValueError:
            return cleaned
    return cleaned.lstrip("0") or "0"


def _normalize_time(s: str) -> str:
    """'9:30 AM' / '9:30am' / '09:30' → '0930am' (or '0930' if no
    meridiem). Lets the haystack-trace match across formatting
    differences between the synth reply and the helper output."""
    s = s.lower().replace(" ", "").replace(".", "")
    # Strip any leading zero on the hour so '9:30am' and '09:30am'
    # collide.
    parts = s.split(":", 1)
    if len(parts) == 2:
        hh = parts[0].lstrip("0") or "0"
        rest = parts[1]
        return f"{hh}:{rest}"
    return s


def _haystack_for_fact_trace(helper_results: list[Any]) -> str:
    """Concatenate every helper's text + JSON output into one
    substring-search string. Generic over the helper-result type so
    this module doesn't import HelperResult — the only fields used are
    `.raw_text` and `.output`."""
    parts: list[str] = []
    for h in helper_results:
        if h is None:
            continue
        rt = getattr(h, "raw_text", "")
        if rt:
            parts.append(rt)
        out = getattr(h, "output", None)
        if isinstance(out, dict):
            try:
                parts.append(json.dumps(out, default=str))
            except Exception:  # noqa: BLE001
                pass
    return "\n".join(parts)


def strip_hallucinated_sentences(
    reply: str,
    helper_results: list[Any],
    actions: list[dict] | None = None,
) -> str:
    """Filter `reply` against the action-claim and number guards.

    Returns the original `reply` unchanged when filtering would empty
    it — better imperfect than blank. Logs a warning for each dropped
    sentence so the next reviewer can audit false positives.
    """
    if not reply:
        return reply
    # Strip leaked action-JSON fenced blocks BEFORE sentence splitting
    # — they contain newlines that would corrupt the sentence stream.
    reply = _LEAKED_ACTION_JSON_RE.sub("", reply)
    # Strip meta-preamble lines ("Here is Terry's reply", standalone
    # `---`, `### **Terry:**` headers, "Based on X here is Y" wrappers).
    for pat in _META_PREAMBLE_PATTERNS:
        reply = pat.sub("", reply)
    reply = reply.strip()
    if not reply:
        return ""

    haystack = _haystack_for_fact_trace(helper_results) if helper_results else ""
    hay_nums: set[str] = set()
    hay_times: set[str] = set()
    if haystack:
        for m in _NUMBER_RE.finditer(haystack):
            hay_nums.add(_normalize_number(m.group(0)))
        for m in _TIME_RE.finditer(haystack):
            hay_times.add(_normalize_time(m.group(0)))

    emitted_verbs: set[str] = set()
    if actions:
        for a in actions:
            if isinstance(a, dict):
                v = a.get("verb")
                if isinstance(v, str):
                    emitted_verbs.add(v)

    # Roles that actually executed this turn. Used by the helper-run
    # claim guard to drop sentences asserting that a helper ran when
    # it didn't.
    helper_roles: set[str] = set()
    for h in helper_results:
        role = getattr(h, "role", None)
        if isinstance(role, str):
            helper_roles.add(role)

    sentences = _SENT_SPLIT.split(reply.strip())
    kept: list[str] = []
    # Track whether high-confidence drops happened (action-claim,
    # helper-run claim). On these, returning empty is correct —
    # the original reply was making a verifiable false claim. The
    # number-guard's fallback to original only applies when the
    # high-confidence guards didn't fire.
    high_confidence_drop = False
    for s in sentences:
        # Action-claim guard runs first — it's the more user-visible
        # hallucination class (assistant claiming to do things it
        # didn't actually do).
        action_drop = False
        for pat, required_verb in _ACTION_CLAIM_PATTERNS:
            if not pat.search(s):
                continue
            if required_verb not in emitted_verbs:
                log.warning(
                    "hallucination guard: dropped action-claim sentence "
                    "(needed verb=%r, emitted=%s) — sentence=%r",
                    required_verb, sorted(emitted_verbs), s[:120],
                )
                action_drop = True
                high_confidence_drop = True
                break
        if action_drop:
            continue

        # Helper-run claim guard. The synth asserts a retrieval helper
        # ran but the role is missing from helper_results — drop the
        # sentence. This catches "I fired a web search" / "the
        # librarian found..." when no such helper ever executed.
        helper_drop = False
        for pat, required_role in _HELPER_RUN_CLAIM_PATTERNS:
            if not pat.search(s):
                continue
            if required_role not in helper_roles:
                log.warning(
                    "hallucination guard: dropped helper-run claim "
                    "(needed role=%r, ran=%s) — sentence=%r",
                    required_role, sorted(helper_roles), s[:120],
                )
                helper_drop = True
                high_confidence_drop = True
                break
        if helper_drop:
            continue

        # Time-of-day guard. Times escape the number guard (HH:MM
        # tokens split into 2-char chunks below the specificity floor)
        # so synth fabrications like "open 9:30 AM until 5:00 PM" sail
        # through. Treat each time-token as one specific claim that
        # must trace to a helper haystack.
        times_in_sent = [m.group(0) for m in _TIME_RE.finditer(s)]
        if times_in_sent and haystack:
            untraced_times = [
                t for t in times_in_sent
                if _normalize_time(t) not in hay_times
            ]
            if untraced_times and not _REFUSAL_RE.search(s):
                log.warning(
                    "hallucination guard: dropped sentence with "
                    "untraced times %s — sentence=%r",
                    untraced_times, s[:120],
                )
                high_confidence_drop = True
                continue

        # Number guard.
        nums = [m.group(0) for m in _NUMBER_RE.finditer(s)]
        if not nums or not haystack:
            kept.append(s)
            continue
        specific = [n for n in nums if len(_normalize_number(n)) >= 3]
        if not specific:
            kept.append(s)
            continue
        if _REFUSAL_RE.search(s):
            kept.append(s)
            continue
        traced = all(_normalize_number(n) in hay_nums for n in specific)
        if traced:
            kept.append(s)
        else:
            log.warning(
                "hallucination guard: dropped sentence with untraced "
                "numbers %s — sentence=%r",
                [n for n in specific if _normalize_number(n) not in hay_nums],
                s[:120],
            )
    if not kept:
        if high_confidence_drop:
            # All sentences were dropped by the action-claim or
            # helper-run claim guards — the original reply was making
            # verifiably false statements. Return empty; downstream
            # (`enforce_empty_retrieval_reply` / `_compose_fallback`)
            # substitutes a safe canonical message.
            return ""
        # Only the number guard fired; the dropped sentences may be
        # over-aggressive false positives. Better imperfect than blank.
        return reply
    return " ".join(kept).strip()


# ---------------------------------------------------------------- empty-retrieval guard

# Canonical reply for the "every retrieval helper came back empty"
# case. Drops the user out of the fabrication loop by replacing a
# hallucinated, narrative-rich reply with a direct admission.
_CANONICAL_EMPTY_RETRIEVAL_REPLY = (
    "I couldn't find that in your vault or on the web. "
    "Try rephrasing, or give me a more specific angle to search for."
)

# Phrases that signal the synthesizer correctly acknowledged the empty
# result instead of fabricating. If the reply contains any of these,
# the empty-retrieval guard treats it as well-formed and leaves it
# alone.
_EMPTY_ACK_PHRASES = (
    "couldn't find",
    "could not find",
    "no notes",
    "came back empty",
    "returned empty",
    "no specific spots",
    "nothing about",
    "not in your vault",
    "not on the web",
    "don't have any notes",
    "do not have any notes",
    "didn't turn up",
    "did not turn up",
    "nothing matched",
    "no hits",
)


def _is_retrieval_role(role: str) -> bool:
    return role in {"librarian", "researcher"}


def _output_has_signal(output: Any) -> bool:
    """True iff a helper's output dict has structured retrieval content
    the synthesizer could ground a reply on: non-empty hits, facts,
    or citations.

    A standalone summary is NOT signal. Librarians routinely emit
    summaries like "no notes found" or "checked vault, nothing
    matched"; those carry no answer content. The earlier rule
    ("summary > 80 chars = signal") let synthesizer fabrications
    slip through because the LLM's own empty-result summary cleared
    the bar.
    """
    if not isinstance(output, dict):
        return False
    hits = output.get("hits")
    if isinstance(hits, list) and hits:
        return True
    facts = output.get("facts")
    if isinstance(facts, list) and facts:
        return True
    citations = output.get("citations")
    if isinstance(citations, list) and citations:
        return True
    return False


def _snippet_from_hit(hit: Any) -> str:
    """Pull a short human-readable string out of a single hit/fact item.

    Generic over hit shape: librarian hits carry `excerpt`/`body`,
    researcher hits may carry `snippet`/`text`/`content`/`summary`.
    Plain strings (e.g. `facts` entries) pass through. Returns "" when
    nothing usable is present so the caller can skip it.
    """
    if isinstance(hit, str):
        return hit.strip()
    if isinstance(hit, dict):
        for key in ("excerpt", "snippet", "text", "content", "body", "summary"):
            v = hit.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return ""


def grounded_snippets_from_helpers(
    helper_results: list[Any],
    *,
    max_items: int = 3,
    max_chars: int = 600,
) -> list[str]:
    """Collect verbatim retrieval snippets from helpers that have signal.

    Used to compose a grounded fallback reply when the synthesizer
    times out / errors but real retrieval content exists — better to
    hand the user what we actually found than a generic apology.

    Returns at most `max_items` snippets, each clamped, drawn from
    `hits` / `facts` / `citations` of helpers that pass
    `_output_has_signal`. Never includes role names. Returns [] when
    there's nothing groundable.
    """
    snippets: list[str] = []
    seen: set[str] = set()
    for h in helper_results:
        if h is None:
            continue
        out = getattr(h, "output", None)
        if not _output_has_signal(out):
            continue
        for key in ("facts", "hits", "citations"):
            items = out.get(key)
            if not isinstance(items, list):
                continue
            for item in items:
                text = _snippet_from_hit(item)
                if not text:
                    continue
                if len(text) > 200:
                    text = text[:200].rstrip() + "…"
                norm = text.lower()
                if norm in seen:
                    continue
                seen.add(norm)
                snippets.append(text)
                if len(snippets) >= max_items:
                    break
            if len(snippets) >= max_items:
                break
        if len(snippets) >= max_items:
            break

    # Clamp total size.
    total = 0
    clamped: list[str] = []
    for s in snippets:
        if total + len(s) > max_chars:
            break
        clamped.append(s)
        total += len(s)
    return clamped


def all_retrieval_helpers_empty(helper_results: list[Any]) -> bool:
    """True iff every librarian/researcher entry in helper_results is
    empty or errored.

    Returns False when there are no retrieval helpers at all (the
    decision doesn't apply) or when at least one retrieval helper has
    real output signal.
    """
    saw_retrieval = False
    for r in helper_results:
        role = getattr(r, "role", "")
        if not _is_retrieval_role(role):
            continue
        saw_retrieval = True
        if getattr(r, "error", None):
            continue  # error counts as empty
        if _output_has_signal(getattr(r, "output", None)):
            return False
    return saw_retrieval


_HIT_TOKEN_RE = re.compile(r"\b[A-Z][A-Za-z0-9'-]{2,}\b")
_GENERIC_PREAMBLE_RE = re.compile(
    r"\b(?:"
    r"here\s+is\s+(?:terry'?s\s+)?(?:the\s+)?(?:reply|composed\s+reply|summary)"
    r"|based\s+on\s+(?:the\s+)?(?:user'?s\s+|the\s+)?(?:query|conversation|"
    r"vault|notes?|results?|preflight|context|knowledge\s+base)"
    r"|synthesi[zs]ed\s+from"
    r")",
    re.IGNORECASE,
)


def _extract_capitalised_tokens(text: str, *, max_tokens: int = 40) -> set[str]:
    """Pull out distinct capitalised proper-noun-like tokens from text.

    Heuristic, not a parser: enough to detect whether two pieces of
    text share concrete entity references. Skips common sentence
    starters ('The', 'This') by length filter + a tiny stop-set.
    """
    stops = {"The", "This", "That", "These", "Those", "Here",
             "Terry", "Based", "Reply", "Summary", "Star", "Citizen"}
    out: set[str] = set()
    for m in _HIT_TOKEN_RE.finditer(text):
        tok = m.group(0)
        if tok in stops:
            continue
        out.add(tok)
        if len(out) >= max_tokens:
            break
    return out


def enforce_groundedness_with_hits(
    reply: str, helper_results: list[Any], user_msg: str = "",
) -> str:
    """When retrieval helpers returned real content but the reply lacks
    concrete tokens from those hits AND looks like generic preamble,
    replace the reply with a grounded snippet summary.

    Catches the SC-retrieval failure mode where synth wraps vault hits
    in fluffy preamble ("Based on the vault search results, here is
    Terry's reply…") without surfacing any specific entity name —
    a 200-char generic answer when the librarian found the exact note.

    Returns reply unchanged when:
    - no retrieval helpers ran
    - retrieval helpers were empty (let enforce_empty_retrieval handle)
    - reply already shares ≥2 capitalised tokens with the hit bodies
    - no hit bodies are available
    """
    if not reply:
        return reply
    if all_retrieval_helpers_empty(helper_results):
        return reply  # different guard handles this
    # Collect capitalised tokens from hit bodies.
    hit_tokens: set[str] = set()
    for h in helper_results:
        if h is None:
            continue
        if not _is_retrieval_role(getattr(h, "role", "")):
            continue
        out = getattr(h, "output", None)
        if not isinstance(out, dict):
            continue
        for key in ("hits", "facts", "citations"):
            items = out.get(key)
            if not isinstance(items, list):
                continue
            for item in items:
                text = _snippet_from_hit(item)
                if text:
                    hit_tokens |= _extract_capitalised_tokens(text)
    # Exclude tokens that came from the user's question (Drake when the
    # user asks "tell me about Drake Interplanetary"). Otherwise the
    # synth can earn full overlap credit by echoing the query — exactly
    # the failure mode this guard is meant to catch.
    user_tokens = _extract_capitalised_tokens(user_msg) if user_msg else set()
    hit_tokens -= user_tokens
    if len(hit_tokens) < 3:
        return reply  # not enough ground truth to score against
    reply_tokens = _extract_capitalised_tokens(reply) - user_tokens
    overlap = hit_tokens & reply_tokens
    # Pass: ≥2 entity hits OR reply doesn't look like generic preamble.
    has_preamble = bool(_GENERIC_PREAMBLE_RE.search(reply))
    if len(overlap) >= 2 or not has_preamble:
        return reply
    grounded = grounded_snippets_from_helpers(helper_results)
    if not grounded:
        return reply
    log.warning(
        "groundedness guard fired: reply had %d/%d hit-token overlap "
        "and looked like preamble (len=%d); replacing with %d grounded "
        "snippets",
        len(overlap), len(hit_tokens), len(reply), len(grounded),
    )
    return "Here's what your vault has on that:\n\n" + "\n".join(
        f"- {s}" for s in grounded
    )


def enforce_empty_retrieval_reply(
    reply: str, helper_results: list[Any],
) -> str:
    """Replace `reply` with the canonical empty-result message when
    every retrieval helper came back empty AND the reply doesn't
    already acknowledge the empty state.

    The synthesizer prompt asks the LLM to produce an admission like
    "I couldn't find that..." but the LLM occasionally fabricates a
    confident narrative answer from training data instead (observed
    in production: "Cafe de Shimokitazawa (Shimokitazawa 1-chome)..."
    with hours + vibe that nothing in the helpers supplied). This
    function detects that case and forces the canonical admission.

    Returns the input `reply` unchanged when:
    - there are no retrieval helpers (decision doesn't apply)
    - at least one retrieval helper had output signal
    - the reply already contains an acknowledgement phrase
    """
    if not reply or not all_retrieval_helpers_empty(helper_results):
        return reply
    lowered = reply.lower()
    if any(p in lowered for p in _EMPTY_ACK_PHRASES):
        return reply
    log.warning(
        "empty-retrieval guard fired: every retrieval helper was "
        "empty but reply lacked an acknowledgement; replacing "
        "fabricated reply (len=%d) with canonical message",
        len(reply),
    )
    return _CANONICAL_EMPTY_RETRIEVAL_REPLY
