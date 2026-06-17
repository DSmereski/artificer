"""Read-only-ish view of saved Civitai image recipes in the vault.

Recipes live as markdown notes under `<vault>/references/` with
`type: image-recipe` in their frontmatter. They're written by the
image-recipe path in `asset_importer.py` and read back by the
`/v1/recipes` REST routes.

We treat the vault file as the source of truth (no separate DB) so
hand-edits + git history Just Work.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from vault_writer.util import parse_frontmatter


log = logging.getLogger("gateway.recipe_store")


_RECIPE_FILE_RE = re.compile(
    r"^civitai-image-(\d+)\.md$",
    re.IGNORECASE,
)


@dataclass
class Recipe:
    image_id: int
    source_url: str
    path: Path
    sampler: str | None = None
    steps: int | None = None
    cfg: float | None = None
    seed: int | None = None
    positive: str = ""
    negative: str = ""
    triggered_imports: list[str] = field(default_factory=list)
    kind: str = "still"          # "still" | "video"
    body: str = ""

    def to_json(self) -> dict:
        return {
            "image_id": self.image_id,
            "source_url": self.source_url,
            "path": str(self.path),
            "sampler": self.sampler,
            "steps": self.steps,
            "cfg": self.cfg,
            "seed": self.seed,
            "positive": self.positive,
            "negative": self.negative,
            "triggered_imports": list(self.triggered_imports),
            "kind": self.kind,
        }


class RecipeStore:
    """Glob-and-parse view of recipe notes. Cheap to call; rebuilds on
    every list() so manual vault edits show up without a restart."""

    def __init__(self, vault_path: Path) -> None:
        self._vault = vault_path
        self._dir = vault_path / "references"

    def _read_one(self, path: Path) -> Recipe | None:
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as e:
            log.warning("recipe %s unreadable: %s", path, e)
            return None
        try:
            fm, body = parse_frontmatter(raw)
        except Exception:  # noqa: BLE001
            log.warning("recipe %s frontmatter parse failed", path)
            return None
        if not isinstance(fm, dict):
            return None

        image_id = fm.get("image_id")
        if image_id is None:
            # Try to lift from filename.
            m = _RECIPE_FILE_RE.match(path.name)
            if not m:
                return None
            image_id = int(m.group(1))
        try:
            image_id = int(image_id)
        except (TypeError, ValueError):
            return None

        kind_raw = str(fm.get("recipe_kind") or fm.get("kind") or "still").lower()
        kind = "video" if kind_raw == "video" else "still"
        return Recipe(
            image_id=image_id,
            source_url=str(fm.get("source_url") or ""),
            path=path,
            sampler=str(fm.get("sampler")) if fm.get("sampler") else None,
            steps=int(fm["steps"]) if isinstance(fm.get("steps"), int) else None,
            cfg=float(fm["cfg"]) if isinstance(fm.get("cfg"), (int, float)) else None,
            seed=int(fm["seed"]) if isinstance(fm.get("seed"), int) else None,
            positive=str(fm.get("positive") or ""),
            negative=str(fm.get("negative") or ""),
            triggered_imports=[
                str(x) for x in (fm.get("triggered_imports") or [])
                if isinstance(x, str)
            ],
            kind=kind,
            body=body,
        )

    def list(self) -> list[Recipe]:
        if not self._dir.is_dir():
            return []
        out: list[Recipe] = []
        for p in sorted(self._dir.glob("civitai-image-*.md")):
            r = self._read_one(p)
            if r is not None:
                out.append(r)
        # Newest first by image_id (rough proxy for creation order).
        out.sort(key=lambda r: r.image_id, reverse=True)
        return out

    def get(self, image_id: int) -> Recipe | None:
        path = self._dir / f"civitai-image-{image_id}.md"
        if not path.is_file():
            return None
        return self._read_one(path)

    def delete(self, image_id: int) -> bool:
        path = self._dir / f"civitai-image-{image_id}.md"
        if not path.is_file():
            return False
        try:
            path.unlink()
            return True
        except OSError as e:
            log.warning("recipe delete failed for %s: %s", image_id, e)
            return False
