"""EV1 — Evolution analyzer: "what's the next valuable work for a done project?".

Weighs cheap signals into a ranked list of candidate next-goals:
  1. repo gap + quality  — the code map (repomap) + a TODO/FIXME scan.
  2. pending / vaulted    — the project's Pending.md + unchecked vault-plan items.
  3. product-critic       — an LLM "what would make this genuinely better" pass.

A single hive-qwen synthesis call blends them into ranked Candidates, each tagged
with the source signal(s) so the UI can show WHY it was picked. The heavier
competitive-research signal is added in EV4. Pure + injector-injectable so the
synthesis is unit-testable offline.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from gateway.crew_board.repomap import build_repo_map

log = logging.getLogger("gateway.crew_board.evolve")

_VAULT_PLANS = Path(os.path.expanduser("~")) / "Ai-Team-Vault" / "plans"
_TODO_RE = re.compile(r"\b(TODO|FIXME|HACK|XXX)\b", re.IGNORECASE)
_UNCHECKED_RE = re.compile(r"^\s*[-*]\s*\[\s\]\s*(.+)$")
_CODE_EXT = (".py", ".ts", ".tsx", ".js", ".jsx", ".dart", ".cs", ".go", ".rs",
             ".java", ".kt", ".gd", ".vue", ".svelte", ".c", ".cpp", ".h")
_SKIP_DIRS = {".git", "node_modules", "__pycache__", "dist", "build", ".venv",
              "venv", ".dart_tool", "target", "bin", "obj", ".next"}

# Valid source tags. "competitive" (EV4) = features comparable/competing products
# commonly offer that this project lacks, from the model's product knowledge. A
# heavier LIVE web rival-research signal (via the competitive-feature-analysis
# skill, fenced under prompt-injection-defense) is a future toggle — it needs web
# tooling the gateway doesn't host, so it runs Claude-side, not here.
SOURCES = ("repo-gap", "pending", "product-idea", "competitive")


@dataclass
class Candidate:
    """One ranked next-work idea for a project."""
    title: str
    body: str
    rationale: str
    source: list[str]
    score: float
    checklist: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "title": self.title, "body": self.body, "rationale": self.rationale,
            "source": self.source, "score": self.score, "checklist": self.checklist,
        }


# --------------------------------------------------------------------------- #
#  Signal 1 — repo gap + quality                                               #
# --------------------------------------------------------------------------- #

def _todo_scan(root: Path, *, max_hits: int = 40, max_files: int = 600) -> list[str]:
    """Collect TODO/FIXME/HACK markers as 'relpath:line: text', bounded."""
    hits: list[str] = []
    seen = 0
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
            for fn in filenames:
                if not fn.endswith(_CODE_EXT):
                    continue
                seen += 1
                if seen > max_files:
                    return hits
                fp = Path(dirpath) / fn
                try:
                    with fp.open("r", encoding="utf-8", errors="ignore") as fh:
                        for i, line in enumerate(fh, 1):
                            if _TODO_RE.search(line):
                                rel = fp.relative_to(root).as_posix()
                                hits.append(f"{rel}:{i}: {line.strip()[:120]}")
                                if len(hits) >= max_hits:
                                    return hits
                except OSError:
                    continue
    except OSError:
        pass
    return hits


# Marker file -> human stack label. First match wins. Language-AGNOSTIC so the
# analyzer grounds on Flutter/Godot/C#/Node/etc., not just Python (the Python-only
# repomap left a Dart app looking empty -> the LLM hallucinated a greenfield web
# app and proposed re-scaffolding an already-shipped project).
_STACK_MARKERS = (
    ("pubspec.yaml", "Flutter / Dart"),
    ("project.godot", "Godot / GDScript"),
    ("Cargo.toml", "Rust"),
    ("go.mod", "Go"),
    ("package.json", "Node / TypeScript"),
    ("pyproject.toml", "Python"),
    ("setup.py", "Python"),
    ("requirements.txt", "Python"),
    ("build.gradle", "Android / Gradle (JVM)"),
)


def _detect_stack(root: Path) -> str:
    for marker, label in _STACK_MARKERS:
        if (root / marker).is_file():
            return label
    # .csproj / .sln anywhere near the top → .NET.
    try:
        if any(p.suffix in (".csproj", ".sln") for p in root.iterdir()):
            return ".NET / C#"
    except OSError:
        pass
    return "unknown"


def _file_tree(root: Path, *, max_entries: int = 90) -> str:
    """Bounded list of source files + key config, so the LLM sees what ALREADY
    exists (and proposes enhancements, not a from-scratch rebuild)."""
    keep_names = {"pubspec.yaml", "package.json", "Cargo.toml", "go.mod",
                  "project.godot", "pyproject.toml", "README.md"}
    rels: list[str] = []
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
            for fn in sorted(filenames):
                if fn.endswith(_CODE_EXT) or fn in keep_names:
                    rels.append((Path(dirpath) / fn).relative_to(root).as_posix())
                    if len(rels) >= max_entries:
                        rels.append("... (truncated)")
                        return "\n".join(rels)
    except OSError:
        pass
    return "\n".join(rels) or "(empty repo)"


def _readme_head(root: Path, *, max_lines: int = 16) -> str:
    for name in ("README.md", "readme.md", "README.txt"):
        p = root / name
        try:
            if p.is_file():
                lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
                return "\n".join(lines[:max_lines]).strip()
        except OSError:
            continue
    return ""


def _repo_signal(root: Path) -> str:
    """Language-agnostic repo-gap context: detected stack + what exists (file
    tree + README) + open code markers + (for Python) the symbol map."""
    parts: list[str] = [f"Detected stack: {_detect_stack(root)}"]
    readme = _readme_head(root)
    if readme:
        parts.append("README:\n" + readme)
    parts.append("Existing files (what's ALREADY built):\n" + _file_tree(root))
    todos = _todo_scan(root)
    if todos:
        parts.append("Open code markers (TODO/FIXME/HACK):\n" + "\n".join(todos))
    # Python symbol map is a useful bonus when applicable; harmless otherwise.
    if (root / "pyproject.toml").is_file() or (root / "setup.py").is_file():
        try:
            parts.append("Python symbols:\n" + build_repo_map(root, token_budget=1200))
        except Exception as e:  # noqa: BLE001
            log.debug("evolve: repo map failed for %s: %s", root, e)
    return "\n\n".join(p for p in parts if p)


# --------------------------------------------------------------------------- #
#  Signal 2 — pending / vaulted backlog                                        #
# --------------------------------------------------------------------------- #

def _unchecked_items(text: str, *, limit: int = 40) -> list[str]:
    out: list[str] = []
    for line in text.splitlines():
        m = _UNCHECKED_RE.match(line)
        if m:
            out.append(m.group(1).strip()[:160])
            if len(out) >= limit:
                break
    return out


def _pending_signal(slug: str, root: Path) -> str:
    """The project's explicit backlog: Pending.md + unchecked vault-plan items."""
    items: list[str] = []
    pending = root / "Pending.md"
    try:
        if pending.is_file():
            items += _unchecked_items(pending.read_text(encoding="utf-8", errors="ignore"))
    except OSError:
        pass
    plan = _VAULT_PLANS / f"{slug}.md"
    try:
        if plan.is_file():
            items += _unchecked_items(plan.read_text(encoding="utf-8", errors="ignore"))
    except OSError:
        pass
    if not items:
        return "(no explicit backlog found)"
    # De-dupe preserving order.
    seen: set[str] = set()
    uniq = [i for i in items if not (i in seen or seen.add(i))]
    return "\n".join(f"- {i}" for i in uniq[:40])


