"""Repo map — a token-budgeted symbol index of a project's Python.

Why: a small local model wastes turns re-listing the file tree and
re-reading files to find where a class/function lives. A compact
`path → [class/def signatures]` summary in the loop preamble gives it
strategic context cheaply (Aider's repo-map idea, stdlib `ast` instead
of tree-sitter so there's no extra dependency).

Two entry points:
  - `build_repo_map(root, token_budget)` → rendered summary string,
    signatures only, bodies elided, capped to a rough token budget.
  - `find_symbol(root, name)` → list of {path, line, signature} for a
    class/def whose name matches (exact, then substring).

Pure stdlib, no mutation, best-effort: a file that fails to parse is
skipped (it's likely the broken file the model is mid-edit on).
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

# Dirs we never descend into — noise or huge.
_SKIP_DIRS = frozenset({
    ".git", ".crew-worktrees", "__pycache__", ".venv", "venv",
    "node_modules", ".mypy_cache", ".pytest_cache", "build", "dist",
    ".idea", ".vscode",
})
# Rough chars-per-token for the budget cap (English/code ≈ 4).
_CHARS_PER_TOKEN = 4
_MAX_FILES = 400


@dataclass(frozen=True)
class Symbol:
    path: str          # POSIX-relative to root
    line: int
    signature: str     # e.g. "def foo(a, b=1)" or "class Bar(Base)"
    kind: str          # "def" | "async def" | "class"


def _iter_py_files(root: Path):
    """Yield project-relative *.py paths, skipping noise dirs and any
    path that resolves OUTSIDE the project root (symlink-escape guard —
    a symlinked dir/file must not let the repo map read arbitrary files
    like ~/.ssh)."""
    root_resolved = root.resolve()
    count = 0
    for p in sorted(root.rglob("*.py")):
        if any(part in _SKIP_DIRS for part in p.relative_to(root).parts):
            continue
        # Refuse symlinked files and anything whose real path escapes root.
        if p.is_symlink():
            continue
        try:
            p.resolve().relative_to(root_resolved)
        except (ValueError, OSError):
            continue
        yield p
        count += 1
        if count >= _MAX_FILES:
            return


def _format_args(node: ast.arguments) -> str:
    """Render a function arg list (names + simple defaults markers)."""
    parts: list[str] = []
    posonly = getattr(node, "posonlyargs", [])
    args = list(posonly) + list(node.args)
    n_defaults = len(node.defaults)
    first_default = len(args) - n_defaults
    for i, a in enumerate(args):
        parts.append(a.arg + ("=…" if i >= first_default else ""))
    if posonly:
        parts.insert(len(posonly), "/")
    if node.vararg:
        parts.append("*" + node.vararg.arg)
    elif node.kwonlyargs:
        parts.append("*")
    for a, d in zip(node.kwonlyargs, node.kw_defaults):
        parts.append(a.arg + ("=…" if d is not None else ""))
    if node.kwarg:
        parts.append("**" + node.kwarg.arg)
    return ", ".join(parts)


def _signature(node: ast.AST) -> tuple[str, str] | None:
    """Return (kind, signature) for a top-level def/class, else None."""
    if isinstance(node, ast.AsyncFunctionDef):
        return "async def", f"async def {node.name}({_format_args(node.args)})"
    if isinstance(node, ast.FunctionDef):
        return "def", f"def {node.name}({_format_args(node.args)})"
    if isinstance(node, ast.ClassDef):
        bases = ", ".join(
            b.id for b in node.bases if isinstance(b, ast.Name)
        )
        head = f"class {node.name}" + (f"({bases})" if bases else "")
        return "class", head
    return None


# Mtime-keyed parse cache: {resolved_path_str: (mtime, [Symbol])}. After
# a single-file edit, build_repo_map / find_symbol re-parse only that one
# file instead of the whole tree (churn-bound, not size-bound). Bounded
# so a long-lived process can't leak.
_SYMBOL_CACHE: dict[str, tuple[float, list["Symbol"]]] = {}
_SYMBOL_CACHE_MAX = 2000


def _symbols_in_file(root: Path, path: Path) -> list[Symbol]:
    """Top-level + one-level-nested (methods) signatures for a file.
    Cached by mtime so an unchanged file is never re-parsed."""
    key = str(path)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return []
    cached = _SYMBOL_CACHE.get(key)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, ValueError, OSError):
        # Cache the empty result too — a broken/mid-edit file shouldn't be
        # re-parsed every turn until its mtime changes.
        _SYMBOL_CACHE[key] = (mtime, [])
        return []
    rel = path.relative_to(root).as_posix()
    out: list[Symbol] = []
    for node in tree.body:
        sig = _signature(node)
        if sig is None:
            continue
        kind, text = sig
        out.append(Symbol(rel, getattr(node, "lineno", 0), text, kind))
        if isinstance(node, ast.ClassDef):
            for sub in node.body:
                subsig = _signature(sub)
                if subsig is None:
                    continue
                skind, stext = subsig
                out.append(
                    Symbol(rel, getattr(sub, "lineno", 0),
                           "    " + stext, skind)
                )
    # Cache by mtime; evict oldest if the cache grows past the cap.
    if len(_SYMBOL_CACHE) >= _SYMBOL_CACHE_MAX:
        _SYMBOL_CACHE.pop(next(iter(_SYMBOL_CACHE)), None)
    _SYMBOL_CACHE[key] = (mtime, out)
    return out


def build_repo_map(root: Path, *, token_budget: int = 1500) -> str:
    """Render a compact signatures-only map of the project's Python,
    grouped by file, capped to roughly `token_budget` tokens."""
    root = Path(root)
    if not root.is_dir():
        return ""
    char_budget = max(0, token_budget) * _CHARS_PER_TOKEN
    lines: list[str] = []
    used = 0
    truncated = False
    for path in _iter_py_files(root):
        syms = _symbols_in_file(root, path)
        if not syms:
            continue
        block = [syms[0].path]
        block.extend(f"  {s.signature}" for s in syms)
        chunk = "\n".join(block)
        if used + len(chunk) + 1 > char_budget and lines:
            truncated = True
            break
        lines.append(chunk)
        used += len(chunk) + 1
    if not lines:
        return ""
    header = "REPO MAP (signatures only; use read_file for bodies):"
    body = "\n".join(lines)
    if truncated:
        body += "\n… (map truncated to fit budget — use find_symbol/read_file)"
    return f"{header}\n{body}"


def find_symbol(root: Path, name: str, *, limit: int = 10) -> list[dict]:
    """Locate a class/def by name across the project. Exact matches
    first, then substring. Returns [{path, line, signature}]."""
    root = Path(root)
    name = (name or "").strip()
    if not name or not root.is_dir():
        return []
    exact: list[Symbol] = []
    partial: list[Symbol] = []
    for path in _iter_py_files(root):
        for s in _symbols_in_file(root, path):
            # Symbol name = first token after def/class, sans indent.
            head = s.signature.strip()
            sym_name = head.split("(")[0].split()[-1]
            if sym_name == name:
                exact.append(s)
            elif name in sym_name:
                partial.append(s)
    chosen = (exact + partial)[:limit]
    return [
        {"path": s.path, "line": s.line, "signature": s.signature.strip()}
        for s in chosen
    ]
