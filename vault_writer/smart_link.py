"""smart_link — wire loose Obsidian notes into a knowledge graph.

Two tiers:
  * REAL notes (knowledge/projects/tools/prompts/loras/canon/wiki/brand/
    briefs/people, minus crew-lessons): folder-hub MOC + a "Related" block
    of the top-N most-similar notes (3*sharedTags + sharedTitleTokens).
  * AUX notes (tasks/ops/skills + knowledge/crew-lessons-*): HUB-ONLY so the
    auto-generated piles stop showing as loose dots — no dense similarity
    cross-links.  Exception: ops/groomer/dup_scanner/A__B reports link
    straight to [[A]] and [[B]] (cheap, meaningful pair links).

Every edit lives inside a delimited managed block, so re-runs update in place
and never duplicate.  ``run_link(apply=False)`` is a dry run; ``apply=True``
writes; ``unlink=True`` strips every block + deletes generated hubs.

Designed to be both a CLI (via scripts/smart_link_vault.py) and importable by
the vault_writer daemon's periodic loop — idempotent, so a steady-state pass
writes nothing (no git churn).
"""
from __future__ import annotations

import os
import re
from collections import defaultdict
from pathlib import Path

_DEBUG = bool(os.environ.get("SMARTLINK_DEBUG"))

# --- config -----------------------------------------------------------------
REAL_DIRS = ["knowledge", "projects", "tools", "prompts", "loras",
             "canon", "wiki", "brand", "briefs", "people"]
AUX_DIRS = ["tasks", "ops", "skills"]
CREW_RE = re.compile(r"^crew-lessons-.*\.md$", re.I)
DUP_DIR = "groomer/dup_scanner"
EXISTING_HUB = {"loras": "INDEX.md", "wiki": "index.md"}
TOP_N = 6
MIN_SCORE = 2
TAG_WEIGHT = 3
DF_MAX_RATIO = 0.60
START = "<!-- smart-links:start -->"
END = "<!-- smart-links:end -->"
GEN_MARK = "smart-links-generated"

STOPWORDS = set("""
a an the and or of for to in on at by with from into over under is are be was were
this that these those it its as if then than so but not no yes you your our we they
note notes md ai claude code hive vault doc docs new old v1 v2 v3 use using used how
what when where why who which about more most some any all each via per task tasks
2024 2025 2026 jan feb mar apr may jun jul aug sep oct nov dec
""".split())
TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(*parts: str) -> set[str]:
    toks: set[str] = set()
    for p in parts:
        for t in TOKEN_RE.findall((p or "").lower()):
            if len(t) >= 3 and t not in STOPWORDS and not t.isdigit():
                toks.add(t)
    return toks


def parse_frontmatter(text: str):
    """Minimal block-YAML frontmatter reader. Returns (meta, body_start)."""
    if not text.startswith("---"):
        return {}, 0
    end = text.find("\n---", 3)
    if end == -1:
        return {}, 0
    block = text[3:end].strip("\n")
    body_start = text.find("\n", end + 1)
    body_start = body_start + 1 if body_start != -1 else len(text)
    meta: dict = {}
    key = None
    for line in block.splitlines():
        if not line.strip():
            continue
        m = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if m:
            key = m.group(1).lower()
            val = m.group(2).strip()
            if val and val not in ("[]", "''", '""'):
                if val.startswith("[") and val.endswith("]"):
                    meta[key] = [v.strip().strip("'\"")
                                 for v in val[1:-1].split(",") if v.strip()]
                else:
                    meta[key] = val.strip().strip("'\"")
            else:
                meta[key] = []
        elif re.match(r"^\s*-\s+", line) and key is not None:
            item = re.sub(r"^\s*-\s+", "", line).strip().strip("'\"")
            meta.setdefault(key, [])
            if isinstance(meta[key], list):
                meta[key].append(item)
    return meta, body_start


def _as_list(v):
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def _first_h1(body: str):
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return None


def pretty(name: str) -> str:
    return re.sub(r"[-_]+", " ", name).strip().title()


class Note:
    def __init__(self, path: Path, vault: Path, tier: str):
        self.path = path
        self.rel = path.relative_to(vault).as_posix()
        self.folder = self.rel.split("/", 1)[0]
        self.basename = path.stem
        self.tier = tier  # 'real' | 'aux'
        raw = path.read_text(encoding="utf-8", errors="replace")
        self.raw = raw
        meta, bstart = parse_frontmatter(raw)
        self.meta = meta
        self.body = raw[bstart:]
        title = meta.get("title")
        if isinstance(title, list):
            title = title[0] if title else None
        self.title = (title or _first_h1(self.body) or pretty(self.basename)).strip()
        self.tags = set(t.lower() for t in _as_list(meta.get("tags")) if t)
        self.tokens = tokenize(self.title, self.basename) | self.tags
        self.related: list[Note] = []
        self.is_dup = (DUP_DIR in self.rel) and ("__" in self.basename)