# --------------------------------------------------------------------------- #
#  Synthesis                                                                    #
# --------------------------------------------------------------------------- #

_CANDIDATES_SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                    "rationale": {"type": "string"},
                    "source": {"type": "array", "items": {"type": "string", "enum": list(SOURCES)}},
                    "score": {"type": "number"},
                    "checklist": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["title", "body", "rationale", "source", "score", "checklist"],
            },
        },
    },
    "required": ["candidates"],
}

_ANALYZE_SYSTEM = """You are a senior engineer AND product lead choosing the NEXT
most valuable work for an EXISTING, shipped project. You are given the project's
code map + open code markers (repo-gap signal), its explicit backlog (pending
signal), and you may also propose net-new improvements (product-idea signal).

Output 3-6 candidate next-goals, each one concrete and independently shippable.
Include at least one COMPETITIVE candidate when the project is a user-facing app
or game: a feature that comparable/competing products commonly have but this one
lacks (judge from the stack + what the files reveal it is).
- title: short imperative (e.g. "Add undo/redo to the board editor").
- body: one focused paragraph of what to build and why it matters.
- rationale: one sentence on the VALUE — why this is the best next step.
- source: array of which signals motivated it (subset of repo-gap, pending,
  product-idea, competitive). repo-gap = code-quality/missing-piece work; pending
  = backlog items; product-idea = net-new improvements; competitive = "rivals
  have this, we don't" (name the kind of product you're comparing to in the body).
- checklist: 2-5 concrete, machine-testable "done" statements for the WHOLE goal.
- score: 0.0-1.0 value/effort estimate; rank the highest-leverage work highest.

Prefer real, grounded work over vague polish. Do NOT invent files or features
that contradict the code map. Respond with JSON only."""


