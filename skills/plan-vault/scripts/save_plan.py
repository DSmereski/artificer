#!/usr/bin/env python3
"""Upsert a plan file into the Ai-Team vault so plans are archived + updatable.

Usage:
  python save_plan.py --plan <path-to-plan.md> --project <name> [--title "<one line>"]

Stable filename per project (slug) → re-running UPDATES the same vault file
(no duplicates) and refreshes the INDEX. The vault is a git-tracked dir, so
history is preserved across updates.
"""
import argparse
import datetime
import pathlib
import re

VAULT = pathlib.Path.home() / "Ai-Team-Vault" / "plans"


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "plan"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True, help="path to the plan .md")
    ap.add_argument("--project", required=True, help="project name (groups the plan)")
    ap.add_argument("--title", default="", help="one-line description for the index")
    args = ap.parse_args()

    src = pathlib.Path(args.plan)
    body = src.read_text(encoding="utf-8")
    date = datetime.date.today().isoformat()
    title = args.title.strip()
    if not title:
        for line in body.splitlines():
            line = line.strip()
            if line:
                title = line.lstrip("# ").strip()
                break

    VAULT.mkdir(parents=True, exist_ok=True)
    fn = VAULT / f"{slug(args.project)}.md"
    header = f"<!-- project: {args.project} | updated: {date} | source: {src} -->\n\n"
    fn.write_text(header + body, encoding="utf-8")

    # Upsert the index line for this plan.
    index = VAULT / "INDEX.md"
    line = f"- [{args.project}]({fn.name}) — {title} — updated {date}"
    lines = []
    if index.exists():
        lines = [l for l in index.read_text(encoding="utf-8").splitlines()
                 if not l.startswith(f"- [{args.project}](")]
    if not lines or lines[0] != "# Plans index":
        lines = ["# Plans index", ""] + [l for l in lines if l not in ("# Plans index", "")]
    lines.append(line)
    index.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"saved: {fn}")
    print(f"index: {index}")


if __name__ == "__main__":
    main()