def _is_generated(path: Path) -> bool:
    if path.stem.startswith("_MOC-"):
        return True
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:400]
    except OSError:
        return False
    return f"generator: {GEN_MARK}" in head


def strip_managed(text: str) -> str:
    i = text.find(START)
    if i == -1:
        return text
    j = text.find(END, i)
    if j == -1:
        return text[:i].rstrip() + "\n"
    return (text[:i] + text[j + len(END):]).rstrip() + "\n"


def collect(vault: Path) -> list[Note]:
    notes: list[Note] = []
    # reused-hub files (loras/INDEX.md, wiki/index.md) are HUBS, not spokes —
    # never collect them as targets or they oscillate (hub block vs related block).
    hub_files = {(vault / folder / name).resolve()
                 for folder, name in EXISTING_HUB.items()}
    for d in REAL_DIRS:
        base = vault / d
        if not base.is_dir():
            continue
        for p in base.rglob("*.md"):
            if CREW_RE.match(p.name) or _is_generated(p) or p.resolve() in hub_files:
                continue
            notes.append(Note(p, vault, "real"))
    for d in AUX_DIRS:
        base = vault / d
        if not base.is_dir():
            continue
        for p in base.rglob("*.md"):
            if _is_generated(p):
                continue
            notes.append(Note(p, vault, "aux"))
    # crew-lessons live under knowledge/ but are aux (regenerated)
    kdir = vault / "knowledge"
    if kdir.is_dir():
        for p in kdir.rglob("crew-lessons-*.md"):
            if not _is_generated(p):
                notes.append(Note(p, vault, "aux"))
    return notes


