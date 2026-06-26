"""Proactive vault lookup for chat turns.

When a user message looks like it might benefit from vault context (it's
non-trivial, contains a proper noun or topic word), the gateway runs a
quick semantic search of the vault for the calling bot's audience plus a
lookup of the requesting user's `people/<user>.md` if it exists, and
prepends the matches to the LLM context for that turn.

Originally `image_research` — only fired on image cue words like "draw" /
"picture" — which left text-only questions ("what's a Drake?") without
vault grounding and Hive/Maggy free to hallucinate. The broader cue set
plus a length threshold keeps short pleasantries cheap while real
questions get the lookup.

Lazy-imports `httpx` and ollama embeddings so the module is light to load.
Never raises — a failed lookup just means no extra context this turn.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from vault_writer.util import audience_matches, coerce_audience, parse_frontmatter, wrap_untrusted

log = logging.getLogger("gateway.image_research")


_OLLAMA_URL = "http://localhost:11434"
_EMBED_MODEL = "nomic-embed-text"


# Image-specific cues — kept for backwards compat / explicit routing.
_IMAGE_CUES = re.compile(
    r"\b("
    r"image|picture|photo|photograph|portrait|selfie|render|paint|painting|"
    r"illustrate|illustration|draw|drawing|art|sketch|wallpaper|"
    r"show me|let me see"
    r")\b",
    re.IGNORECASE,
)


# Filler / chitchat patterns we DON'T want to spend an embedding on.
_TRIVIAL_RE = re.compile(
    r"^[\s\W]*("
    r"hi|hello|hey|sup|yo|hola|"
    r"yes|yeah|yep|y|no|nope|nah|n|"
    r"ok|okay|cool|nice|sure|thanks|thx|ty|"
    r"go|stop|cancel|"
    r"\?+|\!+|\.+"
    r")[\s\W]*$",
    re.IGNORECASE,
)


def looks_like_image_request(user_text: str) -> bool:
    """Kept for callers that want explicit image-vs-text routing."""
    if not user_text:
        return False
    return bool(_IMAGE_CUES.search(user_text))


def needs_vault_context(user_text: str) -> bool:
    """Should this turn pay for a vault search?

    Yes when the message has any real substance: more than a few words,
    not a one-token confirmation, and contains at least one alpha word.
    No for greetings, yes/no, single-word reactions.
    """
    if not user_text:
        return False
    stripped = user_text.strip()
    if len(stripped) < 6:                  # "hi", "yes" etc.
        return False
    if _TRIVIAL_RE.match(stripped):
        return False
    if not re.search(r"[A-Za-z]", stripped):  # pure punctuation / numbers
        return False
    return True


async def _embed(text: str) -> list[float] | None:
    """Embed `text` via local Ollama. None on any failure."""
    from shared.embeddings import embed_text
    return await embed_text(
        text, ollama_url=_OLLAMA_URL, model=_EMBED_MODEL, timeout=10.0,
    )


def _slugify_user(name: str) -> str:
    """Mirror the conventions the vault uses for `people/*.md` filenames."""
    name = (name or "").strip().lower()
    name = re.sub(r"[^a-z0-9._-]+", "-", name)
    name = name.strip("-")
    return name or "unknown"


def _read_people_note(vault_path: Path, user_name: str, agent: str) -> tuple[str, str] | None:
    """If `people/<user>.md` exists and is visible to `agent`, return (relpath, body)."""
    if not user_name:
        return None
    candidate = vault_path / "people" / f"{_slugify_user(user_name)}.md"
    if not candidate.is_file():
        return None
    try:
        raw = candidate.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    fm, body = parse_frontmatter(raw)
    audience = coerce_audience(fm.get("audience"))
    if not audience_matches(agent, audience):
        return None
    rel = candidate.relative_to(vault_path).as_posix()
    return (rel, body.strip())


async def gather_chat_context(
    *,
    user_text: str,
    user_name: str,
    vault_path: Path,
    daemon_host: str,
    daemon_port: int,
    agent: str = "hive",
    k: int = 2,
    max_chars: int = 1800,
    require_image_cue: bool = False,
) -> str:
    """Return a wrap_untrusted block of relevant vault notes for this turn.

    `require_image_cue=True` matches the legacy behaviour (image-only
    triggers). Default False = any non-trivial chat turn gets a lookup,
    which lets Hive/Maggy ground answers in `knowledge/` notes
    (Star Citizen lore, WoW lore, LoRA catalog, etc.) without users
    having to phrase their question as an image request.

    Empty string when the lookup yields nothing (or fails). Caller can splice
    the returned text into the LLM context for one turn — it shouldn't
    persist across turns since it's request-specific.
    """
    if require_image_cue:
        if not looks_like_image_request(user_text):
            return ""
    elif not needs_vault_context(user_text):
        return ""

    notes: list[tuple[str, str]] = []
    seen_paths: set[str] = set()

    # 1) Per-user appearance/preferences.
    user_note = _read_people_note(vault_path, user_name, agent)
    if user_note is not None:
        notes.append(user_note)
        seen_paths.add(user_note[0])

    # 2) Semantic search for relevant canon (lore, vocab, locations, …).
    vec = await _embed(user_text)
    if vec is None:
        return _format_block(notes, max_chars=max_chars)

    try:
        from shared.vault_client import VaultClient
        client = VaultClient(
            vault_path=vault_path,
            daemon_host=daemon_host,
            daemon_port=daemon_port,
        )
        # Slight overscan because we may dedupe against people/ already added.
        hits = client.search(query_embedding=vec, k=max(k + 1, k * 2), audience=agent)
    except Exception as e:  # noqa: BLE001
        log.info("vault semantic search failed: %s", e)
        hits = []

    for h in hits:
        if len(notes) >= k + (1 if user_note else 0):
            break
        if h.path in seen_paths:
            continue
        seen_paths.add(h.path)
        notes.append((h.path, (h.body or "").strip()))

    return _format_block(notes, max_chars=max_chars)


def _format_block(notes: list[tuple[str, str]], *, max_chars: int) -> str:
    if not notes:
        return ""
    parts: list[str] = []
    spent = 0
    for rel, body in notes:
        chunk = f"<!-- {rel} -->\n{body}".strip()
        # Don't let one bloated note crowd everything else out.
        if len(chunk) > max_chars // 2:
            chunk = chunk[: max_chars // 2] + "\n... (truncated)"
        if spent + len(chunk) > max_chars:
            break
        parts.append(chunk)
        spent += len(chunk) + 2  # for the join newlines
    if not parts:
        return ""
    return wrap_untrusted("\n\n".join(parts), source="vault-image-research")


def text_or_empty(value: Any) -> str:
    """Coerce to a printable string; used by the chat route after gather()."""
    return value if isinstance(value, str) else ""