async def analyze_next(store, slug: str, *, invoker=None, max_candidates: int = 6) -> list[Candidate]:
    """Return ranked next-work Candidates for *slug*, or [] if the project is
    unknown / the analysis fails. *invoker* (anything with an async ``chat``) is
    injectable for tests; defaults to a live ``OllamaInvoker``."""
    proj = store.get_project(slug)
    if proj is None:
        return []
    root = Path(proj.path)

    repo_ctx = _repo_signal(root)
    pending_ctx = _pending_signal(slug, root)

    user = (
        f"Project: {getattr(proj, 'name', slug)} (slug: {slug})\n\n"
        f"=== Code map + open markers (repo-gap) ===\n{repo_ctx}\n\n"
        f"=== Explicit backlog (pending) ===\n{pending_ctx}\n\n"
        f"Propose the ranked next-work candidates as JSON."
    )

    if invoker is None:
        from gateway.helpers.base import OllamaInvoker
        invoker = OllamaInvoker()

    try:
        from gateway.helpers.base import extract_json
        text, _, _ = await invoker.chat(
            model="hive-qwen", system=_ANALYZE_SYSTEM, user=user,
            params={"temperature": 0.4, "num_ctx": 8192, "num_predict": 2048},
            fmt=_CANDIDATES_SCHEMA,
        )
        data = extract_json(text)
    except Exception as e:  # noqa: BLE001
        log.warning("evolve: analyze_next failed for %s: %s", slug, e)
        return []

    raw = (data or {}).get("candidates") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return []

    out: list[Candidate] = []
    for c in raw:
        if not isinstance(c, dict) or not str(c.get("title", "")).strip():
            continue
        srcs = [s for s in (c.get("source") or []) if s in SOURCES] or ["product-idea"]
        try:
            score = float(c.get("score", 0.0))
        except (TypeError, ValueError):
            score = 0.0
        out.append(Candidate(
            title=str(c["title"]).strip()[:140],
            body=str(c.get("body", "")).strip(),
            rationale=str(c.get("rationale", "")).strip(),
            source=srcs,
            score=max(0.0, min(1.0, score)),
            checklist=[str(x).strip() for x in (c.get("checklist") or []) if str(x).strip()][:6],
        ))

    out.sort(key=lambda c: c.score, reverse=True)
    return out[:max_candidates]