def run_link(vault, apply: bool = False, unlink: bool = False,
             quiet: bool = False) -> dict:
    vault = Path(vault)
    if not vault.is_dir():
        raise FileNotFoundError(f"vault not found: {vault}")

    def say(*a):
        if not quiet:
            print(*a)

    targets = collect(vault)

    # ---- UNLINK -------------------------------------------------------------
    if unlink:
        stripped = gens = 0
        for n in targets:
            if START in n.raw:
                new = strip_managed(n.raw)
                if new != n.raw:
                    n.path.write_text(new, encoding="utf-8")
                    stripped += 1
        for d in REAL_DIRS + AUX_DIRS + ["."]:
            base = vault / d
            if not base.is_dir():
                continue
            for p in base.glob("*.md"):
                try:
                    t = p.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                if f"generator: {GEN_MARK}" in t:
                    p.unlink()
                    gens += 1
        say(f"UNLINK: stripped {stripped} blocks, deleted {gens} hubs/INDEX.")
        return {"stripped": stripped, "hubs_deleted": gens}

    # ---- global basename index (collision + pair resolution) ----------------
    all_basenames: dict[str, int] = defaultdict(int)
    rel_by_basename: dict[str, str] = {}
    for p in vault.rglob("*.md"):
        all_basenames[p.stem] += 1
        rel_by_basename.setdefault(p.stem, p.relative_to(vault).as_posix())

    def link_to(basename: str, title: str | None = None) -> str | None:
        if all_basenames.get(basename, 0) == 0:
            return None
        if all_basenames[basename] > 1:
            rel = rel_by_basename.get(basename, basename + ".md")[:-3]
            return f"[[{rel}|{title or basename}]]"
        return f"[[{basename}]]"

    def note_link(n: Note) -> str:
        return link_to(n.basename, n.title) or f"[[{n.basename}]]"

    # ---- similarity (real x real only) -------------------------------------
    real = [n for n in targets if n.tier == "real"]
    df: dict[str, int] = defaultdict(int)
    for n in real:
        for t in n.tokens:
            df[t] += 1
    cutoff = max(2, int(len(real) * DF_MAX_RATIO))
    generic = {t for t, c in df.items() if c > cutoff}
    for n in real:
        n.eff = (n.tokens - generic) | n.tags
    for n in real:
        scored = []
        for m in real:
            if m is n:
                continue
            st = n.tags & m.tags
            sk = (n.eff & m.eff) - n.tags
            score = TAG_WEIGHT * len(st) + len(sk)
            if score >= MIN_SCORE:
                scored.append((score, m.basename, m))
        scored.sort(key=lambda x: (-x[0], x[1]))
        n.related = [m for _, _, m in scored[:TOP_N]]

    # ---- hub mapping --------------------------------------------------------
    def hub_path(folder: str) -> tuple[Path, bool]:
        if folder in EXISTING_HUB:
            return vault / folder / EXISTING_HUB[folder], True
        return vault / folder / f"_MOC-{folder}.md", False

    def hub_link(folder: str) -> str:
        hp, pre = hub_path(folder)
        if pre:  # loras/INDEX, wiki/index -> path form (collides with root INDEX)
            return f"[[{hp.relative_to(vault).as_posix()[:-3]}|{folder}]]"
        return f"[[{hp.stem}]]"

    # include folders that have targets, plus any reused-hub folder that exists
    # even with no spokes (its hub still links up to INDEX, so it isn't loose).
    folders = sorted({n.folder for n in targets} |
                     {f for f, name in EXISTING_HUB.items()
                      if (vault / f / name).exists()})
    spokes: dict[str, list[Note]] = defaultdict(list)
    for n in targets:
        spokes[n.folder].append(n)

    # ---- per-note managed blocks -------------------------------------------
    note_edits = []
    links_added = 0
    for n in targets:
        lines = [START, "## Related", "", f"_Hub:_ {hub_link(n.folder)}", ""]
        bullets: list[str] = []
        if n.tier == "real":
            bullets = [f"- {note_link(r)}" for r in n.related]
        elif n.is_dup:  # dup_scanner A__B -> link both sides
            for part in n.basename.split("__"):
                lk = link_to(part)
                if lk:
                    bullets.append(f"- {lk}")
        if bullets:
            lines += bullets
            links_added += len(bullets)
        else:
            lines.append("_Linked to hub._")
        lines += ["", END, ""]
        new = strip_managed(n.raw).rstrip() + "\n\n" + "\n".join(lines)
        if new != n.raw:
            note_edits.append((n.path, new))
            if _DEBUG:
                print(f"  [edit] {n.rel}")

    # ---- hub MOCs -----------------------------------------------------------
    hub_edits = []
    for folder in folders:
        hp, pre = hub_path(folder)
        items = sorted(spokes[folder], key=lambda x: x.title.lower())
        body = [START]
        if not pre:
            body.append(f"_generator: {GEN_MARK}_")
        body += [f"## {pretty(folder)} — map of content", "",
                 "_Up:_ [[INDEX]]", "", f"{len(items)} notes:", ""]
        body += [f"- {note_link(it)}" for it in items]
        body += ["", END, ""]
        block = "\n".join(body)
        if pre and hp.exists():
            ex = hp.read_text(encoding="utf-8", errors="replace")
            new = strip_managed(ex).rstrip() + "\n\n" + block
        else:
            new = (f"---\ntitle: {pretty(folder)} MOC\n"
                   f"generator: {GEN_MARK}\ntags:\n- moc\n---\n\n") + block
        hub_edits.append((hp, new))

    # ---- root INDEX ---------------------------------------------------------
    idx_path = vault / "INDEX.md"
    idx = ["## Vault map of content", "",
           "Top-level hubs (auto-linked by smart_link):", ""]
    for folder in folders:
        idx.append(f"- {hub_link(folder)} — {len(spokes[folder])} notes")
    idx_block = "\n".join([START, f"_generator: {GEN_MARK}_", ""] + idx + ["", END, ""])
    if idx_path.exists():
        ex = idx_path.read_text(encoding="utf-8", errors="replace")
        idx_text = strip_managed(ex).rstrip() + "\n\n" + idx_block
    else:
        idx_text = (f"---\ntitle: Vault INDEX\ngenerator: {GEN_MARK}\n"
                    f"tags:\n- moc\n---\n\n") + idx_block

    stats = {
        "targets": len(targets), "real": len(real),
        "aux": len(targets) - len(real),
        "notes_to_edit": len(note_edits), "links_added": links_added,
        "hubs": len(hub_edits), "folders": folders,
        "spokes": {f: len(spokes[f]) for f in folders},
    }

    say("=" * 60)
    say("SMART-LINK  " + ("APPLY" if apply else "DRY RUN"))
    say("=" * 60)
    say(f"vault         : {vault}")
    say(f"targets       : {stats['targets']}  (real {stats['real']}, "
        f"aux {stats['aux']})")
    say(f"notes to edit : {stats['notes_to_edit']}")
    say(f"links added   : {stats['links_added']}")
    say(f"hubs          : {stats['hubs']}  + root INDEX")
    for f in folders:
        say(f"    - {f:<12} {stats['spokes'][f]:>4} notes")

    if not apply:
        say("DRY RUN — nothing written.")
        return stats

    for path, txt in note_edits:
        path.write_text(txt, encoding="utf-8")
    for path, txt in hub_edits:
        path.write_text(txt, encoding="utf-8")
    idx_path.write_text(idx_text, encoding="utf-8")
    say(f"APPLIED: {len(note_edits)} notes, {len(hub_edits)} hubs, INDEX.")
    return stats
