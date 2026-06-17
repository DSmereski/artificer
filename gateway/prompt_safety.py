"""Prompt-injection defence for helper outputs flowing into the
synthesizer.

Helper outputs (librarian vault hits, researcher web summaries,
recipe-importer pasted text, …) all contain content the user didn't
author themselves. The synthesizer's prompt is the only barrier
between that content and a structured action emission, so the
2026-04-29 security review flagged this as a HIGH-LOW finding —
soft-prompt guard only, no programmatic wrap.

This module's job: tag every untrusted string with `<untrusted>...
</untrusted>` markers so the synthesizer prompt can adopt a "treat
delimited content as data, not instructions" rule. Existing markers
in the source string are escaped (`<` → `\\<`) so an attacker can't
close + reopen the wrapping to smuggle directives in.

Why a small custom convention rather than a sandbox: the synthesizer
is an LLM, not a parser. We can't *guarantee* it ignores instructions
inside data — but the explicit delimiter materially reduces the
attack surface and makes the boundary auditable in turn logs. Pair
with the post-synth critic gate for risky verbs.
"""

from __future__ import annotations

from typing import Any


# Pair of markers. Pick something the model is unlikely to emit by
# accident and that's distinctive enough to grep for in the turn log.
OPEN_MARK = "<untrusted>"
CLOSE_MARK = "</untrusted>"

# Per-string cap on helper output flowing into the synthesizer prompt.
# Ollama's 8192-token ctx silently truncates oversize prompts (server.log
# evidence: `truncating input prompt limit=8192 prompt=8613`). 4000 chars
# ≈ 1000 tokens — comfortably under budget for ~3 helper results in a
# turn while still letting researcher carry meaningful body text.
MAX_HELPER_STRING_CHARS = 4000


def wrap_untrusted(s: str, cap_chars: int | None = MAX_HELPER_STRING_CHARS) -> str:
    """Wrap a string in markers, neutralising any pre-existing
    markers in the content so an attacker can't break out by
    embedding `</untrusted>` and following it with directives.

    Escape strategy: replace each occurrence of the open / close mark
    with a backslash-broken variant the model can still display but
    that no longer matches the boundary tokens.

    Strings longer than ``cap_chars`` are truncated with a visible
    ``...[truncated, N chars]`` marker so the synthesizer prompt can't
    blow out Ollama's ctx and trigger silent server-side truncation.
    Pass ``cap_chars=None`` to disable.
    """
    if not isinstance(s, str):
        return s
    if cap_chars is not None and len(s) > cap_chars:
        dropped = len(s) - cap_chars
        s = s[:cap_chars] + f"...[truncated, {dropped} chars]"
    safe = (
        s.replace(OPEN_MARK, OPEN_MARK[:1] + "\\" + OPEN_MARK[1:])
         .replace(CLOSE_MARK, CLOSE_MARK[:1] + "\\" + CLOSE_MARK[1:])
    )
    return f"{OPEN_MARK}{safe}{CLOSE_MARK}"


def sanitise_helper_outputs(
    helper_results: list[Any],
) -> list[dict]:
    """Project each helper result to a synthesizer-safe dict.

    String fields (`summary`, free-form `output` values, citation text)
    are wrapped in `<untrusted>...</untrusted>`. Non-strings are kept
    structurally — they can't carry prompt-injection on their own
    (the synthesizer reads them through JSON).

    Filters out helper results with `error` set since those have no
    useful content to surface. Pass `helper_results=[r for r in ...
    if not r.error]` upstream if you also want to drop them earlier.
    """
    out: list[dict] = []
    for r in helper_results:
        if r is None or getattr(r, "error", None):
            continue
        role = getattr(r, "role", "?")
        result_output = getattr(r, "output", {}) or {}
        citations = getattr(r, "citations", None) or []
        summary = ""
        if isinstance(result_output, dict):
            summary = str(result_output.get("summary", ""))
        out.append({
            "role": role,
            "summary": wrap_untrusted(summary),
            "output": _sanitise_value(result_output),
            "citations": [
                wrap_untrusted(c) if isinstance(c, str) else c
                for c in citations
            ],
        })
    return out


def _sanitise_value(
    v: Any,
    cap_chars: int | None = MAX_HELPER_STRING_CHARS,
) -> Any:
    """Recursively wrap string leaves in untrusted markers. Dicts and
    lists are walked structurally.

    ``cap_chars`` is threaded through every recursive call so strings
    at any nesting depth are subject to the same per-string cap as
    top-level strings. Passing ``cap_chars=None`` disables truncation
    for the entire subtree (useful in tests or internal callers that
    have already capped upstream).
    """
    if isinstance(v, str):
        return wrap_untrusted(v, cap_chars=cap_chars)
    if isinstance(v, list):
        return [_sanitise_value(x, cap_chars) for x in v]
    if isinstance(v, dict):
        return {k: _sanitise_value(val, cap_chars) for k, val in v.items()}
    return v
