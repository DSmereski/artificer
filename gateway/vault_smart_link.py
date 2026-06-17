"""Auto-rewrite vault notes so proper nouns become Obsidian [[wikilinks]].

Runs once at gateway startup. Reads every `*.md` under `canon/` and
`knowledge/`, builds a registry of every note's title from filename
(`canon/wow-sylvanas.md` → `Sylvanas`), then walks each note's body
and rewrites the first occurrence of a known target as `[[Target]]`
when it isn't already linked. Idempotent — re-running on a linked
note leaves it alone.

Skips:
  - text inside fenced code blocks
  - existing wikilinks (`[[Already linked]]`)
  - frontmatter (between `---` lines at the top)
  - the note's own self-mention (don't link "Sylvanas" inside
    `wow-sylvanas.md`)
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

log = logging.getLogger("gateway.vault_smart_link")


_FRONTMATTER_RE = re.compile(r"^---\n.*?\n---\n", re.DOTALL)
_FENCE_RE = re.compile(r"```[\s\S]*?```")
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
_JSON_ARRAY_RE = re.compile(r"\[[^\[\]\n]*\"[^\[\]\n]*\][^\[\]\n]*\]?", re.MULTILINE)
_EXISTING_LINK_RE = re.compile(r"\[\[[^\]]+\]\]")

# Files that document the marker syntax itself MUST NOT be auto-linked —
# wikilinks inside the JSON examples confuse Terry's LLM, which then copies
# `["you ([[Terry]])"]`-style garbage into her own outputs.
_FILENAME_DENYLIST = (
    "terry-imagegen",
    "imagegen-",      # imagegen-vocab, imagegen-loras, etc.
)


def _is_denied(stem: str) -> bool:
    return any(stem.startswith(p) for p in _FILENAME_DENYLIST)


def _slug_to_title(stem: str) -> str:
    """Turn 'wow-night-elf' or 'wow-sylvanas' into 'Sylvanas' / 'Night Elf'."""
    parts = stem.split("-")
    # Drop common topic-prefix tokens.
    while parts and parts[0] in {"wow", "imagegen", "star", "citizen"}:
        parts.pop(0)
    if not parts:
        return ""
    return " ".join(p.capitalize() for p in parts)


_FRONTMATTER_BLOCK_RE = re.compile(
    r'^---\s*\n(.*?)\n---\s*\n', re.DOTALL,
)
_FRONTMATTER_TITLE_KEY_RE = re.compile(
    r'^title:\s*"?([^"\n]+?)"?\s*$', re.MULTILINE,
)


def _title_from_frontmatter(text: str) -> str | None:
    """Extract the explicit `title:` field from the YAML frontmatter.
    Returns None when no frontmatter or no title key is present."""
    block = _FRONTMATTER_BLOCK_RE.match(text)
    if not block:
        return None
    m = _FRONTMATTER_TITLE_KEY_RE.search(block.group(1))
    if not m:
        return None
    title = m.group(1).strip()
    # Unescape common YAML escapes that show up in our frontmatter
    # (`—` for em-dash, `–` for en-dash). The daemon writes
    # these escaped; the linked text in bodies uses the literal char.
    try:
        title = title.encode("utf-8").decode("unicode_escape")
    except UnicodeDecodeError:
        pass
    return title or None


def build_registry(vault_path: Path) -> dict[str, str]:
    """Return {title → relative path} for canon + knowledge notes.

    Prefers the explicit frontmatter `title:` field over a derived
    slug-to-title because slug derivation lowercases everything and
    breaks mixed-case proper nouns (ArcCorp, microTech, etc.).
    """
    out: dict[str, str] = {}
    for top in ("canon", "knowledge"):
        base = vault_path / top
        if not base.is_dir():
            continue
        for p in base.rglob("*.md"):
            title: str | None = None
            try:
                head = p.read_text(encoding="utf-8", errors="ignore")[:4000]
                title = _title_from_frontmatter(head)
            except OSError:
                title = None
            if not title:
                title = _slug_to_title(p.stem)
            # Only register names with at least 4 chars + a capital — avoids
            # garbage matches like "the" or "a".
            if len(title) >= 4 and any(c.isupper() for c in title):
                out.setdefault(title, p.relative_to(vault_path).as_posix())
    return out


def link_text(text: str, registry: dict[str, str], skip_self: str | None) -> str:
    """Rewrite the FIRST occurrence of each known title as a wikilink.

    Only the first occurrence is touched — we don't want a sea of
    [[links]] all referring to the same note.
    """
    # Strip frontmatter from the head; replace later.
    fm_match = _FRONTMATTER_RE.match(text)
    fm = fm_match.group(0) if fm_match else ""
    body = text[len(fm):]

    # Mask the things we don't want to auto-link inside:
    #   - fenced code blocks (``` ... ```)
    #   - inline backticks (`code`)
    #   - JSON-looking arrays on a single line (option chip examples)
    # Replace each masked range with a placeholder, do the rewrite, then
    # restore. Order matters: fences first (greediest), then inline,
    # then arrays.
    masks: list[str] = []
    def _stash(m: re.Match) -> str:
        masks.append(m.group(0))
        return f"\x00MASK{len(masks)-1}\x00"
    masked = _FENCE_RE.sub(_stash, body)
    masked = _INLINE_CODE_RE.sub(_stash, masked)
    masked = _JSON_ARRAY_RE.sub(_stash, masked)

    for title, _path in registry.items():
        if skip_self and title == skip_self:
            continue
        # Does it already exist as a link? Skip.
        if f"[[{title}]]" in masked or f"[[{title}|" in masked:
            continue
        # Find the first standalone occurrence of `title` (word-bounded,
        # case-sensitive — proper nouns matter).
        pattern = re.compile(rf"(?<!\[)\b{re.escape(title)}\b(?!\])")
        masked, n = pattern.subn(f"[[{title}]]", masked, count=1)
        if n:
            log.debug("linked %r in note", title)

    # Restore masks.
    for i, m in enumerate(masks):
        masked = masked.replace(f"\x00MASK{i}\x00", m)
    return fm + masked


def run(vault_path: Path) -> int:
    """Smart-link every canon + knowledge note in `vault_path`.

    Returns the number of notes modified. Best-effort — never raises.
    """
    try:
        registry = build_registry(vault_path)
    except Exception as e:  # noqa: BLE001
        log.warning("smart-link: registry build failed: %s", e)
        return 0
    if not registry:
        return 0
    log.info("smart-link: registry has %d entries", len(registry))

    modified = 0
    for top in ("canon", "knowledge"):
        base = vault_path / top
        if not base.is_dir():
            continue
        for p in base.rglob("*.md"):
            if _is_denied(p.stem):
                continue
            try:
                old = p.read_text(encoding="utf-8")
            except OSError:
                continue
            self_title = _slug_to_title(p.stem)
            new = link_text(old, registry, skip_self=self_title)
            if new != old:
                try:
                    p.write_text(new, encoding="utf-8")
                    modified += 1
                except OSError as e:
                    log.warning("smart-link: write failed for %s: %s", p, e)
    log.info("smart-link: modified %d notes", modified)
    return modified
