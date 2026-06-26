"""Text markers Hive uses to signal structured intent in her chat reply.

Local Ollama models (qwen3-coder, planner-qwen) don't reliably produce OpenAI-
style tool-calls. Text markers in her reply are reliable enough — we already
use [GENERATE_IMAGE] this way. This module is the single source of truth for
all marker regexes and payload validators so the chat route, the Discord bot,
and the tests all stay in sync.

Markers:

  [GENERATE_IMAGE] <plain-or-json>     # already shipped; render now
  [CONFIRM_IMAGE]  {<json>}            # propose a payload; halt; wait for user yes/edit
  [ASK_USER]       <one-line question> # halt the turn and wait for user reply
  [REMEMBER]       {<json>}            # write a vault note via VaultClient.learn
  [VAULT_LOOKUP]   <one-line query>    # ask the gateway to search the vault and re-feed Hive
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from gateway.image_catalog import parse_image_payload


log = logging.getLogger("gateway.markers")


# Header regexes match just the [MARKER] tag. Payload extraction is custom
# in `_extract_one`: when the payload starts with '{' we read a balanced
# JSON object (which can span multiple lines — many models pretty-print);
# otherwise we read to end-of-line. This was a real bug when Hive emitted
# multi-line JSON: a `(.*?)\n` regex captured the empty string.
_MARKER_HEADER_RE = {
    "generate_image": re.compile(r"\[GENERATE_IMAGE\]"),
    "confirm_image":  re.compile(r"\[CONFIRM_IMAGE\]"),
    "ask_user":       re.compile(r"\[ASK_USER\]"),
    "remember":       re.compile(r"\[REMEMBER\]"),
    "vault_lookup":   re.compile(r"\[VAULT_LOOKUP\]"),
    "fix_fact":       re.compile(r"\[FIX_FACT\]"),
    "web_lookup":     re.compile(r"\[WEB_LOOKUP\]"),
    "create_skill":   re.compile(r"\[CREATE_SKILL\]"),
}

# Strip pattern: any of the known headers + everything until end of payload.
# Used by strip_markers; matches what _extract_one logically removes.
_STRIP_RE = re.compile(
    r"\[(?:GENERATE_IMAGE|CONFIRM_IMAGE|ASK_USER|REMEMBER|VAULT_LOOKUP|FIX_FACT|WEB_LOOKUP|CREATE_SKILL)\]"
    r"[ \t]*"
    r"(?:"
    r"\{(?:[^{}]|\{[^{}]*\})*\}"   # balanced single-level-nested JSON
    r"|"
    r"[^\n]*"                       # or a single-line free-text payload
    r")"
    r"\n?",
    re.DOTALL,
)


# Fake "I'm rendering" prose Hive sometimes invents. The gateway emits real
# image_pending / image_done events; she should NEVER fabricate progress
# lines. We strip these on output AND before saving to history so the next
# turn doesn't see them and copy the pattern.
_FAKE_PROGRESS_RE = re.compile(
    r"^[ \t]*("
    r"Status:[^\n]*\n?"                                    # "Status: ..." lines
    r"|"
    r"\[?Image requested[: ][^\n\]]*\]?\n?"                # legacy Discord marker
    r"|"
    r"(?:Initializing|Loading|Sculpting|Rendering|Finalizing|Generating)[^\n]*\n?"
    r"|"
    r"[^\n]*\d{1,3}%\s*complete[^\n]*\n?"                  # "... NN% complete"
    r")",
    re.IGNORECASE | re.MULTILINE,
)


def sanitize_hive_reply(reply: str) -> str:
    """Full sanitization for Hive's reply text.

    Strips: control markers, fake render-progress prose, orphaned
    bracket artifacts, and naked-JSON implicit-marker payloads (since
    those get re-routed by scan()'s lenient fallback, the raw JSON
    must not leak into the visible bubble). Collapses runs of blank
    lines so removed sections don't leave a chasm.
    """
    out = strip_markers(reply)
    out = _FAKE_PROGRESS_RE.sub("", out)
    # Strip a leading JSON object if it looks like a marker-payload
    # Hive forgot to prefix. Mirrors the lenient match in scan().
    stripped = out.lstrip()
    if stripped.startswith("{"):
        end = _read_balanced_json(stripped)
        if end is not None:
            try:
                obj = json.loads(stripped[:end])
            except json.JSONDecodeError:
                obj = None
            if isinstance(obj, dict) and (
                ("question" in obj and "options" in obj)
                or "prompt" in obj
            ):
                offset = len(out) - len(stripped)
                out = out[: offset] + stripped[end:]
    # Collapse 3+ newlines down to 2 (paragraph break).
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


@dataclass
class AskUserHit:
    """Parsed ASK_USER payload.

    `options` is empty when the question is open-ended. When non-empty, the
    app renders each as a tappable chip the user can pick from.
    """
    question: str
    options: list[str] = field(default_factory=list)


@dataclass
class FixFactHit:
    """Parsed FIX_FACT payload — overwrite or correct a vault note."""
    path: str                    # vault-relative .md path
    correction: str              # the corrected fact, body of the note
    mode: str = "append"         # "append" (keep history, add a CORRECTION block) | "replace"


@dataclass
class MarkerHits:
    """Result of scanning a single Hive reply for all known markers."""
    generate_image: dict | None = None    # parsed payload (uses image_catalog.parse_image_payload)
    confirm_image:  dict | None = None    # same shape as generate_image
    ask_user:       AskUserHit | None = None
    remember:       dict | None = None    # validated learn() params
    vault_lookup:   str | None = None     # the search query
    fix_fact:       FixFactHit | None = None
    web_lookup:     str | None = None     # the web query
    create_skill:   dict | None = None    # validated CREATE_SKILL payload
    raw_spans: list[tuple[int, int]] = field(default_factory=list)


def parse_create_skill(payload: str) -> dict | None:
    """Validate a [CREATE_SKILL] payload.

    Expected JSON shape:
      {"name": "...", "body": "<markdown with frontmatter>", ...}

    The full markdown body must include `---` frontmatter and at least
    one numbered step. Critic re-checks before write.
    """
    payload = payload.strip()
    if not payload.startswith("{"):
        return None
    try:
        obj = json.loads(payload)
    except json.JSONDecodeError as e:
        log.warning("[CREATE_SKILL] payload JSON parse failed: %s", e)
        return None
    if not isinstance(obj, dict):
        return None
    name = str(obj.get("name", "")).strip()
    body = str(obj.get("body", "")).strip()
    if not name or len(name) > 64:
        return None
    if len(body) < 100 or len(body) > 8 * 1024:
        return None
    if "---" not in body:
        # Frontmatter is required for the SkillRegistry parser.
        return None
    out: dict = {"name": name, "body": body}
    if "rationale" in obj:
        out["rationale"] = str(obj["rationale"])[:500]
    return out


def strip_markers(reply: str) -> str:
    """Return `reply` with every recognised marker (and its payload) removed.

    Used to compute what the user actually sees when Hive's reply contains
    a control marker. Both the gateway WS path and the Discord bot must use
    this to avoid leaking `[GENERATE_IMAGE] ...` into the visible chat.
    """
    return _STRIP_RE.sub("", reply).strip()


def _read_balanced_json(text: str) -> int | None:
    """Return the index just past a balanced `{...}` JSON object at index 0.

    Tracks brace depth and ignores braces inside strings. Returns None when
    no balanced object can be found before end-of-text.
    """
    if not text or text[0] != "{":
        return None
    depth = 0
    in_str = False
    escape = False
    for i, c in enumerate(text):
        if in_str:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i + 1
    return None


def _extract_one(text: str, key: str) -> tuple[str, tuple[int, int]] | None:
    """Find marker `key` in `text` and return (payload_str, (start, end)).

    Payload is matched as either a balanced JSON object (when it starts with
    '{', possibly multi-line) or a single line of free text. Returns None if
    the marker is missing or the payload is empty.
    """
    rx = _MARKER_HEADER_RE[key]
    m = rx.search(text)
    if not m:
        return None
    after_start = m.end()
    # Skip any inline whitespace (but not newlines) between the tag and payload.
    i = after_start
    while i < len(text) and text[i] in " \t":
        i += 1
    if i < len(text) and text[i] == "{":
        end_offset = _read_balanced_json(text[i:])
        if end_offset is None:
            return None
        payload = text[i : i + end_offset].strip()
        return payload, (m.start(), i + end_offset)
    # Free-text payload: read to end of line.
    nl = text.find("\n", i)
    end = len(text) if nl == -1 else nl
    payload = text[i:end].strip()
    if not payload:
        return None
    return payload, (m.start(), end)


def _parse_ask_user_payload(payload: str) -> AskUserHit | None:
    """Two accepted forms:

    Plain text:  [ASK_USER] What hair colour?
    JSON:        [ASK_USER] {"question":"...","options":["a","b","c"]}

    Returns None when both `question` ends up empty.
    """
    payload = payload.strip()
    if not payload:
        return None
    if payload.startswith("{"):
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            log.info("[ASK_USER] JSON parse failed; falling back to plain text")
            return AskUserHit(question=payload[:500])
        if not isinstance(obj, dict):
            return AskUserHit(question=payload[:500])
        question = str(obj.get("question", "")).strip()[:500]
        if not question:
            return None
        raw_opts = obj.get("options")
        options: list[str] = []
        if isinstance(raw_opts, list):
            for o in raw_opts:
                s = str(o).strip()
                if not s or len(s) > 80:
                    continue
                options.append(s)
                if len(options) >= 8:        # cap chip count
                    break
        return AskUserHit(question=question, options=options)
    return AskUserHit(question=payload[:500])


def parse_remember(payload: str) -> dict | None:
    """Validate a [REMEMBER] payload. Returns kwargs for VaultClient.learn or None."""
    payload = payload.strip()
    if not payload.startswith("{"):
        return None
    try:
        obj = json.loads(payload)
    except json.JSONDecodeError as e:
        log.warning("[REMEMBER] payload JSON parse failed: %s", e)
        return None
    if not isinstance(obj, dict):
        return None

    category = str(obj.get("category", "")).strip()
    title    = str(obj.get("title", "")).strip()
    body     = str(obj.get("body", "")).strip()
    if not (category and title and body):
        log.info("[REMEMBER] missing required fields (category/title/body)")
        return None
    if len(title) > 200 or len(body) > 32 * 1024:
        log.info("[REMEMBER] field too long")
        return None

    audience: list[str] = []
    raw_audience = obj.get("audience")
    if isinstance(raw_audience, list):
        audience = [str(x).strip() for x in raw_audience if str(x).strip()]
    if not audience:
        # Sensible default: Hive can read it back, claude-code can audit it,
        # nothing is broadcast to other bots without an explicit list.
        audience = ["hive", "claude-code"]

    tags: list[str] = []
    raw_tags = obj.get("tags")
    if isinstance(raw_tags, list):
        tags = [str(x).strip() for x in raw_tags if str(x).strip()]

    # M4.2: optional source provenance.
    sources: list[dict] = []
    raw_sources = obj.get("sources")
    if isinstance(raw_sources, list):
        for s in raw_sources:
            if not isinstance(s, dict):
                continue
            url = str(s.get("url", "")).strip()
            if not url or len(url) > 1000:
                continue
            entry = {"url": url}
            title_v = s.get("title")
            if isinstance(title_v, str) and title_v.strip():
                entry["title"] = title_v.strip()[:240]
            accessed = s.get("accessed")
            if isinstance(accessed, str) and accessed.strip():
                entry["accessed"] = accessed.strip()[:64]
            sources.append(entry)
            if len(sources) >= 10:
                break
    corroboration = obj.get("corroboration")
    if not isinstance(corroboration, int) or corroboration < 0:
        corroboration = None

    out: dict = {
        "category": category,
        "title": title,
        "body": body,
        "audience": audience,
        "tags": tags,
    }
    extra: dict = {}
    if sources:
        extra["sources"] = sources
    if corroboration is not None:
        extra["corroboration"] = corroboration
    if extra:
        out["extra"] = extra
    return out


def scan(reply: str) -> MarkerHits:
    """Find every marker in `reply` and return parsed/validated payloads.

    For markers that take JSON, this delegates to the appropriate validator.
    For free-text markers (ASK_USER, VAULT_LOOKUP) the raw text is returned.
    Markers found multiple times: only the FIRST is honored. Hive should
    emit at most one structural marker per turn anyway.
    """
    hits = MarkerHits()

    gi = _extract_one(reply, "generate_image")
    if gi is not None:
        payload_text, span = gi
        parsed = parse_image_payload(payload_text)
        if parsed is not None:
            hits.generate_image = parsed
            hits.raw_spans.append(span)

    ci = _extract_one(reply, "confirm_image")
    if ci is not None:
        payload_text, span = ci
        parsed = parse_image_payload(payload_text)
        if parsed is not None:
            hits.confirm_image = parsed
            hits.raw_spans.append(span)

    au = _extract_one(reply, "ask_user")
    if au is not None:
        text, span = au
        hits.ask_user = _parse_ask_user_payload(text)
        if hits.ask_user is not None:
            hits.raw_spans.append(span)

    rm = _extract_one(reply, "remember")
    if rm is not None:
        payload_text, span = rm
        validated = parse_remember(payload_text)
        if validated is not None:
            hits.remember = validated
            hits.raw_spans.append(span)

    vl = _extract_one(reply, "vault_lookup")
    if vl is not None:
        text, span = vl
        hits.vault_lookup = text[:300]
        hits.raw_spans.append(span)

    ff = _extract_one(reply, "fix_fact")
    if ff is not None:
        payload_text, span = ff
        validated = _parse_fix_fact_payload(payload_text)
        if validated is not None:
            hits.fix_fact = validated
            hits.raw_spans.append(span)

    wl = _extract_one(reply, "web_lookup")
    if wl is not None:
        text, span = wl
        hits.web_lookup = text[:300]
        hits.raw_spans.append(span)

    cs = _extract_one(reply, "create_skill")
    if cs is not None:
        payload_text, span = cs
        validated = parse_create_skill(payload_text)
        if validated is not None:
            hits.create_skill = validated
            hits.raw_spans.append(span)

    # Lenient fallback — Hive sometimes drops the `[ASK_USER]` /
    # `[CONFIRM_IMAGE]` prefix and emits raw JSON. If we see a balanced
    # JSON object at the start of the reply (and we haven't already
    # matched a structural marker above), treat it as an implicit marker
    # so the user still gets chip buttons / confirm card instead of raw
    # JSON dumped in chat.
    if (
        hits.ask_user is None
        and hits.confirm_image is None
        and hits.generate_image is None
        and reply.lstrip().startswith("{")
    ):
        offset = len(reply) - len(reply.lstrip())
        json_end = _read_balanced_json(reply[offset:])
        if json_end is not None:
            payload_text = reply[offset : offset + json_end].strip()
            try:
                obj = json.loads(payload_text)
            except json.JSONDecodeError:
                obj = None
            if isinstance(obj, dict):
                if "question" in obj and "options" in obj:
                    parsed = _parse_ask_user_payload(payload_text)
                    if parsed is not None:
                        hits.ask_user = parsed
                        hits.raw_spans.append((offset, offset + json_end))
                elif "prompt" in obj:
                    parsed = parse_image_payload(payload_text)
                    if parsed is not None:
                        hits.confirm_image = parsed
                        hits.raw_spans.append((offset, offset + json_end))

    return hits


def _parse_fix_fact_payload(payload: str) -> FixFactHit | None:
    """Validate a [FIX_FACT] payload — must be JSON with `path` + `correction`."""
    payload = payload.strip()
    if not payload.startswith("{"):
        return None
    try:
        obj = json.loads(payload)
    except json.JSONDecodeError as e:
        log.warning("[FIX_FACT] JSON parse failed: %s", e)
        return None
    if not isinstance(obj, dict):
        return None
    path = str(obj.get("path", "")).strip()
    correction = str(obj.get("correction", "")).strip()
    mode = str(obj.get("mode", "append")).strip().lower()
    if not (path and correction) or mode not in ("append", "replace"):
        return None
    if ".." in path or path.startswith("/") or path.startswith("\\"):
        return None
    if not path.endswith(".md"):
        return None
    if len(correction) > 16 * 1024:
        return None
    return FixFactHit(path=path, correction=correction, mode=mode)


def confirmation_yes(text: str) -> bool:
    """Loose match for 'user confirmed the proposed image payload'."""
    t = text.strip().lower()
    if not t:
        return False
    yes_tokens = {"yes", "y", "yeah", "yep", "go", "send it", "do it",
                  "confirm", "looks good", "lgtm", "ship it", "👍"}
    if t in yes_tokens:
        return True
    # Short prefixes like "yes please", "go for it"
    return any(t.startswith(token) for token in yes_tokens if len(token) >= 2)


def confirmation_no(text: str) -> bool:
    """Loose match for 'user cancelled the proposed image payload'."""
    t = text.strip().lower()
    if not t:
        return False
    no_tokens = {"no", "n", "cancel", "nope", "nah", "stop", "abort", "wait"}
    if t in no_tokens:
        return True
    return any(t.startswith(token) for token in no_tokens if len(token) >= 2)
